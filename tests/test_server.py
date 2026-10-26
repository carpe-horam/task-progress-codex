"""Public-operation regressions. Fixtures never use the user's session directory."""
import importlib.util
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("progress_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
ASSIGNMENT = {"execution_mode": "main_session", "model": "test-fixture", "reasoning_effort": "high"}


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-test-")
        self.previous_dir = server.SESSION_DIR
        server.SESSION_DIR = Path(self.temp.name)
        result = server.init_task_network("Regression fixture")
        self.sid = result["structuredContent"]["session_id"]

    def tearDown(self):
        server.SESSION_DIR = self.previous_dir
        self.temp.cleanup()

    def progress(self):
        return server.get_progress(self.sid)["structuredContent"]["progress"]

    def node(self, node_id):
        return next(n for n in self.progress()["nodes"] if n["id"] == node_id)

    def add(self, *tasks, parent="root", **kwargs):
        return server.decompose_task(self.sid, parent, list(tasks), **kwargs)

    def start(self, node_id, **kwargs):
        server.update_task(self.sid, node_id, "running", assignment=ASSIGNMENT, **kwargs)
        return self.node(node_id)["attempt_id"]

    def finish(self, node_id, outcome="resolved", **kwargs):
        return server.update_task(self.sid, node_id, "finished", outcome=outcome,
                                  attempt_id=self.node(node_id)["attempt_id"], assignment=ASSIGNMENT, **kwargs)

    def assertRejectedUnchanged(self, action):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            action()
        self.assertEqual(before, server._session_file(self.sid).read_bytes())

    def test_group_dependency_blocks_children_and_parent_acceptance(self):
        self.add({"id": "a", "title": "A"}, {"id": "g", "title": "G", "depends_on": ["a"]})
        self.add({"id": "child", "title": "Child"}, parent="g")
        self.assertEqual(self.progress()["ready_tasks"], ["a"])
        self.assertEqual(self.node("child")["blocked_by"], ["a"])
        self.assertRejectedUnchanged(lambda: self.start("child"))
        server.update_task(self.sid, "child", "cancelled")
        self.assertRejectedUnchanged(lambda: self.finish("g", outcome="open"))
        self.start("a"); self.finish("a", summary="Upstream result", evidence=["fixture://a"])
        self.finish("g", outcome="open")

    def test_inherited_policy_and_evidence(self):
        self.add({"id": "a", "title": "A"}, {"id": "g", "title": "G", "depends_on": ["a"], "dependency_policy": "finished"})
        self.add({"id": "h", "title": "H"}, parent="g")
        self.add({"id": "child", "title": "Child"}, parent="h")
        aid = self.start("a")
        server.update_task(self.sid, "a", "failed", attempt_id=aid, summary="Failed upstream", evidence=["fixture://failure"])
        ready = server.get_ready_tasks(self.sid)["structuredContent"]["ready_tasks"]
        self.assertEqual([n["id"] for n in ready], ["child"])
        dependency = ready[0]["dependencies"][0]
        self.assertEqual(dependency["inherited_from"], "g")
        self.assertEqual(dependency["dependency_policy"], "finished")
        self.assertEqual(dependency["evidence"], ["fixture://failure"])
        self.start("child")

    def test_stricter_ancestor_policy_cannot_be_overridden(self):
        self.add({"id": "a", "title": "A"}, {"id": "g", "title": "G", "depends_on": ["a"]})
        self.add({"id": "child", "title": "Child", "depends_on": ["a"], "dependency_policy": "finished"}, parent="g")
        self.start("a"); self.finish("a", outcome="open")
        self.assertEqual(self.node("child")["blocked_by"], ["a"])
        self.assertRejectedUnchanged(lambda: self.start("child"))

    def test_ancestor_dependency_deadlock_is_rejected(self):
        self.add({"id": "g", "title": "G"})
        self.assertRejectedUnchanged(lambda: self.add({"id": "child", "title": "Child", "depends_on": ["g"]}, parent="g"))

    def test_cross_group_completion_cycle_is_rejected(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B"})
        self.add({"id": "a1", "title": "A1", "depends_on": ["b"]}, parent="a")
        self.assertRejectedUnchanged(lambda: self.add({"id": "b1", "title": "B1", "depends_on": ["a"]}, parent="b"))

    def test_valid_nested_dependencies_are_accepted(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B", "depends_on": ["a"]})
        self.add({"id": "a1", "title": "A1"}, {"id": "a2", "title": "A2", "depends_on": ["a1"]}, parent="a")
        self.add({"id": "b1", "title": "B1"}, parent="b")
        self.assertEqual(self.progress()["ready_tasks"], ["a1"])
        for nid in ["a1", "a2"]:
            self.start(nid); self.finish(nid)
        self.finish("a")
        self.assertEqual(self.progress()["ready_tasks"], ["b1"])
        self.start("b1"); self.finish("b1"); self.finish("b"); self.finish("root")
        self.assertEqual(self.progress()["status"], "completed")

    def test_generated_root_does_not_reuse_any_historical_id(self):
        self.add({"id": "root-e1", "title": "Old task"}, {"id": "root-e1-1", "title": "Collision suffix"})
        old_attempt = self.start("root-e1")
        server.update_task(self.sid, "root-e1", "cancelled", attempt_id=old_attempt)
        old_instance = self.progress()["task_instance_id"]
        server.rebuild_task_network(self.sid, "New plan")
        self.assertNotIn(self.progress()["root_id"], {"root", "root-e1", "root-e1-1"})
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root-e1", "finished", outcome="resolved", attempt_id=old_attempt, task_instance_id=old_instance))
        self.assertRejectedUnchanged(lambda: self.add({"id": "root-e1", "title": "Reuse"}, parent=self.progress()["root_id"]))

    def test_nested_ready_status_and_cancelled_root(self):
        self.add({"id": "g", "title": "G"})
        self.add({"id": "child", "title": "Child"}, parent="g")
        self.assertEqual(self.progress()["root_status"], "decomposing")
        other = server.init_task_network("Cancel fixture")["structuredContent"]["session_id"]
        p = server.update_task(other, "root", "cancelled")["structuredContent"]["progress"]
        self.assertEqual(p["status"], "cancelled")

    def test_retry_rechecks_current_dependency(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B", "depends_on": ["a"]})
        for nid in ["a", "b"]:
            self.start(nid); self.finish(nid)
        self.start("a", retry=True)
        self.assertRejectedUnchanged(lambda: self.start("b", retry=True))
        self.finish("a")
        self.start("b", retry=True)
        self.assertEqual(self.node("b")["attempt"], 2)

    def test_retry_reopens_all_ancestors_and_preserves_acceptances(self):
        self.add({"id": "g", "title": "G"})
        self.add({"id": "a", "title": "A"}, parent="g")
        first = self.start("a"); self.finish("a")
        self.finish("g", summary="Original acceptance", evidence=["fixture://acceptance"])
        self.finish("root")
        root_attempt = self.node("root")["attempt_id"]
        self.start("a", retry=True)
        p = self.progress()
        self.assertEqual(p["status"], "in_progress")
        self.assertEqual(p["executing_count"], 1)
        self.assertEqual(self.node("g")["execution_status"], "pending")
        self.assertIsNone(self.node("root")["outcome"])
        old = self.node("g")["invalidated_results"][0]
        self.assertEqual(old["evidence"], ["fixture://acceptance"])
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "a", "finished", outcome="resolved", attempt_id=first))
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "finished", outcome="resolved", attempt_id=root_attempt, assignment=ASSIGNMENT))
        self.finish("a"); self.finish("g"); self.finish("root")
        self.assertEqual(self.progress()["status"], "completed")
        self.assertEqual(len(self.node("root")["attempts"]), 2)

    def test_retry_cannot_invalidate_results_used_by_running_consumers(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B", "depends_on": ["a"]})
        self.start("a"); self.finish("a"); self.start("b")
        self.assertRejectedUnchanged(lambda: self.start("a", retry=True))

    def test_retry_cannot_invalidate_ancestor_used_by_running_consumer(self):
        self.add({"id": "g", "title": "G"}, {"id": "b", "title": "B", "depends_on": ["g"]})
        self.add({"id": "a", "title": "A"}, parent="g")
        self.start("a"); self.finish("a"); self.finish("g"); self.start("b")
        self.assertRejectedUnchanged(lambda: self.start("a", retry=True))

    def test_split_running_task_closes_attempt_and_can_be_completed(self):
        self.add({"id": "a", "title": "A"})
        old = self.start("a")
        self.assertRejectedUnchanged(lambda: self.add({"id": "child", "title": "Child"}, parent="a"))
        self.add({"id": "child", "title": "Child"}, parent="a", attempt_id=old)
        parent = self.node("a")
        self.assertEqual(parent["execution_status"], "pending")
        self.assertEqual(parent["attempts"][0]["execution_status"], "cancelled")
        self.assertIsNotNone(parent["attempts"][0]["finished_at"])
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "a", "finished", outcome="resolved", attempt_id=old, assignment=ASSIGNMENT))
        self.start("child"); self.finish("child"); self.finish("a"); self.finish("root")
        attempts = self.node("a")["attempts"]
        self.assertEqual([a["kind"] for a in attempts], ["execution", "synthesis"])
        self.assertEqual(self.progress()["status"], "completed")

    def test_invalid_split_does_not_close_running_attempt(self):
        self.add({"id": "a", "title": "A"})
        old = self.start("a")
        self.assertRejectedUnchanged(lambda: self.add({"id": "child", "title": "Child", "depends_on": ["a"]}, parent="a", attempt_id=old))
        self.assertEqual(self.node("a")["execution_status"], "running")

    def test_synthesis_requires_and_persists_execution_metadata(self):
        self.add({"id": "a", "title": "A"})
        self.start("a"); self.finish("a")
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "finished", outcome="resolved"))
        self.finish("root", summary="Accepted")
        n = self.node("root")
        self.assertEqual(n["assignment"], ASSIGNMENT)
        self.assertEqual(n["attempts"][0]["kind"], "synthesis")
        self.assertEqual(n["attempts"][0]["summary"], "Accepted")
        self.assertIsNotNone(n["started_at"])

    def test_attempt_metadata_is_immutable_on_updates_and_completion(self):
        self.add({"id": "a", "title": "A"})
        aid = self.start("a")
        for state in ["running", "finished"]:
            kwargs = {"outcome": "resolved"} if state == "finished" else {}
            self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "a", state, attempt_id=aid, assignment={**ASSIGNMENT, "model": "different-fixture"}, **kwargs))
        self.finish("a")

    def test_stray_attempt_cannot_finish_an_unstarted_task(self):
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "finished", outcome="resolved", attempt_id="stale"))

    def register(self, result, view_id):
        s = result["structuredContent"]
        return server.register_ui_view(self.sid, view_id, s["view_sequence"], s["state_revision"],
                                       s["progress"]["task_instance_id"])["structuredContent"]["status"]

    def test_newer_result_wins_independent_of_mount_order(self):
        older = server.get_progress(self.sid)
        newer = server.get_progress(self.sid)
        self.assertEqual(self.register(newer, "new"), "registered")
        self.assertEqual(self.register(older, "old-remount"), "superseded")
        self.assertEqual(server.check_ui_view(self.sid, "new")["structuredContent"]["status"], "latest")

    def test_older_snapshot_cannot_win_even_if_response_was_issued_later(self):
        old = self.progress()
        self.add({"id": "a", "title": "A"})
        new_result = server.get_progress(self.sid)
        late_result = server._tool_result("Delayed snapshot", old, self.sid)
        self.assertGreater(late_result["structuredContent"]["view_sequence"], new_result["structuredContent"]["view_sequence"])
        self.register(new_result, "new")
        self.assertEqual(self.register(late_result, "old"), "superseded")

    def test_archived_task_and_legacy_cards_cannot_reclaim_view(self):
        old = server.get_progress(self.sid)
        new = server.init_task_network("Next", self.sid)
        self.register(new, "new")
        self.assertEqual(self.register(old, "old"), "superseded")
        self.assertEqual(server.register_ui_view(self.sid, "legacy")["structuredContent"]["status"], "unregistered")
        self.assertEqual(server.check_ui_view(self.sid, "new")["structuredContent"]["status"], "latest")

    def test_state_order_survives_task_switch_and_clear(self):
        initial = server.get_progress(self.sid)["structuredContent"]["state_revision"]
        second = server.init_progress(["Legacy fixture"], session_id=self.sid)["structuredContent"]["state_revision"]
        cleared = server.clear_progress(self.sid)["structuredContent"]["state_revision"]
        empty = server.get_progress(self.sid)["structuredContent"]
        recreated = server.init_task_network("Recreated", self.sid)["structuredContent"]["state_revision"]
        self.assertLess(initial, second)
        self.assertLess(second, cleared)
        self.assertIsNone(empty["progress"])
        self.assertEqual(empty["state_revision"], cleared)
        self.assertLess(cleared, recreated)
        self.assertEqual(set(server._read_order(self.sid)), {"state_revision", "view_sequence"})

    def test_linear_updates_also_advance_snapshot_order(self):
        r = server.init_progress(["One"], session_id=self.sid)
        p = r["structuredContent"]["progress"]
        result = server.update_step(self.sid, 0, "in_progress", assignment=ASSIGNMENT, task_instance_id=p["task_instance_id"])
        self.assertGreater(result["structuredContent"]["state_revision"], p["state_revision"])
        server.update_step(self.sid, 0, "completed", task_instance_id=p["task_instance_id"])

    def test_legacy_deadlocked_plan_can_still_be_read_and_replanned(self):
        self.add({"id": "g", "title": "G"})
        self.add({"id": "child", "title": "Child"}, parent="g")
        data = server._read(self.sid)
        data["nodes"]["child"]["depends_on"] = ["g"]
        server._session_file(self.sid).write_text(json.dumps(data))
        self.assertEqual(self.progress()["ready_tasks"], [])
        server.rebuild_task_network(self.sid, "Repair legacy deadlock")
        self.assertEqual(len(self.progress()["history"]), 1)

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "Process lock check uses POSIX fork")
    def test_concurrent_writes_preserve_nodes_and_order(self):
        def worker(i):
            server.decompose_task(self.sid, "root", [{"id": f"worker-{i}", "title": str(i)}])
        processes = [multiprocessing.get_context("fork").Process(target=worker, args=(i,)) for i in range(12)]
        for process in processes: process.start()
        for process in processes:
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.progress()["nodes"]), 13)
        self.assertEqual(self.progress()["state_revision"], 13)

    @unittest.skipIf(sys.platform == "win32", "POSIX launcher smoke check")
    def test_launcher_handshake_tools_and_app_resource(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": server.RESOURCE_URI}},
        ]
        proc = subprocess.run([str(PLUGIN / "scripts" / "launch_progress_mcp"), "./server.py"], cwd=PLUGIN,
                              input="".join(json.dumps(m) + "\n" for m in messages), text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line)["result"] for line in proc.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertEqual(replies[0]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(replies[1]["tools"]), 13)
        self.assertEqual(replies[2]["contents"][0]["text"], (PLUGIN / "app.html").read_text())

    def test_stdio_recovers_from_malformed_requests(self):
        messages = ["[]", "null", "{", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": [], "arguments": {}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_progress", "arguments": None}}),
                    json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"})]
        proc = subprocess.run([sys.executable, str(PLUGIN / "server.py")], input="\n".join(messages) + "\n", capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([r["error"]["code"] for r in replies[:-1]], [-32600, -32600, -32700, -32602, -32602, -32602])
        self.assertEqual(replies[-1], {"jsonrpc": "2.0", "id": 4, "result": {}})


if __name__ == "__main__":
    unittest.main()
