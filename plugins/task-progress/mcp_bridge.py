#!/usr/bin/env python3
"""Start the managed shared service, then forward stdio MCP to loopback HTTP.

Only this transport adapter belongs to the Codex connection. Task state and MCP
execution remain in the single service owned by the native user service manager.
"""
from __future__ import annotations

import http.client
import json
import sys

from service_manager import PORT, ServiceError, ServiceManager
from shared_service import MAX_REQUEST_BYTES, read_authorization


def forward(message, authorization):
    # Direct HTTPConnection never reads proxy settings or follows redirects.
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
    try:
        connection.request("POST", "/mcp", json.dumps(message).encode("utf-8"), {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
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
        # install() is locked and idempotent; it also verifies authenticated health.
        # Native launchd/systemd/Task Scheduler owns the daemon, never this bridge.
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
