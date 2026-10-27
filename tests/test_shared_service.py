"""Real HTTP/process regressions; all progress and daemon state are temporary."""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"


class SharedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "service"
        self.state.mkdir(mode=0o700)
        self.authorization = "Bearer " + secrets.token_urlsafe(32)
        credentials = self.state / "http-headers.json"
        credentials.write_text(json.dumps({"Authorization": self.authorization}))
        credentials.chmod(0o600)
        self.proc = None
        self.addCleanup(self.stop)
        self.start()

    def command(self, *, state=None, port=0):
        return [sys.executable, "-B", str(PLUGIN / "shared_service.py"), "--port", str(port),
                "--state-dir", str(state or self.state), "--session-dir", str(self.root / "sessions")]

    def start(self, *, port=0):
        self.proc = subprocess.Popen(self.command(port=port), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if self.proc.poll() is not None:
                self.fail("Service exited: " + self.proc.stderr.read())
            path = self.state / "daemon.json"
            if path.exists():
                metadata = json.loads(path.read_text())
                # A crash leaves the predecessor's metadata until the new
                # process acquires its lock and binds the listening socket.
                if metadata["pid"] == self.proc.pid:
                    self.metadata = metadata
                    self.port = metadata["port"]
                    return
            time.sleep(0.02)
        self.fail("Service startup timed out")

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
            try:
                self.proc.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.communicate()
                raise AssertionError("Shared service did not shut down normally")
            self.proc = None

    def http(self, method="POST", path="/mcp", payload=None, *, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        base = {"Authorization": self.authorization, "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        base.update(headers or {})
        try:
            body = raw if raw is not None else json.dumps(payload) if payload is not None else None
            connection.request(method, path, body=body, headers=base)
            response = connection.getresponse(); data = response.read()
            return response.status, dict(response.getheaders()), json.loads(data) if data else None
        finally:
            connection.close()

    def rpc(self, name, arguments, *, request_id=1):
        status, headers, message = self.http(payload={"jsonrpc": "2.0", "id": request_id,
            "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        self.assertEqual(status, 200, message)
        self.assertEqual(message["id"], request_id)
        self.assertNotIn("error", message)
        self.assertFalse(message["result"].get("isError"), message)
        self.assertEqual(int(headers["X-Task-Progress-Pid"]), self.proc.pid)
        return message["result"]["structuredContent"]

    def test_multiple_clients_share_one_pid_without_mixing_request_ids_or_sessions(self):
        def create(index):
            # Independent clients deliberately reuse JSON-RPC ID 1.
            result = self.rpc("init_task_network", {"task_title": f"Client {index}",
                "atomic_reason": "One operation", "request_id": f"client-{index}"})
            sid = result["session_id"]; instance = result["progress"]["task_instance_id"]
            run = self.rpc("update_task", {"session_id": sid, "task_instance_id": instance,
                "task_id": "root", "execution_status": "running", "assignment": {"execution_mode": "main_session"}})
            attempt = run["progress"]["nodes"][0]["attempt_id"]
            self.rpc("update_task", {"session_id": sid, "task_instance_id": instance, "task_id": "root",
                "execution_status": "finished", "outcome": "resolved", "attempt_id": attempt,
                "summary": f"Result {index}"})
            final = self.rpc("get_progress", {"session_id": sid})["progress"]
            self.assertEqual(final["task_title"], f"Client {index}")
            self.assertEqual(final["nodes"][0]["summary"], f"Result {index}")
            return sid
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            sessions = list(pool.map(create, range(12)))
        self.assertEqual(len(set(sessions)), 12)
        status, _, health = self.http("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(health["pid"], self.proc.pid)
        self.assertEqual(health["request_count"], 48)

    def test_concurrent_repeated_init_is_idempotent(self):
        def initialize(_):
            return self.rpc("init_task_network", {"task_title": "One shared request", "request_id": "one-key",
                        "atomic_reason": "One operation"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(initialize, range(8)))
        self.assertEqual(len({r["session_id"] for r in results}), 1)
        self.assertEqual(len({r["progress"]["task_instance_id"] for r in results}), 1)

    def test_streamable_handshake_notifications_and_resource_version(self):
        status, headers, message = self.http(payload={"jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"}})
        version = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())["version"]
        self.assertEqual(status, 200)
        self.assertEqual(message["result"]["serverInfo"]["version"], version)
        self.assertNotIn("Mcp-Session-Id", headers)
        self.assertEqual(self.http(payload={"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)
        status, _, message = self.http(payload={"jsonrpc": "2.0", "id": 2, "method": "resources/read",
            "params": {"uri": "ui://task-progress/progress-inspector/v3"}})
        self.assertEqual(status, 200)
        html = message["result"]["contents"][0]["text"]
        self.assertIn(f"version: '{version}'", html)
        self.assertNotIn("__TASK_PROGRESS_VERSION__", html)
        self.assertEqual(self.http("GET", "/mcp")[0], 405)
        self.assertEqual(self.http("DELETE", "/mcp")[0], 405)

    def test_credentials_host_and_origin_are_checked_before_writes(self):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "init_task_network", "arguments": {"task_title": "Must not exist", "atomic_reason": "test"}}}
        for headers, expected in [({"Authorization": "wrong"}, 401), ({"Authorization": "Bearer café"}, 401),
                                  ({"Origin": "https://untrusted.example"}, 403), ({"Origin": "null"}, 403),
                                  ({"Host": "untrusted.example"}, 403)]:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.http(payload=payload, headers=headers)[0], expected)
        self.assertFalse(list((self.root / "sessions").glob("*.json")))
        self.assertEqual(self.http("OPTIONS", "/mcp")[0], 403)

    def test_bad_requests_do_not_break_next_client(self):
        self.assertEqual(self.http(raw="not-json")[0], 400)
        self.assertEqual(self.http(payload={}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.http(payload={}, headers={"MCP-Protocol-Version": "1900-01-01"})[0], 400)
        self.assertEqual(self.http(payload={}, headers={"Accept": "text/html"})[0], 406)
        self.assertEqual(self.http(raw="", headers={"Content-Length": str(65*1024*1024)})[0], 413)
        self.assertEqual(self.http(payload={"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 200)

    def test_duplicate_daemon_is_rejected_even_on_another_port(self):
        second = subprocess.run(self.command(), capture_output=True, text=True, timeout=3)
        self.assertEqual(second.returncode, 1)
        self.assertIn("already owns", second.stderr)
        self.assertEqual(self.http("GET", "/healthz")[2]["pid"], self.proc.pid)

    def test_normal_stop_and_crash_restart_preserve_data_and_release_lock(self):
        result = self.rpc("init_task_network", {"task_title": "Survives restart", "atomic_reason": "test"})
        sid = result["session_id"]; pid = self.proc.pid; port = self.port
        self.stop()
        self.assertFalse((self.state / "daemon.json").exists())
        self.start(port=port)
        self.assertNotEqual(self.proc.pid, pid)
        self.assertEqual(self.rpc("get_progress", {"session_id": sid})["progress"]["task_title"], "Survives restart")
        self.proc.kill(); self.proc.communicate(timeout=3); self.proc = None
        self.start(port=port)
        self.assertEqual(self.rpc("get_progress", {"session_id": sid})["progress"]["task_instance_id"], result["progress"]["task_instance_id"])

    def test_client_disconnect_does_not_stop_global_service(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.connect(); connection.close()
        self.assertEqual(self.http("GET", "/healthz")[2]["pid"], self.proc.pid)

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions")
    def test_public_credentials_are_rejected(self):
        self.stop(); (self.state / "http-headers.json").chmod(0o644)
        result = subprocess.run(self.command(), capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn("0600", result.stderr)

    def test_plugin_configuration_uses_http_without_session_process_command(self):
        config = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["task_progress"]
        self.assertEqual(config["url"], "http://127.0.0.1:53421/mcp")
        self.assertIn("http-headers.json", config["http_headers_helper"])
        for key in ("command", "args", "cwd", "env_vars"):
            self.assertNotIn(key, config)


if __name__ == "__main__":
    unittest.main()
