#!/usr/bin/env python3
"""Optional native Codex transport acceptance; creates no Codex chats or turns."""
import argparse
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def health(port, headers):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/healthz", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        return json.loads(response.read())
    finally:
        connection.close()


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=3)


def client(executable, server_name, overrides, index):
    command = [executable]
    for override in overrides:
        command.extend(["-c", override])
    process = subprocess.Popen(command + ["app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, cwd=ROOT)
    messages = queue.Queue()
    def reader():
        for line in process.stdout:
            messages.put(json.loads(line))
    threading.Thread(target=reader, daemon=True).start()
    def rpc(request_id, method, params):
        process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            response = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if response.get("id") == request_id:
                assert "error" not in response, response.get("error")
                return response["result"]
        raise TimeoutError(method)
    try:
        rpc(1, "initialize", {"clientInfo": {"name": "task_progress_http_acceptance", "version": "1.0"},
                              "capabilities": {"experimentalApi": True}})
        process.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n"); process.stdin.flush()
        status = rpc(2, "mcpServerStatus/list", {"serverName": server_name, "detail": "full"})
        # Some app-server versions return every configured server despite the
        # serverName filter. Select this server explicitly and follow pagination.
        entries = [entry for entry in status.get("data", []) if entry.get("name") == server_name]
        seen_cursors = set()
        while not entries and status.get("nextCursor"):
            cursor = status["nextCursor"]
            assert cursor not in seen_cursors, "MCP status pagination repeated a cursor"
            seen_cursors.add(cursor)
            status = rpc(10 + len(seen_cursors), "mcpServerStatus/list",
                         {"serverName": server_name, "detail": "full", "cursor": cursor})
            entries = [entry for entry in status.get("data", []) if entry.get("name") == server_name]
        assert len(entries) == 1, {"requested_server": server_name, "matching_entries": len(entries)}
        entry = entries[0]
        # app-server can report the matching server before asynchronous MCP
        # startup has populated tools. Read status only; never create a chat.
        startup_deadline = time.monotonic() + 15
        retry = 0
        while not entry.get("tools") and not entry.get("error") and time.monotonic() < startup_deadline:
            time.sleep(0.2)
            retry += 1
            status = rpc(100 + retry, "mcpServerStatus/list", {"serverName": server_name, "detail": "full"})
            matches = [item for item in status.get("data", []) if item.get("name") == server_name]
            if matches:
                entry = matches[0]
        tools = entry.get("tools", {})
        names = list(tools) if isinstance(tools, dict) else [tool["name"] for tool in tools]
        required = {"get_progress", "get_progress_view", "reassign_task", "bind_task_agent", "review_task"}
        missing = [name for name in required if not any(discovered.endswith(name) for discovered in names)]
        assert not missing, {"server": entry.get("name"), "tool_count": len(names), "missing": missing, "error": entry.get("error")}
        discovered_tools = list(tools.values()) if isinstance(tools, dict) else tools
        specs = {tool.get("name", "").split("__")[-1]: tool for tool in discovered_tools}
        query = specs["get_progress"]
        properties = query.get("inputSchema", query.get("input_schema", {}))["properties"]
        assert properties["view"]["default"] == "overview"
        assert set(properties["view"]["enum"]) == {"overview", "tree", "tasks", "records", "context", "history", "full"}
        assert "expected_state_revision" in properties
        app_reader = specs["get_progress_view"]
        metadata = app_reader.get("_meta", app_reader.get("meta", {}))
        assert metadata["ui"]["visibility"] == ["app"], "App snapshot reader must not be model-visible"
        render = specs["render_progress"]
        assert "bootstrap" in render.get("description", "")
        for mutation in ("init_task_network", "update_task", "route_request"):
            assert "回执" in specs[mutation].get("description", "")
        resource = rpc(3, "mcpServer/resource/read", {"server": server_name, "uri": "ui://task-progress/progress-inspector/v3"})
        # App-server returns the MCP resource response directly or in contents.
        assert "__TASK_PROGRESS_VERSION__" not in json.dumps(resource)
        assert "version: '0.2.0'" in json.dumps(resource), list(resource)
        return {"client": index, "server": entry.get("name"), "tool_count": len(names), "resource_version": "0.2.0", "query_views": properties["view"]["enum"], "app_reader_visibility": ["app"], "creates_chat": False}
    finally:
        stop(process)
        process.stdin.close(); process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--installed", action="store_true", help="Read installed plugin config and existing global service")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    daemon = None
    with tempfile.TemporaryDirectory(prefix="task-progress-native-http-") as temporary:
        if args.installed:
            state = Path.home() / ".cache/task-progress/service"
            port = 53421
            overrides = []
            server_name = "task_progress"
        else:
            state = Path(temporary) / "service"; state.mkdir(mode=0o700)
            credentials = state / "http-headers.json"
            credentials.write_text(json.dumps({"Authorization": "Bearer " + secrets.token_urlsafe(48)}))
            credentials.chmod(0o600)
            daemon = subprocess.Popen([sys.executable, "-B", str(ROOT / "plugins/task-progress/shared_service.py"),
                "--port", "0", "--state-dir", str(state), "--session-dir", str(Path(temporary) / "sessions")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(250):
                if (state / "daemon.json").exists():
                    break
                assert daemon.poll() is None, "isolated daemon failed"
                time.sleep(0.02)
            port = json.loads((state / "daemon.json").read_text())["port"]
            server_name = "task_progress_http_probe"
            # Paths are passed as CLI argv TOML values; no shell interpolation.
            helper = "cat " + "'" + str(credentials).replace("'", "'\\''") + "'"
            overrides = [f'mcp_servers.{server_name}.url="http://127.0.0.1:{port}/mcp"',
                         f'mcp_servers.{server_name}.http_headers_helper={json.dumps(helper)}']
        try:
            headers = json.loads((state / "http-headers.json").read_text())
            before = health(port, headers)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                clients = list(pool.map(lambda i: client(args.codex, server_name, overrides, i), range(2)))
            after = health(port, headers)
            assert before["pid"] == after["pid"] and before["instance_id"] == after["instance_id"]
            assert after["request_count"] > before["request_count"]
            result = {"ok": True, "installed_plugin": args.installed, "codex_version": subprocess.check_output([args.codex, "--version"], text=True).strip(),
                      "clients": clients, "service_pid": after["pid"], "service_version": after["version"],
                      "requests_added": after["request_count"] - before["request_count"], "created_chats": 0}
            if args.output:
                args.output.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        finally:
            if daemon:
                stop(daemon)


if __name__ == "__main__":
    main()
