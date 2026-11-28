#!/usr/bin/env python3
"""Start the managed shared service, then forward stdio MCP to loopback HTTP.

Only this transport adapter belongs to the Codex connection. Task state and MCP
execution remain in the single service owned by the native user service manager.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import sys

from service_manager import PORT, ServiceError, ServiceManager
from shared_service import MAX_REQUEST_BYTES, read_authorization, runtime_compatibility

CLIENT_SOURCE = os.environ.get('TASK_PROGRESS_SOURCE', 'codex')


_PLUGIN_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][A-Za-z0-9.-]+)?\Z")


def _version_family(value):
    match = _PLUGIN_VERSION.fullmatch(str(value or ""))
    return tuple(map(int, match.groups())) if match else None


def _package_version():
    root = Path(__file__).resolve().parent
    for manifest in (root / "ghost.json", root / ".codex-plugin/plugin.json"):
        try:
            version = json.loads(manifest.read_text()).get("version")
        except (OSError, ValueError, TypeError):
            continue
        if _version_family(version) is not None:
            return version
    return None


def _healthy_newer_compatible_service(manager):
    """Keep newer daemons, or an equal compatible preferred Cindy runtime."""
    try:
        health = manager.health()
    except (ServiceError, OSError, ValueError):
        return False
    if not isinstance(health, dict):
        return False
    client_version = _version_family(_package_version())
    running_version = _version_family(health.get("version"))
    if not client_version or not running_version or running_version[:2] != client_version[:2]:
        return False
    if running_version > client_version:
        return True
    # Codex/DSH must not replace Cindy's equivalent shared engine merely
    # because its manifest, UI or manager wrapper has a different release hash.
    # Same-host connections still reconcile same-version local updates.
    if (running_version == client_version and CLIENT_SOURCE in ('codex', 'dsh')
            and health.get('runtime_host') == 'cindy'):
        try:
            return health.get('runtime_compatibility') == runtime_compatibility()
        except OSError:
            return False
    return False


def forward(message, authorization):
    # Direct HTTPConnection never reads proxy settings or follows redirects.
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
    try:
        connection.request("POST", "/mcp", json.dumps(message).encode("utf-8"), {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Task-Progress-Source": CLIENT_SOURCE,
        })
        response = connection.getresponse()
        body = response.read(MAX_REQUEST_BYTES + 1)
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("Service response exceeds the bridge size limit")
        if response.status == 202 and not body:
            return None
        if response.status != 200:
            raise ValueError(f"Shared service HTTP failure ({response.status})")
        return json.loads(body)
    finally:
        connection.close()


def main():
    manager = ServiceManager()
    try:
        # Prefer the compatible Cindy shared engine across host connections.
        # Otherwise reconcile this package, including same-host new digests.
        # Native launchd/systemd/Task Scheduler owns the daemon, never this bridge.
        if not _healthy_newer_compatible_service(manager):
            manager.install()
        authorization = read_authorization(manager.state_dir)
    except (ServiceError, OSError, ValueError) as error:
        print(f"Task Progress startup: {error}", file=sys.stderr, flush=True)
        return 1

    stdin = sys.stdin.buffer
    while True:
        # readline(size) bounds allocation before the configured request limit
        # is checked; iterating over stdin first would buffer an arbitrary line.
        line = stdin.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            break
        if not line.strip():
            continue
        message = None
        try:
            if len(line) > MAX_REQUEST_BYTES:
                # Discard the rest of an oversized JSON-RPC line in bounded
                # chunks so its tail is not misinterpreted as a new message.
                while line and not line.endswith(b"\n"):
                    line = stdin.readline(MAX_REQUEST_BYTES + 1)
                raise ValueError("MCP request exceeds the bridge size limit")
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            result = {"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "Invalid JSON or oversized request."}}
        else:
            try:
                result = forward(message, authorization)
            except (OSError, ValueError, http.client.HTTPException):
                # Never retry writes: the daemon may already have applied them.
                if not isinstance(message, dict) or "id" not in message:
                    print("Task Progress: notification forwarding failed", file=sys.stderr, flush=True)
                    continue
                result = {"jsonrpc": "2.0", "id": message["id"],
                          "error": {"code": -32603, "message": "Task Progress shared service request failed."}}
        if result is not None:
            sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
