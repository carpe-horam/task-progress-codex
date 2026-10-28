"""Regressions for pre-work planning and idempotent plan creation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("task_startup_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class TaskStartupContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-startup-")
        self.old_dir = server.SESSION_DIR
        server.SESSION_DIR = Path(self.temp.name)

    def tearDown(self):
        server.SESSION_DIR = self.old_dir
        self.temp.cleanup()

    def test_unplanned_root_is_not_ready_and_cannot_start(self):
        result = server.init_task_network("Multi-stage work")
        sid = result["structuredContent"]["session_id"]
        progress = result["structuredContent"]["progress"]
        self.assertEqual(progress["planning_status"], "required")
        self.assertEqual(progress["ready_tasks"], [])
        before = server._session_file(sid).read_bytes()
        with self.assertRaisesRegex(server.ProgressError, "planning_required"):
            server.update_task(sid, "root", "running", assignment={"execution_mode": "main_session"})
        self.assertEqual(before, server._session_file(sid).read_bytes())

    def test_initial_task_tree_is_ready_as_one_atomic_init(self):
        tasks = [
            {"id": "inspect", "title": "Inspect", "assignment": {"execution_mode": "subagent", "complexity": "complex"}},
            {"id": "implement", "title": "Implement", "depends_on": ["inspect"],
             "assignment": {"execution_mode": "subagent", "complexity": "complex"}},
        ]
        result = server.init_task_network("Multi-stage work", tasks=tasks, request_id="request-1")
        progress = result["structuredContent"]["progress"]
        self.assertEqual(progress["planning_status"], "planned")
        self.assertEqual(progress["ready_tasks"], ["inspect"])
        implement = next(node for node in progress["nodes"] if node["id"] == "implement")
        self.assertEqual(implement["planned_assignment"]["execution_mode"], "subagent")

    def test_legacy_two_call_initialization_unlocks_only_after_decomposition(self):
        result = server.init_task_network("Two-call plan")
        sid = result["structuredContent"]["session_id"]
        task_instance_id = result["structuredContent"]["progress"]["task_instance_id"]
        server.decompose_task(sid, "root", [
            {"id": "one", "title": "One", "assignment": {"execution_mode": "main_session"}},
            {"id": "two", "title": "Two", "assignment": {"execution_mode": "main_session"}},
        ], task_instance_id=task_instance_id)
        progress = server.get_progress(sid)["structuredContent"]["progress"]
        self.assertEqual(progress["planning_status"], "planned")
        self.assertEqual(progress["ready_tasks"], ["one", "two"])

    def test_atomic_task_requires_reason_and_can_start(self):
        result = server.init_task_network("Atomic work", atomic_reason="One self-contained documentation lookup")
        sid = result["structuredContent"]["session_id"]
        progress = result["structuredContent"]["progress"]
        self.assertEqual(progress["planning_status"], "atomic")
        self.assertEqual(progress["planning_note"], "One self-contained documentation lookup")
        started = server.update_task(sid, "root", "running", assignment={"execution_mode": "main_session"})
        self.assertEqual(started["structuredContent"]["progress"]["nodes"][0]["execution_status"], "running")

    def test_initialization_retry_is_idempotent_and_conflicts_are_rejected(self):
        tasks = [
            {"id": "one", "title": "One", "assignment": {"execution_mode": "main_session"}},
            {"id": "two", "title": "Two", "assignment": {"execution_mode": "main_session"}},
        ]
        result = server.init_task_network("Plan", tasks=tasks, request_id="same")
        sid = result["structuredContent"]["session_id"]
        task_id = result["structuredContent"]["progress"]["task_instance_id"]
        retried = server.init_task_network("Plan", sid, tasks=tasks, request_id="same")
        self.assertEqual(retried["structuredContent"]["progress"]["task_instance_id"], task_id)
        with self.assertRaisesRegex(server.ProgressError, "不同的初始化内容"):
            server.init_task_network("Different", sid, atomic_reason="different", request_id="same")

    def test_hook_emits_startup_and_prompt_guidance(self):
        hook = PLUGIN / "hooks" / "task_progress_hook.py"
        for event, phrase in [
            ({"hook_event_name": "SessionStart", "source": "startup"}, "coarse HTN plan"),
            ({"hook_event_name": "UserPromptSubmit", "prompt": "Please fix a bug"}, "coarse plan"),
            ({"hook_event_name": "SessionStart", "source": "resume"}, "resumed Codex session"),
        ]:
            run = subprocess.run(
                [sys.executable, str(hook)], input=json.dumps(event), text=True,
                capture_output=True, check=True,
            )
            output = json.loads(run.stdout)
            self.assertEqual(output["hookSpecificOutput"]["hookEventName"], event["hook_event_name"])
            self.assertIn(phrase, output["hookSpecificOutput"]["additionalContext"])

    def test_plugin_manifest_discovers_hooks_without_trust_override(self):
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
        events = hooks["hooks"]
        self.assertIn("SessionStart", events)
        self.assertIn("UserPromptSubmit", events)
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        serialized = json.dumps(manifest).lower()
        self.assertNotIn("trusted_hash", serialized)
        self.assertNotIn("trust", serialized)
        initialized = json.loads(server.handle({"jsonrpc": "2.0", "id": 1,
                                               "method": "initialize", "params": {}}))
        self.assertEqual(initialized["result"]["serverInfo"]["version"], manifest["version"])
        resource = json.loads(server.handle({"jsonrpc": "2.0", "id": 2,
                                            "method": "resources/read",
                                            "params": {"uri": server.RESOURCE_URI}}))
        app = resource["result"]["contents"][0]["text"]
        self.assertIn(f"version: '{manifest['version']}'", app)
        self.assertNotIn("__TASK_PROGRESS_VERSION__", app)

    def test_tool_schema_exposes_atomic_plan_and_idempotency_fields(self):
        response = json.loads(server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}))
        tool = next(item for item in response["result"]["tools"] if item["name"] == "init_task_network")
        self.assertTrue({"tasks", "atomic_reason", "request_id"}.issubset(tool["inputSchema"]["properties"]))
        self.assertNotIn("根任务默认 ready", tool["description"])


if __name__ == "__main__":
    unittest.main()
