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
ASSIGNMENT = {"execution_mode": "main_session", "model": "test-fixture", "reasoning_effort": "high", "self_execution_reason": "Fixture verifies a bounded coordinator operation"}


class ProgressTests(unittest.TestCase):
    def test_append_inserts_real_pending_dependency_with_reason_atomically(self):
        self.add({"id": "first", "title": "First"}, {"id": "later", "title": "Later", "depends_on": ["first"]})
        self.add({"id": "inserted", "title": "New issue", "depends_on": ["first"],
                  "precedes": ["later"], "addition_reason": "New issue must be fixed before verification"})
        later = self.node("later")
        added = self.node("inserted")
        self.assertEqual(later["depends_on"], ["first", "inserted"])
        self.assertTrue(added["is_added"])
        self.assertEqual(added["addition_reason"], "New issue must be fixed before verification")
        self.assertLess(self.node("root")["children"].index("inserted"), self.node("root")["children"].index("later"))
        self.start("first"); self.finish("first")
        self.assertEqual(self.progress()["ready_tasks"], ["inserted"])
        self.start("inserted"); self.finish("inserted")
        self.assertEqual(self.progress()["ready_tasks"], ["later"])
        self.start("later")
        self.assertRejectedUnchanged(lambda: self.add({"id": "unsafe", "title": "Unsafe", "precedes": ["later"], "addition_reason": "late prerequisite"}))

    def test_append_cycle_and_missing_reason_do_not_change_state(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B", "depends_on": ["a"]})
        self.assertRejectedUnchanged(lambda: self.add({"id": "bad", "title": "Bad", "precedes": ["b"]}))
        with self.assertRaisesRegex(server.ProgressError, "循环依赖"):
            self.add({"id": "bad", "title": "Bad", "depends_on": ["b"], "precedes": ["a"], "addition_reason": "cycle"})
        self.assertNotIn("bad", [node["id"] for node in self.progress()["nodes"]])

    def test_stage_inventory_invalidated_by_retry_and_records_are_selective(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B", "depends_on": ["a"]})
        self.start("a"); self.finish("a")
        self.start("b"); self.finish("b")
        old_a, old_b = self.node("a")["attempt_id"], self.node("b")["attempt_id"]
        server.update_task(self.sid, "a", "running", retry=True, assignment=ASSIGNMENT)
        self.assertNotIn("stage_inventory_due_attempt", self.node("a"))
        self.assertNotIn("stage_inventory_due_attempt", self.node("b"))
        self.assertEqual(self.node("a")["stage_inventories"][-1]["status"], "superseded")
        self.assertEqual(self.node("b")["stage_inventories"][-1]["status"], "superseded")
        revision = server.get_progress(self.sid)["structuredContent"]["state_revision"]
        self.assertRejectedUnchanged(lambda: server.review_stage_inventory(self.sid, "b", old_b, [], revision))
        detail = server.get_progress(self.sid, view="tasks", task_ids=["a"])["structuredContent"]["progress"]["nodes"][0]
        self.assertNotIn("stage_inventories", detail)
        records = server.get_progress(self.sid, view="records", task_id="a", record_kind="stage_inventories")["structuredContent"]["progress"]["records"]
        self.assertEqual(records[-1]["attempt_id"], old_a)

    def test_major_stage_completion_receipt_requests_inventory(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B"})
        attempt = self.start("a")
        args = {"session_id": self.sid, "task_id": "a", "execution_status": "finished", "outcome": "open", "attempt_id": attempt}
        result = server.update_task(self.sid, "a", "finished", outcome="open", attempt_id=attempt)
        wire = server._wire_result("update_task", args, result)
        self.assertTrue(wire["structuredContent"]["stage_inventory_due"])
        self.assertIn("盘点", wire["content"][0]["text"])
        overview = server.get_progress(self.sid)["structuredContent"]
        self.assertEqual(overview["progress"]["stage_inventory_due"], [{"task_id": "a", "attempt_id": attempt}])
        revision = overview["state_revision"]
        decisions = [{"task_id": "a", "decision": "continue", "reason": "Need to finish the open result"},
                     {"task_id": "b", "decision": "covered", "reason": "Covered by fixture evidence"}]
        self.assertRejectedUnchanged(lambda: server.review_stage_inventory(self.sid, "a", attempt, decisions[:1], revision))
        self.assertRejectedUnchanged(lambda: server.review_stage_inventory(self.sid, "a", attempt, decisions, revision - 1))
        server.review_stage_inventory(self.sid, "a", attempt, decisions, revision)
        self.assertEqual(self.node("a")["stage_inventories"][0]["decisions"], decisions)
        self.assertEqual(server.get_progress(self.sid)["structuredContent"]["progress"]["stage_inventory_due"], [])
        self.assertEqual(self.node("b")["execution_status"], "pending")

    def test_idle_session_locks_are_released_in_a_long_lived_service(self):
        import gc
        import uuid
        with server._session_lock(self.sid):
            active = server._SESSION_LOCKS[self.sid]
            # Touch many unrelated sessions without retaining their locks.
            for _ in range(100):
                with server._session_lock(str(uuid.uuid4())):
                    pass
            gc.collect()
            self.assertIs(server._SESSION_LOCKS[self.sid], active)
            self.assertEqual(len(server._SESSION_LOCKS), 1)
        del active
        gc.collect()
        self.assertEqual(len(server._SESSION_LOCKS), 0)

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
        return server.get_progress_view(self.sid)["structuredContent"]["progress"]

    def node(self, node_id):
        return next(n for n in server.get_progress_view(self.sid, include_history=True)["structuredContent"]["progress"]["nodes"] if n["id"] == node_id)

    def add(self, *tasks, parent="root", **kwargs):
        planned = [
            {**task, "assignment": task.get("assignment", {"execution_mode": "main_session"})}
            for task in tasks
        ]
        return server.decompose_task(self.sid, parent, planned, **kwargs)

    def start(self, node_id, **kwargs):
        server.update_task(self.sid, node_id, "running", assignment=ASSIGNMENT, **kwargs)
        return self.node(node_id)["attempt_id"]

    def finish(self, node_id, outcome="resolved", **kwargs):
        if self.node(node_id).get("children"):
            kwargs.setdefault("summary", "Coordinator checked all child results")
            if outcome == "resolved":
                kwargs.setdefault("evidence", ["fixture://checked-child-results"])
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
        self.assertRejectedUnchanged(lambda: self.finish("g", outcome="open", summary=""))
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
        self.assertEqual(self.progress()["planning_status"], "required")
        self.assertEqual(self.progress()["ready_tasks"], [])
        self.assertRejectedUnchanged(lambda: server.update_task(
            self.sid, self.progress()["root_id"], "running", assignment=ASSIGNMENT
        ))
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

    def test_retry_invalidates_transitive_consumers_and_retains_attempts(self):
        self.add({"id": "a", "title": "Build"}, {"id": "b", "title": "Validate", "depends_on": ["a"]},
                 {"id": "c", "title": "Deliver", "depends_on": ["b"]})
        for task in ["a", "b", "c"]:
            self.start(task); self.finish(task, summary="Original " + task)
        self.finish("root")
        old_b = self.node("b")["attempt_id"]
        self.start("a", retry=True)
        self.assertEqual(self.progress()["ready_tasks"], [])
        for task in ["b", "c", "root"]:
            self.assertEqual(self.node(task)["execution_status"], "pending")
            self.assertEqual(self.node(task)["invalidated_by"], "a")
            self.assertEqual(self.node(task)["attempts"][0]["invalidated_by"], "a")
        self.assertRejectedUnchanged(lambda: self.start("c"))
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "b", "finished", outcome="resolved", attempt_id=old_b))
        self.finish("a")
        self.assertEqual(self.progress()["ready_tasks"], ["b"])
        self.start("b"); self.finish("b")
        self.assertNotIn("invalidated_by", self.node("b"))
        self.assertEqual(self.node("b")["attempt"], 2)
        self.assertEqual(self.progress()["ready_tasks"], ["c"])
        self.start("c"); self.finish("c"); self.finish("root")
        self.assertEqual(self.progress()["status"], "completed")

    def test_retry_rejects_transitively_running_consumer_atomically(self):
        self.add({"id": "a", "title": "A"}, {"id": "b", "title": "B", "depends_on": ["a"]},
                 {"id": "c", "title": "C", "depends_on": ["b"]})
        for task in ["a", "b"]:
            self.start(task); self.finish(task)
        self.start("c")
        self.assertRejectedUnchanged(lambda: self.start("a", retry=True))

    def test_retry_invalidates_dependent_subtree_and_its_acceptance(self):
        self.add({"id": "a", "title": "A"}, {"id": "g", "title": "Group", "depends_on": ["a"]},
                 {"id": "next", "title": "Next", "depends_on": ["g"]})
        self.add({"id": "child", "title": "Child"}, parent="g")
        for task in ["a", "child"]:
            self.start(task); self.finish(task)
        self.finish("g")
        self.start("a", retry=True)
        self.assertEqual(self.node("g")["execution_status"], "pending")
        self.assertEqual(self.node("child")["execution_status"], "pending")
        self.assertRejectedUnchanged(lambda: self.start("next"))
        self.finish("a"); self.start("child"); self.finish("child"); self.finish("g")
        self.assertEqual(self.progress()["ready_tasks"], ["next"])

    def test_failed_dependency_group_can_close_open_with_reason(self):
        self.add({"id": "a", "title": "A"}, {"id": "g", "title": "G", "depends_on": ["a"]})
        self.add({"id": "child", "title": "Child"}, parent="g")
        aid = self.start("a")
        server.update_task(self.sid, "a", "failed", attempt_id=aid)
        server.update_task(self.sid, "child", "cancelled")
        self.assertRejectedUnchanged(lambda: self.finish("g"))
        self.assertRejectedUnchanged(lambda: self.finish("g", outcome="open", summary=""))
        self.finish("g", outcome="open", summary="Required input failed")
        self.finish("root", outcome="open", summary="Cannot complete")
        self.assertEqual(self.progress()["status"], "waiting_synthesis")
        self.assertEqual(self.node("root")["outcome"], "open")
        self.assertEqual(self.node("g")["attempts"][0]["kind"], "synthesis")

    def test_group_cancellation_requires_closed_children_metadata_and_reason(self):
        self.add({"id": "child", "title": "Child"})
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "cancelled", summary="Stop", assignment=ASSIGNMENT))
        server.update_task(self.sid, "child", "cancelled")
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "cancelled", assignment=ASSIGNMENT))
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "cancelled", summary="Stop"))
        server.update_task(self.sid, "root", "cancelled", summary="Stop", assignment=ASSIGNMENT)
        self.assertEqual(self.progress()["status"], "waiting_synthesis")
        self.assertEqual(self.node("root")["execution_status"], "cancelled")
        self.assertEqual(self.node("root")["attempts"][0]["execution_status"], "cancelled")

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
        self.assertEqual({key: n["assignment"][key] for key in ASSIGNMENT}, ASSIGNMENT)
        self.assertEqual(n["assignment"]["metadata_provenance"]["verification"], "not_verified_by_codex_host")
        self.assertEqual(n["attempts"][0]["kind"], "synthesis")
        self.assertEqual(n["attempts"][0]["summary"], "Accepted")
        self.assertIsNotNone(n["started_at"])

    def test_main_session_unknown_values_do_not_block_and_are_marked_unverified(self):
        result = server.init_task_network(
            "Unknown runtime metadata", main_session={"model": "gpt-fixture"},
            atomic_reason="The fixture is a single tracked operation",
        )
        sid = result["structuredContent"]["session_id"]
        current = server.get_progress_view(sid)["structuredContent"]["progress"]
        self.assertEqual(current["main_session"]["model"], "gpt-fixture")
        self.assertIsNone(current["main_session"]["reasoning_effort"])
        provenance = current["main_session"]["metadata_provenance"]
        self.assertEqual(provenance["verification"], "not_verified_by_codex_host")
        self.assertEqual(provenance["model_source"], "mcp_tool_input")
        self.assertEqual(provenance["reasoning_effort_source"], "unknown")
        self.assertIsNotNone(provenance["captured_at"])

        started = server.update_task(sid, "root", "running", assignment={"execution_mode": "main_session"})
        node = started["structuredContent"]["progress"]["nodes"][0]
        self.assertEqual(node["execution_status"], "running")
        self.assertNotIn("model", node["assignment"])
        self.assertNotIn("reasoning_effort", node["assignment"])
        self.assertEqual(node["assignment"]["metadata_provenance"]["model_source"], "unknown")
        history_node = server.get_progress_view(sid, include_history=True)["structuredContent"]["progress"]["nodes"][0]
        self.assertEqual(history_node["attempts"][0]["assignment"]["metadata_provenance"]["verification"], "not_verified_by_codex_host")

    def test_caller_cannot_claim_codex_verified_assignment_provenance(self):
        server.init_task_network("Atomic provenance fixture", self.sid, new_task_reason="New provenance target; prior pending fixture archived",
                                 atomic_reason="One metadata-provenance regression")
        assignment = {**ASSIGNMENT, "metadata_provenance": {"verification": "verified_by_codex_host"}}
        server.update_task(self.sid, "root", "running", assignment=assignment,
                           task_instance_id=self.progress()["task_instance_id"])
        provenance = self.node("root")["assignment"]["metadata_provenance"]
        self.assertEqual(provenance["source"], "mcp_tool_input")
        self.assertEqual(provenance["verification"], "not_verified_by_codex_host")
        self.assertNotEqual(provenance["verification"], "verified_by_codex_host")

    def test_subagent_still_requires_selected_dispatch_metadata(self):
        self.assertRejectedUnchanged(lambda: server.update_task(
            self.sid, "root", "running", assignment={"execution_mode": "subagent"}
        ))

    def test_attempt_metadata_is_immutable_on_updates_and_completion(self):
        self.add({"id": "a", "title": "A"})
        aid = self.start("a")
        for state in ["running", "finished"]:
            kwargs = {"outcome": "resolved"} if state == "finished" else {}
            self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "a", state, attempt_id=aid, assignment={**ASSIGNMENT, "model": "different-fixture"}, **kwargs))
        self.finish("a")

    def test_stray_attempt_cannot_finish_an_unstarted_task(self):
        self.assertRejectedUnchanged(lambda: server.update_task(self.sid, "root", "finished", outcome="resolved", attempt_id="stale"))

    def test_decomposition_rejects_tasks_without_executor_metadata(self):
        self.assertRejectedUnchanged(lambda: server.decompose_task(
            self.sid, "root", [{"id": "unassigned", "title": "Unassigned"}]
        ))

    def test_clear_tool_documents_archived_only_policy_and_host_limit(self):
        response = json.loads(server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}))
        tool = next(item for item in response['result']['tools'] if item['name'] == 'clear_progress')
        self.assertIn('已归档', tool['description'])
        self.assertIn('不会读取或验证 Codex 归档状态', tool['description'])

    def register(self, result, view_id):
        s = result["structuredContent"]
        return server.register_ui_view(self.sid, view_id, s["view_sequence"], s["state_revision"],
                                       s["progress"]["task_instance_id"])["structuredContent"]["status"]

    def test_newer_result_wins_independent_of_mount_order(self):
        older = server.get_progress_view(self.sid)
        newer = server.get_progress_view(self.sid)
        self.assertEqual(self.register(newer, "new"), "registered")
        self.assertEqual(self.register(older, "old-remount"), "superseded")
        self.assertEqual(server.check_ui_view(self.sid, "new")["structuredContent"]["status"], "latest")

    def test_fullscreen_progress_does_not_supersede_inline_view(self):
        server.init_task_network("Atomic view fixture", self.sid, new_task_reason="New view target; prior pending fixture archived",
                                 atomic_reason="One view-lease regression")
        inline_result = server.get_progress_view(self.sid)
        self.assertEqual(self.register(inline_result, "conversation"), "registered")
        snapshot = inline_result["structuredContent"]
        fullscreen = server.register_ui_view(
            self.sid, "tab", snapshot["view_sequence"], snapshot["state_revision"],
            snapshot["progress"]["task_instance_id"], "fullscreen",
        )
        self.assertEqual(fullscreen["structuredContent"]["status"], "registered")

        server.update_task(self.sid, "root", "running", assignment=ASSIGNMENT,
                           task_instance_id=self.progress()["task_instance_id"])
        updated_fullscreen = server.get_progress_view(self.sid)["structuredContent"]
        updated = server.register_ui_view(
            self.sid, "tab", updated_fullscreen["view_sequence"], updated_fullscreen["state_revision"],
            updated_fullscreen["progress"]["task_instance_id"], "fullscreen",
        )
        self.assertEqual(updated["structuredContent"]["status"], "registered")
        self.assertEqual(
            server.check_ui_view(self.sid, "conversation", "inline")["structuredContent"]["status"],
            "hidden_by_fullscreen",
        )

        server.unregister_ui_view(self.sid, "tab")
        self.assertEqual(
            server.check_ui_view(self.sid, "conversation", "inline")["structuredContent"]["status"],
            "latest",
        )

    def test_second_fullscreen_view_is_rejected_while_first_lease_is_active(self):
        source = server.get_progress_view(self.sid)["structuredContent"]
        task_instance_id = source["progress"]["task_instance_id"]
        self.assertEqual(server.register_ui_view(
            self.sid, "tab-1", source["view_sequence"], source["state_revision"], task_instance_id, "fullscreen",
        )["structuredContent"]["status"], "registered")
        self.assertEqual(server.register_ui_view(
            self.sid, "tab-2", source["view_sequence"], source["state_revision"], task_instance_id, "fullscreen",
        )["structuredContent"]["status"], "duplicate_fullscreen")
        server.unregister_ui_view(self.sid, "tab-1")
        self.assertEqual(server.register_ui_view(
            self.sid, "tab-2", source["view_sequence"], source["state_revision"], task_instance_id, "fullscreen",
        )["structuredContent"]["status"], "registered")

    def test_older_snapshot_cannot_win_even_if_response_was_issued_later(self):
        old = self.progress()
        self.add({"id": "a", "title": "A"})
        new_result = server.get_progress_view(self.sid)
        late_result = server._tool_result("Delayed snapshot", old, self.sid)
        self.assertGreater(late_result["structuredContent"]["view_sequence"], new_result["structuredContent"]["view_sequence"])
        self.register(new_result, "new")
        self.assertEqual(self.register(late_result, "old"), "superseded")

    def test_archived_task_and_legacy_cards_cannot_reclaim_view(self):
        old = server.get_progress_view(self.sid)
        new = server.init_task_network("Next", self.sid, new_task_reason="New test target; prior pending fixture intentionally archived")
        self.register(new, "new")
        self.assertEqual(self.register(old, "old"), "superseded")
        self.assertEqual(server.register_ui_view(self.sid, "legacy")["structuredContent"]["status"], "unregistered")
        self.assertEqual(server.check_ui_view(self.sid, "new")["structuredContent"]["status"], "latest")

    def test_task_switch_waits_for_actual_replacement_view(self):
        old = server.get_progress_view(self.sid)
        self.register(old, "existing")
        new = server.init_task_network("Next", self.sid, new_task_reason="New test target; prior pending fixture intentionally archived")
        self.assertEqual(server.check_ui_view(self.sid, "existing")["structuredContent"]["status"], "latest")
        self.assertEqual(self.register(old, "old-remount"), "superseded")
        self.register(new, "replacement")
        self.assertEqual(server.check_ui_view(self.sid, "existing")["structuredContent"]["status"], "superseded")

    def test_fullscreen_view_hides_inline_view_until_released(self):
        source = server.get_progress_view(self.sid)
        self.assertEqual(self.register(source, "conversation"), "registered")
        snapshot = source["structuredContent"]
        full = server.register_ui_view(
            self.sid, "tab", snapshot["view_sequence"], snapshot["state_revision"],
            snapshot["progress"]["task_instance_id"], "fullscreen",
        )
        self.assertEqual(full["structuredContent"]["status"], "registered")
        self.assertEqual(
            server.check_ui_view(self.sid, "conversation", "inline")["structuredContent"]["status"],
            "hidden_by_fullscreen",
        )
        server.unregister_ui_view(self.sid, "tab")
        self.assertEqual(
            server.check_ui_view(self.sid, "conversation", "inline")["structuredContent"]["status"],
            "latest",
        )

    def test_expired_fullscreen_lease_does_not_keep_inline_hidden(self):
        source = server.get_progress_view(self.sid)
        self.register(source, "conversation")
        snapshot = source["structuredContent"]
        server.register_ui_view(
            self.sid, "tab", snapshot["view_sequence"], snapshot["state_revision"],
            snapshot["progress"]["task_instance_id"], "fullscreen",
        )
        metadata_path = server._ui_view_file(self.sid)
        metadata = json.loads(metadata_path.read_text())
        metadata["views"]["tab"]["expires_at"] = server.time.time() - 1
        metadata_path.write_text(json.dumps(metadata))
        self.assertEqual(
            server.check_ui_view(self.sid, "conversation", "inline")["structuredContent"]["status"],
            "latest",
        )

    def test_state_order_survives_task_switch_and_clear(self):
        initial = server.get_progress_view(self.sid)["structuredContent"]["state_revision"]
        second = server.init_progress(["Legacy fixture"], session_id=self.sid, new_task_reason="New linear target; prior pending fixture archived")["structuredContent"]["state_revision"]
        cleared = server.clear_progress(self.sid)["structuredContent"]["state_revision"]
        empty = server.get_progress_view(self.sid)["structuredContent"]
        recreated = server.init_task_network("Recreated", self.sid)["structuredContent"]["state_revision"]
        self.assertLess(initial, second)
        self.assertLess(second, cleared)
        self.assertIsNone(empty["progress"])
        self.assertEqual(empty["state_revision"], cleared)
        self.assertLess(cleared, recreated)
        self.assertEqual(set(server._read_order(self.sid)), {"state_revision", "view_sequence", "require_task_instance_id", "legacy_history_cleared"})
        self.assertTrue(server._read_order(self.sid)["require_task_instance_id"])

    def test_linear_updates_also_advance_snapshot_order(self):
        r = server.init_progress(["One"], session_id=self.sid, new_task_reason="New linear target; prior pending fixture archived")
        p = r["structuredContent"]["progress"]
        result = server.update_step(self.sid, 0, "in_progress", assignment=ASSIGNMENT, task_instance_id=p["task_instance_id"])
        self.assertGreater(result["structuredContent"]["state_revision"], p["state_revision"])
        attempt_id = result["structuredContent"]["progress"]["steps"][0]["attempt_id"]
        server.update_step(self.sid, 0, "completed", task_instance_id=p["task_instance_id"], attempt_id=attempt_id)

    def test_linear_order_single_running_and_reopen_guards(self):
        result = server.init_progress(["First", "Second"], legacy_linear=True)
        sid = result["structuredContent"]["session_id"]
        for status in ["in_progress", "completed"]:
            with self.assertRaises(server.ProgressError):
                server.update_step(sid, 1, status, assignment=ASSIGNMENT)
        first_run = server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT)
        first_attempt = first_run["structuredContent"]["progress"]["steps"][0]["attempt_id"]
        with self.assertRaises(server.ProgressError):
            server.update_step(sid, 1, "in_progress", assignment=ASSIGNMENT)
        server.update_step(sid, 0, "completed", attempt_id=first_attempt)
        second_run = server.update_step(sid, 1, "in_progress", assignment=ASSIGNMENT)
        second_attempt = second_run["structuredContent"]["progress"]["steps"][1]["attempt_id"]
        for status in ["pending", "in_progress"]:
            with self.assertRaises(server.ProgressError):
                server.update_step(sid, 0, status, assignment=ASSIGNMENT, attempt_id=first_attempt)
        server.update_step(sid, 1, "pending", attempt_id=second_attempt)
        server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT, attempt_id=first_attempt)
        self.assertEqual([step["status"] for step in server.get_progress_view(sid)["structuredContent"]["progress"]["steps"]], ["in_progress", "pending"])

    def test_legacy_deadlocked_plan_can_still_be_read_and_replanned(self):
        self.add({"id": "g", "title": "G"})
        self.add({"id": "child", "title": "Child"}, parent="g")
        data = server._read(self.sid)
        data["nodes"]["child"]["depends_on"] = ["g"]
        server._session_file(self.sid).write_text(json.dumps(data))
        self.assertEqual(self.progress()["ready_tasks"], [])
        server.rebuild_task_network(self.sid, "Repair legacy deadlock")
        self.assertEqual(len(server.get_progress_view(self.sid, include_history=True)["structuredContent"]["progress"]["history"]), 1)

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "Process lock check uses POSIX fork")
    def test_concurrent_writes_preserve_nodes_and_order(self):
        def worker(i):
            server.decompose_task(self.sid, "root", [{
                "id": f"worker-{i}", "title": str(i), "assignment": {"execution_mode": "main_session"},
            }])
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
        self.assertEqual(len(replies[1]["tools"]), 27)
        listed = {tool["name"]: tool for tool in replies[1]["tools"]}
        self.assertTrue({"get_progress", "update_task", "review_task", "route_request", "get_progress_view", "consume_display_intent"} <= listed.keys())
        self.assertEqual(listed["consume_display_intent"]["_meta"]["ui"]["visibility"], ["app"])
        self.assertEqual(listed["get_progress_view"]["_meta"]["ui"]["visibility"], ["app"])
        manifest_version = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"]
        self.assertEqual(replies[0]["serverInfo"]["version"], manifest_version)
        self.assertEqual(replies[2]["contents"][0]["text"], server._resource_html())
        self.assertNotIn("__TASK_PROGRESS_VERSION__", replies[2]["contents"][0]["text"])

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
