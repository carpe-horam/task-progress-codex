"""Compact payload and continuity/dispatch safety, isolated from real sessions."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("response_protocol_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as isolated:
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "One isolated verification"}
SUB = {"execution_mode": "subagent", "model": "fixture-model", "reasoning_effort": "high",
       "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Inspect a fixture and return evidence"}


def progress(result):
    return result["structuredContent"]["progress"]


class ResponseProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        server.LEGACY_DB_PATH = Path(self.temp.name) / "archive.sqlite3"
        p = progress(server.init_task_network("Goal", tasks=[
            {"id": "work", "title": "Work", "assignment": SUB},
            {"id": "main", "title": "Main", "assignment": MAIN}]))
        self.sid, self.instance = p["session_id"], p["task_instance_id"]

    def node(self):
        return next(n for n in progress(server.get_progress(self.sid))["nodes"] if n["id"] == "work")

    def start(self, assignment=None):
        server.update_task(self.sid, "work", "running", assignment=assignment or SUB)
        return self.node()["attempt_id"]

    def unchanged_error(self, callback, pattern):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaisesRegex(server.ProgressError, pattern):
            callback()
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def test_compact_default_and_mutation_preserve_full_history_on_demand(self):
        server.rebuild_task_network(self.sid, "Plan revision")
        result = server.init_task_network("Explicit next goal", self.sid,
            atomic_reason="One lookup", new_task_reason="User requests new goal; old plan remains unresolved")
        p = progress(result)
        self.assertFalse(p["history_included"])
        self.assertEqual(p["history_summary"], {"task_count": 1, "plan_count": 0, "legacy_task_count": 0})
        for key in ("task_history", "history"):
            self.assertNotIn(key, p)
            self.assertNotIn(key, progress(server.get_progress(self.sid)))
        full = progress(server.get_progress(self.sid, include_history=True))
        self.assertTrue(full["history_included"])
        self.assertEqual(len(full["task_history"][0]["history"]), 1)
        self.assertIn("remains unresolved", full["task_history"][0]["archive_reason"])
        self.assertEqual(full["task_history"][0]["status"], "pending")
        self.assertLess(len(json.dumps(p)), len(json.dumps(full)))

    def test_unfinished_goal_cannot_be_replaced_by_either_initializer_without_reason(self):
        self.unchanged_error(lambda: server.init_task_network("Round 2", self.sid), "new_task_reason")
        self.unchanged_error(lambda: server.init_progress(["New"], session_id=self.sid), "new_task_reason")
        self.unchanged_error(lambda: server.init_task_network("Round 2", self.sid, new_task_reason=" "), "new_task_reason")

    def test_explicit_new_goal_cannot_archive_running_or_pending_review_work(self):
        aid = self.start()
        self.unchanged_error(lambda: server.init_task_network("New", self.sid, new_task_reason="New user target"), "运行")
        server.bind_task_agent(self.sid, "work", aid, "/root/actual")
        server.update_task(self.sid, "work", "finished", "resolved", "Output", ["fixture://output"], aid)
        self.unchanged_error(lambda: server.init_task_network("New", self.sid, new_task_reason="New user target"), "待验收")
        p = progress(server.get_progress(self.sid))
        self.assertEqual(p["pending_review_count"], 1)
        action = next(x for x in p["next_actions"] if "review_task(work)" in x)
        for token in (aid, "decision=accept|reject", "summary", "evidence", "main_session"):
            self.assertIn(token, action)

    def test_spawn_alias_is_pending_until_actual_returned_id_is_bound(self):
        aid = self.start({**SUB, "agent_name": "planned-alias"})
        self.assertEqual(self.node()["dispatch"]["status"], "pending")
        self.assertIsNone(self.node()["dispatch"]["agent_name"])
        self.unchanged_error(lambda: server.update_task(self.sid, "work", "finished", "resolved", attempt_id=aid), "bind_task_agent")
        server.bind_task_agent(self.sid, "work", aid, "/root/actual-returned")
        self.assertEqual(self.node()["dispatch"]["agent_name"], "/root/actual-returned")

    def test_followup_requires_explicit_tool_and_known_executor(self):
        self.unchanged_error(lambda: self.start({**SUB, "tool_identifier": "collaboration.followup_task"}), "已有 agent_name")
        self.start({**SUB, "tool_identifier": "collaboration.followup_task", "agent_name": "/root/actual"})
        self.assertEqual(self.node()["dispatch"]["status"], "bound")

    def test_missing_json_with_broken_legacy_store_reports_unknown_read_only_archive(self):
        server._session_file(self.sid).unlink()
        server.LEGACY_DB_PATH.write_bytes(b"corrupt fixture database")
        before = server.LEGACY_DB_PATH.read_bytes()
        compact_result = server.get_progress(self.sid)
        compact = progress(compact_result)
        self.assertEqual(compact["mode"], "legacy_archive")
        self.assertTrue(compact["read_only"])
        self.assertEqual(compact["legacy_history_warning"], "legacy_history_unavailable")
        self.assertEqual(compact["history_summary"]["legacy_task_count"], 0)
        self.assertFalse(compact["history_included"])
        self.assertNotIn("legacy_history", compact)
        self.assertIn("均未知", compact_result["content"][0]["text"])
        full = progress(server.get_progress(self.sid, include_history=True))
        self.assertEqual(full["legacy_history"]["status"], "unavailable")
        self.assertEqual(full["legacy_history"]["records"], [])
        self.assertEqual(server.LEGACY_DB_PATH.read_bytes(), before)
        self.assertFalse(server._session_file(self.sid).exists())

    def test_legacy_failure_is_isolated_and_clear_tombstone_survives_new_init(self):
        server.LEGACY_DB_PATH.write_bytes(b"corrupt")
        before = server._session_file(self.sid).read_bytes()
        p = progress(server.get_progress(self.sid, include_history=True))
        self.assertEqual(p["legacy_history"]["status"], "unavailable")
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)
        server.clear_progress(self.sid)
        self.assertTrue(server._read_order(self.sid)["legacy_history_cleared"])
        self.assertIsNone(progress(server.get_progress(self.sid, True)))
        server.init_task_network("New after clear", self.sid)
        self.assertNotIn("legacy_history", progress(server.get_progress(self.sid, True)))


if __name__ == "__main__":
    unittest.main()
