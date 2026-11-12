"""Stage inventory scopes and source bindings preserve execution facts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "stage_inventory_server", Path(__file__).resolve().parents[1] / "plugins/task-progress/server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)):
    spec.loader.exec_module(server)
MAIN = {"execution_mode": "main_session", "self_execution_reason": "Bounded inventory fixture"}
SUB = {"execution_mode": "subagent"}


def decision(nid, disposition="continue"):
    return {"task_id": nid, "decision": disposition, "reason": "Follow up using the fixture evidence"}


class StageInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        server.LEGACY_DB_PATH = Path(self.temp.name) / "missing.sqlite"
        p = server.init_task_network("Inventory fixture", tasks=[
            {"id": nid, "title": nid, "assignment": MAIN} for nid in ("a", "b", "backlog", "later")
        ])["structuredContent"]["progress"]
        self.sid, self.iid = p["session_id"], p["task_instance_id"]

    def raw(self):
        return server._read(self.sid)

    def finish(self, nid, outcome="resolved"):
        server.update_task(self.sid, nid, "running", assignment=MAIN, task_instance_id=self.iid)
        aid = self.raw()["nodes"][nid]["attempt_id"]
        server.update_task(self.sid, nid, "finished", outcome=outcome, attempt_id=aid,
                           summary="Checked fixture result", evidence=["fixture://result"], task_instance_id=self.iid)
        return aid

    def binding(self, nid, scope=None, view="tasks"):
        args = {"task_ids": [nid]} if view == "tasks" else {"task_id": nid}
        response = server.get_progress(self.sid, task_instance_id=self.iid, view=view,
                                       scope_task_ids=scope, **args)["structuredContent"]
        node = next(n for n in response["progress"]["nodes"] if n["id"] == nid)
        return node["stage_inventory_binding"]

    def review(self, nid, decisions, scope=None, binding=None, reuse=None, **kwargs):
        binding = binding or self.binding(nid, scope)
        return server.review_stage_inventory(
            self.sid, nid, binding["attempt_id"], decisions, task_instance_id=self.iid,
            scope_task_ids=scope, source_fingerprint=binding["source_fingerprint"], reuse_decisions=reuse, **kwargs)

    def record(self, nid):
        return self.raw()["nodes"][nid]["stage_inventories"][-1]

    def rejected(self, action):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            action()
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def test_old_call_defaults_to_global_and_preserves_execution(self):
        aid = self.finish("a", "open")
        revision = self.raw()["state_revision"]
        decisions = [decision(nid) for nid in ("a", "b", "backlog", "later")]
        self.rejected(lambda: server.review_stage_inventory(self.sid, "a", aid, decisions[:1], revision, self.iid))
        before = self.raw()["nodes"]
        server.review_stage_inventory(self.sid, "a", aid, decisions, revision, self.iid)
        after = self.raw()["nodes"]
        for nid in before:
            for field in ("execution_status", "outcome", "review_status", "attempt_id", "depends_on"):
                self.assertEqual(before[nid].get(field), after[nid].get(field))
        self.assertIsNone(self.record("a")["scope_task_ids"])
        self.assertEqual(self.record("a")["decisions"], decisions)

    def test_coverage_error_reports_missing_extra_and_duplicate_task_ids(self):
        self.finish("a", "open")
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError) as raised:
            self.review("a", [decision("a"), decision("a"), decision("outside_scope")])

        message = str(raised.exception)
        self.assertIn('缺少 task_id=["b", "backlog", "later"]', message)
        self.assertIn('多余 task_id=["outside_scope"]', message)
        self.assertIn('重复 task_id=["a"]', message)
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def test_scope_contains_stage_and_explicit_cross_stage_backlog(self):
        self.finish("a", "open")
        self.rejected(lambda: self.binding("a", ["backlog"]))
        scope = ["a", "backlog"]
        binding = self.binding("a", scope, "tree")
        self.rejected(lambda: self.review("a", [decision("a")], scope, binding))
        self.rejected(lambda: self.review("a", [decision("a"), decision("later")], scope, binding))
        self.review("a", [decision("a"), decision("backlog")], scope, binding)
        self.assertEqual(set(self.record("a")["scope_task_ids"]), set(scope))
        self.assertEqual(self.raw()["nodes"]["later"]["execution_status"], "pending")

    def test_batch_scope_error_names_the_stage_missing_from_scope(self):
        self.finish("a")
        self.finish("b")
        args = {"session_id": self.sid, "task_instance_id": self.iid, "view": "tasks",
                "scope_task_ids": ["a"]}

        def query(task_ids):
            return json.loads(server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_progress", "arguments": {**args, "task_ids": task_ids}},
            }))["result"]

        rejected = query(["a", "b"])
        self.assertTrue(rejected["isError"])
        self.assertIn("scope_task_ids 缺少阶段 b", rejected["content"][0]["text"])
        selected = query(["a"])["structuredContent"]["progress"]["nodes"][0]
        self.assertEqual(selected["stage_inventory_binding"]["scope_task_ids"], ["a"])

    def test_next_actions_keep_unresolved_tasks_visible_and_refresh_after_retry(self):
        self.finish("a", "open")
        self.review("a", [
            decision("a", "obsolete"), decision("b", "covered"),
            decision("backlog", "continue"), decision("later", "continue"),
        ])
        actions = server._progress_payload(self.raw())["next_actions"]
        for nid in ("a", "b", "backlog", "later"):
            self.assertTrue(any(nid in action for action in actions), (nid, actions))
        self.assertTrue(any("最近盘点为 obsolete" in action for action in actions))
        self.assertTrue(any("retry=true" in action for action in actions))

        server.update_task(self.sid, "a", "running", assignment=MAIN, retry=True,
                           task_instance_id=self.iid)
        actions = server._progress_payload(self.raw())["next_actions"]
        self.assertTrue(any("等待任务 a" in action for action in actions))
        self.assertFalse(any("最近盘点为 obsolete" in action for action in actions))

        new_attempt = self.raw()["nodes"]["a"]["attempt_id"]
        server.update_task(self.sid, "a", "finished", outcome="open", attempt_id=new_attempt,
                           summary="New unresolved result", evidence=["fixture://new-result"],
                           task_instance_id=self.iid)
        actions = server._progress_payload(self.raw())["next_actions"]
        self.assertTrue(any("盘点未解决任务 a" in action for action in actions))
        self.assertFalse(any("最近盘点为 obsolete" in action for action in actions))

    def test_latest_current_decision_wins_across_overlapping_inventories(self):
        self.finish("a", "open")
        self.finish("b")
        self.review("b", [decision("a"), decision("backlog", "obsolete"), decision("later")])

        server.update_task(self.sid, "a", "running", assignment=MAIN, retry=True,
                           task_instance_id=self.iid)
        new_attempt = self.raw()["nodes"]["a"]["attempt_id"]
        server.update_task(self.sid, "a", "finished", outcome="open", attempt_id=new_attempt,
                           summary="New result", evidence=["fixture://new-result"],
                           task_instance_id=self.iid)
        self.review("a", [decision("a"), decision("backlog", "continue"), decision("later")])

        decisions = server._current_stage_inventory_decisions(self.raw())
        self.assertEqual(decisions["backlog"]["decision"], "continue")

    def test_accepted_continue_reuse_retains_original_and_source_stage_attempt(self):
        self.finish("a"); self.finish("b")
        self.review("a", [decision("backlog")], ["a", "backlog"])
        original = self.record("a")
        reference = {"inventory_id": original["inventory_id"], "task_id": "backlog"}
        self.review("b", [], ["b", "backlog"], reuse=[reference])
        record = self.record("b")
        self.assertEqual(record["decisions"], original["decisions"])
        self.assertEqual(record["reused_decisions"], [{**reference, "stage_task_id": "a", "stage_attempt_id": original["attempt_id"]}])
        self.assertEqual(self.record("a"), original)
        queried = server.get_progress(self.sid, view="records", task_id="b", record_kind="stage_inventories",
                                      task_instance_id=self.iid)["structuredContent"]["progress"]["records"]
        self.assertEqual(queried[-1]["inventory_id"], record["inventory_id"])

    def assert_disposition_requires_new_decision(self, disposition):
        self.finish("a"); self.finish("b")
        self.review("a", [decision("backlog", disposition)], ["a", "backlog"])
        reference = {"inventory_id": self.record("a")["inventory_id"], "task_id": "backlog"}
        self.rejected(lambda: self.review("b", [], ["b", "backlog"], reuse=[reference]))
        self.review("b", [decision("backlog", disposition)], ["b", "backlog"])
        self.assertEqual(self.record("b")["reused_decisions"], [])

    def test_covered_requires_explicit_new_decision(self):
        self.assert_disposition_requires_new_decision("covered")

    def test_obsolete_requires_explicit_new_decision(self):
        self.assert_disposition_requires_new_decision("obsolete")

    def test_other_inventory_logs_and_unrelated_scope_writes_do_not_stale_binding(self):
        self.finish("a"); self.finish("b")
        a_binding = self.binding("a", ["a", "backlog"])
        b_binding = self.binding("b", ["b", "backlog"])
        old_revision = self.raw()["state_revision"]
        self.review("b", [decision("backlog")], ["b", "backlog"], b_binding)
        server.update_task(self.sid, "later", "running", assignment=MAIN, task_instance_id=self.iid)
        self.assertGreater(self.raw()["state_revision"], old_revision)
        self.assertEqual(self.binding("a", ["a", "backlog"]), a_binding)
        self.review("a", [decision("backlog")], ["a", "backlog"], a_binding, expected_state_revision=old_revision)

    def test_inventory_log_alone_does_not_stale_global_binding(self):
        self.finish("a"); self.finish("b")
        binding = self.binding("a")
        self.review("b", [decision("backlog")], ["b", "backlog"])
        self.assertEqual(self.binding("a"), binding)
        self.review("a", [decision("backlog"), decision("later")], binding=binding)

    def test_wire_routes_scoped_fingerprint_without_legacy_revision(self):
        self.finish("a")

        def rpc(name, **args):
            result = json.loads(server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": {"session_id": self.sid, "task_instance_id": self.iid, **args}}}))
            self.assertNotIn("error", result)
            return result["result"]

        scope = ["a", "backlog"]
        query = rpc("get_progress", view="tasks", task_ids=["a"], scope_task_ids=scope)
        binding = query["structuredContent"]["progress"]["nodes"][0]["stage_inventory_binding"]
        arguments = {"task_id": "a", "source_fingerprint": binding["source_fingerprint"],
                     "scope_task_ids": scope, "decisions": [decision("backlog")]}
        before = server._session_file(self.sid).read_bytes()
        missing = rpc("review_stage_inventory", **arguments)
        self.assertTrue(missing["isError"])
        self.assertIn("缺少阶段盘点 attempt_id", missing["content"][0]["text"])
        self.assertIn("stage_inventory_binding.attempt_id", missing["content"][0]["text"])
        stale = rpc("review_stage_inventory", attempt_id="stale-attempt", **arguments)
        self.assertTrue(stale["isError"])
        self.assertIn("已过期或无需盘点", stale["content"][0]["text"])
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

        result = rpc("review_stage_inventory", attempt_id=binding["attempt_id"], **arguments)
        self.assertFalse(result.get("isError"), result)
        receipt = result["structuredContent"]
        self.assertTrue(receipt["receipt"])
        self.assertNotIn("progress", receipt)

    def test_parallel_instance_write_does_not_stale_source_binding(self):
        self.finish("a")
        binding = self.binding("a", ["a", "backlog"])
        server.route_request(self.sid, self.iid, "peer", "Independent fixture", "Peer goal", "parallel",
                             "Disjoint fixture", ["Check peer"])
        peer = self.raw()["session_work_state"]["requests"][0]["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "peer_leaf", "title": "Peer work", "assignment": SUB}], task_instance_id=peer)
        self.assertEqual(self.binding("a", ["a", "backlog"]), binding)
        self.review("a", [decision("backlog")], ["a", "backlog"], binding)

    def test_target_attempt_and_result_changes_invalidate_binding_and_reuse(self):
        self.finish("a"); self.finish("b")
        self.review("a", [decision("backlog")], ["a", "backlog"])
        original = self.record("a")
        reference = {"inventory_id": original["inventory_id"], "task_id": "backlog"}
        old = self.binding("b", ["b", "backlog"])
        server.update_task(self.sid, "backlog", "running", assignment=MAIN, task_instance_id=self.iid)
        self.rejected(lambda: self.review("b", [decision("backlog")], ["b", "backlog"], old))
        self.rejected(lambda: self.review("b", [], ["b", "backlog"], reuse=[reference]))
        running = self.binding("b", ["b", "backlog"])
        aid = self.raw()["nodes"]["backlog"]["attempt_id"]
        server.update_task(self.sid, "backlog", "finished", outcome="open", summary="Different result", attempt_id=aid, task_instance_id=self.iid)
        self.rejected(lambda: self.review("b", [decision("backlog")], ["b", "backlog"], running))
        self.assertEqual(self.record("a"), original)

    def test_source_stage_attempt_change_disallows_old_accepted_decision_reuse(self):
        self.finish("a"); self.finish("b")
        self.review("a", [decision("backlog")], ["a", "backlog"])
        reference = {"inventory_id": self.record("a")["inventory_id"], "task_id": "backlog"}
        server.update_task(self.sid, "a", "running", retry=True, assignment=MAIN, task_instance_id=self.iid)
        self.rejected(lambda: self.review("b", [], ["b", "backlog"], reuse=[reference]))

    def test_scope_expansion_and_scope_choice_changes_invalidate_binding(self):
        self.finish("a")
        server.decompose_task(self.sid, "backlog", [{"id": "old", "title": "Old", "assignment": MAIN}], task_instance_id=self.iid)
        scope = ["a", "backlog"]
        old = self.binding("a", scope)
        server.decompose_task(self.sid, "backlog", [{"id": "new", "title": "New", "assignment": MAIN,
                                                    "addition_reason": "New scoped issue"}], task_instance_id=self.iid)
        self.rejected(lambda: self.review("a", [decision("old"), decision("new")], scope, old))
        current = self.binding("a", scope)
        self.rejected(lambda: self.review("a", [decision("old"), decision("new"), decision("later")], scope + ["later"], current))
        self.review("a", [decision("old"), decision("new")], scope, current)

    def test_final_global_inventory_cannot_inherit_partial_scope_coverage(self):
        self.finish("a"); self.finish("b")
        self.review("a", [decision("backlog")], ["a", "backlog"])
        reference = {"inventory_id": self.record("a")["inventory_id"], "task_id": "backlog"}
        global_binding = self.binding("b")
        self.rejected(lambda: self.review("b", [], binding=global_binding, reuse=[reference]))
        self.review("b", [decision("later")], binding=global_binding, reuse=[reference])
        self.assertIsNone(self.record("b")["scope_task_ids"])
        self.assertEqual({item["task_id"] for item in self.record("b")["decisions"]}, {"backlog", "later"})


if __name__ == "__main__":
    unittest.main()
