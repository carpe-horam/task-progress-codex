"""Read-only product review; business data is confined to TemporaryDirectory."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "plugins/task-progress/server.py"

with tempfile.TemporaryDirectory(prefix="task-progress-peer-review-") as isolated:
    spec = importlib.util.spec_from_file_location("peer_review_server", SERVER)
    server = importlib.util.module_from_spec(spec)
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)
    server.SESSION_DIR = Path(isolated) / "sessions"
    server.SESSION_DIR.mkdir()
    def rpc(name, **args):
        response = json.loads(server.handle({"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": name, "arguments": args}}))
        assert "error" not in response, response
        return response["result"]
    initial = rpc("init_task_network", task_title="Unstarted open bypass", tasks=[
        {"id": "worker", "title": "Delegated producer", "assignment": {"execution_mode": "subagent"}},
        {"id": "consumer", "title": "End-sensitive consumer", "depends_on": ["worker"],
         "dependency_policy": "finished", "assignment": {"execution_mode": "main_session"}}])
    progress = initial["structuredContent"]["progress"]
    binding = {"session_id": progress["session_id"], "task_instance_id": progress["task_instance_id"]}
    original_bytes = server._session_file(binding["session_id"]).read_bytes()
    outcome = rpc("update_task", **binding, task_id="worker", execution_status="finished",
        outcome="open", summary="Unstarted producer claims unfinished output", evidence=["fixture://unstarted"])
    current = rpc("get_progress", session_id=binding["session_id"])["structuredContent"]["progress"]
    worker = next((n for n in current.get("nodes", []) if n["id"] == "worker"), {})
    print(json.dumps({"server_sha256": hashlib.sha256(SERVER.read_bytes()).hexdigest(),
        "case": "pending planned-subagent leaf -> finished/open without attempt",
        "is_error": outcome.get("isError", False), "message": outcome["content"][0]["text"],
        "session_bytes_unchanged": server._session_file(binding["session_id"]).read_bytes() == original_bytes,
        "state_revision_unchanged": current["state_revision"] == progress["state_revision"],
        "worker": {key: worker.get(key) for key in ("execution_status", "outcome", "attempt_id", "assignment", "planned_assignment", "review_status", "submission")},
        "ready_tasks": current.get("ready_tasks")}, ensure_ascii=False, indent=2))
