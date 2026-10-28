#!/usr/bin/env python3
"""One authenticated loopback Streamable HTTP MCP service per OS user.

The transport is stateless: JSON-RPC IDs belong to each HTTP request, while the
existing explicit session_id remains the business-data boundary. No client
connection spawns a Python child or holds a permanent worker thread.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import threading
import time
import uuid

DEFAULT_PORT = 53421
MAX_REQUEST_BYTES = 64 * 1024 * 1024  # Two base64-encoded 20 MiB exports + metadata.
HTTP_PROTOCOL_VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25"}


def read_authorization(state_dir: Path) -> str:
    path = state_dir / "http-headers.json"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("HTTP credentials must be a regular private file.")
    if os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("HTTP credentials must be owned by this user with mode 0600.")
    value = json.loads(path.read_text(encoding="utf-8")).get("Authorization")
    if not isinstance(value, str) or not value.startswith("Bearer ") or len(value[7:]) < 32:
        raise ValueError("Run service_manager.py prepare to create valid credentials.")
    return value


@contextmanager
def singleton_lock(state_dir: Path):
    """Hold an OS lock, not a stale PID check; never unlink the lock inode."""
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(state_dir / "daemon.lock", flags, 0o600)
    stream = os.fdopen(fd, "a+b")
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows only.
            import msvcrt
            stream.seek(0); stream.write(b"\0"); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("A Task Progress shared service already owns this state directory.") from None
    try:
        yield
    finally:
        stream.close()


def load_core(session_dir: Path | None = None):
    spec = importlib.util.spec_from_file_location("task_progress_shared_core", Path(__file__).with_name("server.py"))
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    if session_dir is not None:
        session_dir.mkdir(parents=True, exist_ok=True)
        core.SESSION_DIR = session_dir
        # Explicit storage is an isolation boundary for every data generation.
        # Keep the production cache/sessions + sibling SQLite layout locally.
        core.LEGACY_DB_PATH = session_dir.parent / "progress-v3.sqlite3"
    return core


class SharedMCPServer(ThreadingHTTPServer):
    # Wait for accepted writes to finish on a normal stop, bounded by socket timeout.
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, port: int, authorization: str, core, *, max_workers: int = 32):
        self.authorization = authorization
        self.core = core
        self.instance_id = str(uuid.uuid4())
        self.started_at = time.time()
        self._slots = threading.BoundedSemaphore(max_workers)
        self._counter_lock = threading.Lock()
        self._request_count = 0
        super().__init__(("127.0.0.1", port), MCPRequestHandler)
        self.timeout = 0.25

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 1\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def record_request(self):
        with self._counter_lock:
            self._request_count += 1

    def health(self):
        with self._counter_lock:
            count = self._request_count
        return {"service": "task-progress", "transport": "streamable-http",
                "version": self.core._plugin_version(), "pid": os.getpid(),
                "instance_id": self.instance_id, "runtime_path": str(Path(__file__).resolve()),
                "port": self.server_port, "uptime_seconds": round(time.time() - self.started_at, 3),
                "request_count": count}


class MCPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TaskProgressHTTP"
    sys_version = ""

    def log_message(self, *_):
        # Never log payloads, credentials or session IDs; launchd captures startup errors.
        pass

    def _reply(self, status_code: int, payload=None, *, allow: str | None = None):
        body = b"" if payload is None else (payload.encode("utf-8") if isinstance(payload, str)
                                            else json.dumps(payload).encode("utf-8"))
        self.send_response(status_code)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Task-Progress-Pid", str(os.getpid()))
        if allow:
            self.send_header("Allow", allow)
        if status_code == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="task-progress"')
        self.end_headers()
        self.close_connection = True
        if body:
            self.wfile.write(body)

    def _guard(self) -> bool:
        # A browser page cannot use the user's loopback daemon via DNS rebinding
        # or cross-origin requests. Native MCP clients do not send Origin.
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get_all("Host", []) != [expected_host] or self.headers.get_all("Origin"):
            self._reply(403, {"error": "Untrusted HTTP origin or host."})
            return False
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not hmac.compare_digest(values[0].encode("utf-8"), self.server.authorization.encode("utf-8")):
            self._reply(401, {"error": "Task Progress credentials are required."})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        if self.path == "/healthz":
            self._reply(200, self.server.health())
        elif self.path == "/mcp":
            self._reply(405, allow="POST")  # Stateless JSON responses; no SSE stream.
        else:
            self._reply(404, {"error": "Unknown endpoint."})

    def do_POST(self):
        if not self._guard():
            return
        if self.path != "/mcp":
            self._reply(404, {"error": "Unknown endpoint."}); return
        if self.headers.get_content_type() != "application/json":
            self._reply(415, {"error": "Use application/json."}); return
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept and "*/*" not in accept:
            self._reply(406, {"error": "Accept application/json responses."}); return
        version = self.headers.get("MCP-Protocol-Version")
        if version is not None and version not in HTTP_PROTOCOL_VERSIONS:
            self._reply(400, {"error": "Unsupported MCP-Protocol-Version."}); return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            self._reply(400, {"error": "A single Content-Length is required."}); return
        length = int(lengths[0])
        if length > MAX_REQUEST_BYTES:
            self._reply(413, {"error": "MCP request exceeds the local size limit."}); return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._reply(400, {"error": "Incomplete request body."}); return
            message = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self._reply(400, self.server.core._err(None, -32700, "Invalid JSON.")); return
        except (TimeoutError, socket.timeout):
            self._reply(408, {"error": "Request body timed out."}); return
        self.server.record_request()
        try:
            response = self.server.core.handle(message)
        except Exception:
            response = self.server.core._err(message.get("id") if isinstance(message, dict) else None,
                                             -32603, "Internal server error.")
        self._reply(202 if response is None else 200, response)

    def do_DELETE(self):
        if self._guard():
            self._reply(405, allow="POST" if self.path == "/mcp" else "GET")

    def do_OPTIONS(self):
        # No browser CORS permission is granted, including preflight requests.
        self._reply(403, {"error": "Browser cross-origin requests are not supported."})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".cache/task-progress/service")
    parser.add_argument("--session-dir", type=Path, help="Explicit isolated data directory for development/tests.")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    state_dir = args.state_dir.expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    try:
        authorization = read_authorization(state_dir)
        with singleton_lock(state_dir):
            core = load_core(args.session_dir.expanduser().resolve() if args.session_dir else None)
            with SharedMCPServer(args.port, authorization, core) as service:
                metadata = state_dir / "daemon.json"
                core._atomic_json(metadata, service.health())
                try:
                    print(json.dumps({"service": "task-progress", "pid": os.getpid(),
                                      "port": service.server_port, "version": core._plugin_version()}), flush=True)
                    while not stopping.is_set():
                        service.handle_request()
                finally:
                    # The lock excludes any successor until cleanup is finished.
                    metadata.unlink(missing_ok=True)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Task Progress shared service: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
