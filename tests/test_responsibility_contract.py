"""Responsibility state transitions, using only temporary business sessions."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("responsibility_contract_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory(prefix="responsibility-import-") as isolated:
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "Coordinator performs one bounded integration decision"}
SUB = {"execution_mode": "subagent", "model": "fixture-model", "reasoning_effort": "high",
       "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Inspect isolated fixture and report evidence."}


class ResponsibilityContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="responsibility-contract-")
        server.SESSION_DIR = Path(self.temp.name)
        self.new()

    def tearDown(self):
        self.temp.cleanup()

    def new(self, tasks=None):
        result = server.init_task_network("Responsibilities", tasks=tasks or [
            {"id": "work", "title": "Delegated work", "assignment": {"execution_mode": "subagent"}},
            {"id": "other", "title": "Coordinator action", "assignment": {"execution_mode": "main_session"}},
        ])["structuredContent"]
        self.sid = result["session_id"]
        self.instance = result["progress"]["task_instance_id"]

    def progress(self):
        return server.get_progress_view(self.sid)["structuredContent"]["progress"]

    def node(self, task_id):
        return next(node for node in server.get_progress_view(self.sid, include_history=True)["structuredContent"]["progress"]["nodes"] if node["id"] == task_id)

    def update(self, task_id, status, **kwargs):
        return server.update_task(self.sid, task_id, status, task_instance_id=self.instance, **kwargs)

    def begin(self, task_id="work", assignment=SUB, **kwargs):
        self.update(task_id, "running", assignment=assignment, **kwargs)
        return self.node(task_id)["attempt_id"]

    def bind(self, task_id="work", agent_name="fixture-agent", attempt_id=None):
        return server.bind_task_agent(self.sid, task_id, attempt_id or self.node(task_id)["attempt_id"],
                                      agent_name, self.instance)

    def submit(self, task_id="work", outcome="resolved"):
        self.bind(task_id)
        self.update(task_id, "finished", outcome=outcome, attempt_id=self.node(task_id)["attempt_id"],
                    summary="Executor result", evidence=["fixture://execution"])

    def review(self, task_id="work", decision="accept", **kwargs):
        values = {"attempt_id": self.node(task_id)["attempt_id"], "decision": decision,
                  "summary": "Coordinator independently checked output", "evidence": ["fixture://review"],
                  "reviewer": {"execution_mode": "main_session"}, "task_instance_id": self.instance}
        values.update(kwargs)
        return server.review_task(self.sid, task_id, **values)

    def reject_unchanged(self, action):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            action()
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def rewrite(self, change):
        path = server._session_file(self.sid)
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    def finish_main(self, task_id="other"):
        aid = self.begin(task_id, MAIN)
        self.update(task_id, "finished", outcome="resolved", attempt_id=aid, summary="Coordinator result")

    def test_planned_executor_cannot_be_silently_replaced_in_either_direction(self):
        self.reject_unchanged(lambda: self.begin("work", MAIN))
        self.reject_unchanged(lambda: self.begin("other", SUB))
        aid = self.begin()
        node = self.node("work")
        self.assertEqual(node["planned_assignment"]["execution_mode"], "subagent")
        self.assertEqual(node["attempts"][0]["planned_assignment"], node["planned_assignment"])
        self.assertEqual(node["attempt_id"], aid)

    def test_explicit_pending_reassignment_requires_main_reason_and_retains_history(self):
        for reason, reviewer in (("", MAIN), ("Coordinator takes over", SUB)):
            self.reject_unchanged(lambda: server.reassign_task(self.sid, "work", MAIN, reason, reviewer, self.instance))
        server.reassign_task(self.sid, "work", MAIN, "A bounded final integration decision replaces delegated inspection", MAIN, self.instance)
        node = self.node("work")
        self.assertEqual(node["reassignment_history"][0]["previous_assignment"]["execution_mode"], "subagent")
        self.assertEqual(node["reassignment_history"][0]["reviewer"]["metadata_provenance"]["verification"], "not_verified_by_codex_host")
        self.begin("work", MAIN)
        self.reject_unchanged(lambda: server.reassign_task(self.sid, "work", SUB, "Too late", MAIN, self.instance))

    def test_main_leaf_requires_reason_and_complex_main_requires_specific_exception(self):
        self.reject_unchanged(lambda: self.begin("other", {"execution_mode": "main_session"}))
        self.begin("other", MAIN)
        with self.assertRaises(server.ProgressError):
            server.init_task_network("Complex plan", tasks=[
                {"id": "a", "title": "A", "assignment": {"execution_mode": "main_session", "complexity": "complex", "self_execution_reason": "需要集成"}},
                {"id": "b", "title": "B", "assignment": SUB}])
        sid = server.init_task_network("Atomic", atomic_reason="One short isolated lookup")["structuredContent"]["session_id"]
        server.update_task(sid, "root", "running", assignment={"execution_mode": "main_session"})

    def test_dispatch_fields_are_required_and_binding_does_not_mutate_execution_snapshot(self):
        for field in ("model", "reasoning_effort", "tool_identifier", "dispatch_text"):
            incomplete = {key: value for key, value in SUB.items() if key != field}
            self.reject_unchanged(lambda: self.begin(assignment=incomplete))
        self.begin()
        before = deepcopy(self.node("work")["assignment"])
        self.bind()
        self.assertEqual(self.node("work")["assignment"], before)
        self.assertEqual(self.node("work")["dispatch"]["agent_name"], "fixture-agent")
        self.assertEqual(self.node("work")["attempts"][-1]["dispatch"]["agent_name"], "fixture-agent")
        revision = self.progress()["state_revision"]
        self.bind()
        self.assertEqual(self.progress()["state_revision"], revision)
        self.reject_unchanged(lambda: self.bind(agent_name="different-agent"))
        self.reject_unchanged(lambda: self.bind(attempt_id="stale"))

    def test_known_followup_agent_can_bind_immediately_and_fake_provenance_is_ignored(self):
        self.begin(assignment={**SUB, "tool_identifier": "collaboration.followup_task", "agent_name": "known-returned-agent", "metadata_provenance": {"verification": "verified"}})
        node = self.node("work")
        self.assertEqual(node["dispatch"]["status"], "bound")
        self.assertEqual(node["assignment"]["metadata_provenance"]["verification"], "not_verified_by_codex_host")

    def test_finish_requires_binding_but_failed_dispatch_cleanup_does_not(self):
        aid = self.begin()
        self.reject_unchanged(lambda: self.update("work", "finished", outcome="resolved", attempt_id=aid))
        self.update("work", "failed", attempt_id=aid, summary="Actual spawn failed")
        self.assertEqual(self.node("work")["execution_status"], "failed")
        self.assertIsNone(self.node("work")["submission"])

    def test_pending_main_and_delegated_leaves_cannot_finish_either_outcome_without_attempt(self):
        for task_id, role in (("work", SUB), ("other", MAIN)):
            for outcome in ("open", "resolved"):
                for assignment in (None, role):
                    with self.subTest(task_id=task_id, outcome=outcome, assignment_supplied=assignment is not None):
                        revision = self.progress()["state_revision"]
                        self.reject_unchanged(lambda: self.update(task_id, "finished", outcome=outcome,
                            assignment=assignment, summary="Unstarted result", evidence=["fixture://unstarted"]))
                        self.assertEqual(self.progress()["state_revision"], revision)
                        self.assertEqual(self.node(task_id)["execution_status"], "pending")
                        self.assertIsNone(self.node(task_id)["attempt_id"])

    def test_started_main_leaf_can_finish_open_without_subagent_review(self):
        aid = self.begin("other", MAIN)
        self.update("other", "finished", outcome="open", attempt_id=aid, summary="Coordinator operation remains unresolved")
        node = self.node("other")
        self.assertEqual(node["outcome"], "open")
        self.assertEqual(node["execution_status"], "finished")
        self.assertIsNone(node["submission"])

    def test_pending_failure_and_cancellation_keep_existing_scheduling_cleanup_semantics(self):
        for status in ("failed", "cancelled"):
            with self.subTest(status=status):
                self.new([
                    {"id": "work", "title": "Unstarted delegated producer", "assignment": SUB},
                    {"id": "ended", "title": "Cleanup-aware consumer", "depends_on": ["work"],
                     "dependency_policy": "finished", "assignment": MAIN}])
                self.update("work", status, summary="Scheduling cleanup before actual dispatch")
                node = self.node("work")
                self.assertEqual(node["execution_status"], status)
                self.assertIsNone(node["attempt_id"])
                self.assertIsNone(node["submission"])
                self.assertIsNone(node["review_status"])
                self.assertEqual(self.progress()["ready_tasks"], ["ended"])

    def test_public_mcp_handler_rejects_unstarted_open_and_preserves_bound_open_review_path(self):
        self.new([
            {"id": "work", "title": "Producer", "assignment": SUB},
            {"id": "strict", "title": "Strict consumer", "depends_on": ["work"], "assignment": MAIN},
            {"id": "ended", "title": "Ended consumer", "depends_on": ["work"], "dependency_policy": "finished", "assignment": MAIN}])
        def rpc(name, arguments):
            response = json.loads(server.handle({"jsonrpc": "2.0", "id": "pending-open-regression",
                "method": "tools/call", "params": {"name": name, "arguments": {
                    "session_id": self.sid, "task_instance_id": self.instance, **arguments}}}))
            self.assertEqual(response["id"], "pending-open-regression")
            self.assertNotIn("error", response)
            return response["result"]
        for outcome in ("open", "resolved"):
            before = server._session_file(self.sid).read_bytes()
            revision = self.progress()["state_revision"]
            rejected = rpc("update_task", {"task_id": "work", "execution_status": "finished",
                                          "outcome": outcome, "summary": "No execution attempt"})
            self.assertTrue(rejected["isError"])
            self.assertIn("running", rejected["content"][0]["text"])
            self.assertEqual(server._session_file(self.sid).read_bytes(), before)
            self.assertEqual(self.progress()["state_revision"], revision)
            self.assertEqual(self.progress()["ready_tasks"], ["work"])
        started = rpc("update_task", {"task_id": "work", "execution_status": "running", "assignment": SUB})
        self.assertFalse(started.get("isError"))
        aid = self.node("work")["attempt_id"]
        self.assertFalse(rpc("bind_task_agent", {"task_id": "work", "attempt_id": aid, "agent_name": "mcp-open-agent"}).get("isError"))
        submitted = rpc("update_task", {"task_id": "work", "execution_status": "finished", "outcome": "open",
            "attempt_id": aid, "summary": "Worker reports remaining work", "evidence": ["fixture://open-submission"]})
        self.assertFalse(submitted.get("isError"))
        self.assertEqual(self.node("work")["review_status"], "pending")
        self.assertIsNone(self.node("work")["outcome"])
        self.assertEqual(self.progress()["ready_tasks"], [])
        reviewed = rpc("review_task", {"task_id": "work", "attempt_id": aid, "decision": "accept",
            "summary": "Coordinator confirms unresolved output", "evidence": ["fixture://open-review"], "reviewer": MAIN})
        self.assertFalse(reviewed.get("isError"))
        self.assertEqual(self.node("work")["outcome"], "open")
        self.assertEqual(self.node("work")["review_status"], "accepted")
        self.assertEqual(self.progress()["ready_tasks"], ["ended"])

    def test_submission_blocks_both_dependency_policies_until_review_and_preserves_execution(self):
        self.new([
            {"id": "work", "title": "Producer", "assignment": SUB},
            {"id": "strict", "title": "Strict", "depends_on": ["work"], "assignment": MAIN},
            {"id": "ended", "title": "Ended", "depends_on": ["work"], "dependency_policy": "finished", "assignment": MAIN}])
        self.begin(); self.submit()
        node = self.node("work")
        self.assertEqual(node["status"], "awaiting_review")
        self.assertIsNone(node["outcome"])
        self.assertEqual(self.progress()["ready_tasks"], [])
        self.assertEqual(self.progress()["status"], "awaiting_review")
        self.assertTrue(any("review_task(work)" in action for action in self.progress()["next_actions"]))
        submission = deepcopy(node["submission"])
        self.review()
        node = self.node("work")
        self.assertEqual(node["submission"], submission)
        self.assertEqual(node["summary"], "Executor result")
        self.assertEqual(node["evidence"], ["fixture://execution"])
        self.assertEqual(node["review"]["evidence"], ["fixture://review"])
        self.assertEqual(set(self.progress()["ready_tasks"]), {"strict", "ended"})

    def test_pending_submission_cannot_be_overwritten_retried_replanned_or_cleared(self):
        self.begin(); self.submit()
        aid = self.node("work")["attempt_id"]
        actions = [lambda: self.update("work", "cancelled", attempt_id=aid),
                   lambda: self.begin(retry=True),
                   lambda: server.decompose_task(self.sid, "work", [{"id": "extra", "title": "Extra", "assignment": MAIN}], self.instance),
                   lambda: server.rebuild_task_network(self.sid, "Skip pending review", task_instance_id=self.instance),
                   lambda: server.clear_progress(self.sid),
                   lambda: server.init_task_network("Replacement", self.sid, atomic_reason="New operation"),
                   lambda: server.init_progress(["Legacy replacement"], session_id=self.sid)]
        for action in actions:
            self.reject_unchanged(action)

    def test_review_requires_main_summary_evidence_and_current_pending_attempt(self):
        self.begin(); self.submit()
        for override in ({"reviewer": SUB}, {"summary": ""}, {"evidence": []}, {"attempt_id": "old"}, {"decision": "upgrade"}):
            self.reject_unchanged(lambda: self.review(**override))
        self.review()
        self.reject_unchanged(lambda: self.review(decision="reject"))

    def test_review_accept_open_never_upgrades_and_reject_retains_original_resolved_submission(self):
        self.begin(); self.submit(outcome="open"); self.review()
        self.assertEqual(self.node("work")["outcome"], "open")
        self.begin(retry=True); self.submit(); self.review(decision="reject")
        node = self.node("work")
        self.assertEqual(node["outcome"], "open")
        self.assertEqual(node["submission"]["outcome"], "resolved")
        self.assertEqual(node["review_status"], "rejected")

    def test_retry_preserves_submissions_reviews_dispatch_and_rejects_stale_reviews(self):
        self.begin(); self.submit(); self.review(decision="reject")
        previous = deepcopy(self.node("work"))
        self.begin(retry=True)
        node = self.node("work")
        self.assertIsNone(node["review"])
        self.assertIsNone(node["submission"])
        for key in ("submission", "review", "dispatch"):
            self.assertEqual(node["attempts"][0][key], previous[key])
        self.reject_unchanged(lambda: self.review(attempt_id=previous["attempt_id"]))

    def test_parent_and_root_synthesis_require_main_role_evidence_and_resolved_children(self):
        self.begin(); self.submit()
        self.finish_main()
        self.reject_unchanged(lambda: self.update("root", "finished", outcome="resolved", assignment=MAIN, summary="Premature", evidence=["fixture://all"]))
        self.review()
        for values in ({"assignment": SUB, "summary": "Bad role", "evidence": ["fixture://all"]},
                       {"assignment": MAIN, "summary": "", "evidence": ["fixture://all"]},
                       {"assignment": MAIN, "summary": "No evidence", "evidence": []}):
            self.reject_unchanged(lambda: self.update("root", "finished", outcome="resolved", **values))
        self.update("root", "finished", outcome="resolved", assignment=MAIN, summary="All results independently checked", evidence=["fixture://all"])
        self.assertEqual(self.progress()["status"], "completed")
        self.assertEqual(self.node("root")["attempts"][-1]["kind"], "synthesis")

    def test_unresolved_children_cannot_be_silently_synthesized_as_resolved(self):
        aid = self.begin(); self.update("work", "failed", attempt_id=aid, summary="Dispatch failed")
        self.finish_main()
        self.reject_unchanged(lambda: self.update("root", "finished", outcome="resolved", assignment=MAIN, summary="Pretend done", evidence=["fixture://bad"]))
        self.update("root", "finished", outcome="open", assignment=MAIN, summary="Worker failed; user goal remains unresolved")
        self.assertEqual(self.progress()["status"], "waiting_synthesis")
        self.assertEqual(self.node("root")["outcome"], "open")
        self.assertTrue(any("review_task_closure" in action for action in self.progress()["next_actions"]))

    def test_accepted_producer_retry_invalidates_consumer_and_root_reviews_without_losing_evidence(self):
        self.new([{ "id": "work", "title": "Producer", "assignment": SUB},
                  {"id": "consumer", "title": "Consumer", "depends_on": ["work"], "assignment": SUB}])
        for task_id in ("work", "consumer"):
            self.begin(task_id); self.submit(task_id); self.review(task_id)
        self.update("root", "finished", outcome="resolved", assignment=MAIN, summary="Checked", evidence=["fixture://synthesis"])
        old_consumer = deepcopy(self.node("consumer"))
        self.begin(retry=True)
        node = self.node("consumer")
        self.assertEqual(node["execution_status"], "pending")
        self.assertIsNone(node["review_status"])
        self.assertEqual(node["invalidated_results"][0]["review"], old_consumer["review"])
        self.assertEqual(node["attempts"][0]["submission"], old_consumer["submission"])
        self.assertIsNone(self.node("root")["outcome"])

    def test_retry_cannot_discard_a_pending_consumer_submission(self):
        self.new([{ "id": "work", "title": "Producer", "assignment": SUB},
                  {"id": "consumer", "title": "Consumer", "depends_on": ["work"], "assignment": SUB}])
        self.begin(); self.submit(); self.review()
        self.begin("consumer"); self.submit("consumer")
        self.reject_unchanged(lambda: self.begin(retry=True))
        self.review("consumer")
        self.begin(retry=True)
        self.assertEqual(self.node("consumer")["execution_status"], "pending")
        self.assertEqual(self.node("consumer")["attempts"][0]["review"]["decision"], "accept")

    def test_splitting_subagent_attempt_preserves_plan_but_group_synthesis_is_main_only(self):
        aid = self.begin()
        server.decompose_task(self.sid, "work", [{"id": "child", "title": "Remaining work", "assignment": SUB}],
                              task_instance_id=self.instance, attempt_id=aid)
        self.assertEqual(self.node("work")["planned_assignment"]["execution_mode"], "subagent")
        self.assertEqual(self.node("work")["attempts"][0]["execution_status"], "cancelled")
        self.begin("child"); self.submit("child"); self.review("child")
        self.reject_unchanged(lambda: self.review("work", attempt_id=aid))
        self.reject_unchanged(lambda: self.update("work", "finished", outcome="resolved", assignment=SUB,
                                                summary="Wrong synthesis role", evidence=["fixture://bad"]))
        self.update("work", "finished", outcome="resolved", assignment=MAIN,
                    summary="Coordinator accepted remaining child", evidence=["fixture://child-review"])
        self.assertEqual(self.node("work")["attempts"][-1]["kind"], "synthesis")
        self.assertEqual(self.node("work")["assignment"]["execution_mode"], "main_session")

    def test_read_legacy_completed_data_does_not_invent_plan_or_acceptance(self):
        self.finish_main()
        def old(data):
            node = data["nodes"]["other"]
            for key in ("planned_assignment", "responsibility_version", "review_status", "review", "submission", "dispatch"):
                node.pop(key, None)
        self.rewrite(old)
        before = server._session_file(self.sid).read_bytes()
        node = self.node("other")
        self.assertTrue(node["responsibility_legacy"])
        self.assertIsNone(node["planned_assignment"])
        self.assertEqual(node["review_status"], "legacy_unrecorded")
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)
        self.reject_unchanged(lambda: self.begin("other", MAIN, retry=True))

    def test_legacy_pending_requires_explicit_plan_and_legacy_running_submission_still_needs_review(self):
        def old(data):
            node = data["nodes"]["work"]
            node.pop("planned_assignment", None)
            node.pop("responsibility_version", None)
        self.rewrite(old)
        self.reject_unchanged(lambda: self.begin())
        self.assertTrue(any("reassign_task(work)" in action for action in self.progress()["next_actions"]))
        server.reassign_task(self.sid, "work", SUB, "Confirm old pending work delegation", MAIN, self.instance)
        self.begin()
        def inflight(data):
            node = data["nodes"]["work"]
            for key in ("responsibility_version", "dispatch", "attempt_id", "attempts"):
                node.pop(key, None)
        self.rewrite(inflight)
        self.update("work", "finished", outcome="resolved", summary="Legacy output", evidence=["fixture://legacy"])
        node = self.node("work")
        self.assertTrue(node["dispatch"]["legacy"])
        self.assertEqual(node["review_status"], "pending")
        self.assertTrue(node["attempt_id"])
        self.review()
        self.assertEqual(self.node("work")["outcome"], "resolved")

    def test_ready_text_and_next_actions_distinguish_roles_binding_review_synthesis_and_completion(self):
        text = server.get_ready_tasks(self.sid)["content"][0]["text"]
        self.assertIn("待派发子智能体", text)
        self.assertIn("主会话自执行", text)
        self.begin()
        self.assertTrue(any("bind_task_agent(work)" in action for action in self.progress()["next_actions"]))
        self.bind()
        self.assertTrue(any("等待任务 work" in action for action in self.progress()["next_actions"]))
        self.submit(); self.review(); self.finish_main()
        self.assertTrue(any("综合验收 root" in action for action in self.progress()["next_actions"]))
        self.update("root", "finished", outcome="resolved", assignment=MAIN, summary="Checked", evidence=["fixture://all"])
        self.assertEqual(self.progress()["next_actions"], ["任务已完成：报告验收结果。"])

    def test_new_mcp_tools_have_schemas_and_handle_full_submission_review_flow(self):
        names = {tool["name"] for tool in server.TOOLS}
        self.assertTrue({"reassign_task", "bind_task_agent", "review_task"} <= names)
        def rpc(name, arguments):
            result = json.loads(server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": {"session_id": self.sid, "task_instance_id": self.instance, **arguments}}}))
            self.assertNotIn("error", result)
            return result["result"]
        rejected = rpc("reassign_task", {"task_id": "work", "assignment": MAIN, "reason": "Bad actor", "reviewer": SUB})
        self.assertTrue(rejected["isError"])
        started = rpc("update_task", {"task_id": "work", "execution_status": "running", "assignment": SUB})
        self.assertFalse(started.get("isError"))
        aid = self.node("work")["attempt_id"]
        self.assertFalse(rpc("bind_task_agent", {"task_id": "work", "attempt_id": aid, "agent_name": "rpc-agent"}).get("isError"))
        rpc("update_task", {"task_id": "work", "attempt_id": aid, "execution_status": "finished", "outcome": "resolved", "summary": "RPC output", "evidence": ["fixture://rpc-output"]})
        reviewed = rpc("review_task", {"task_id": "work", "attempt_id": aid, "decision": "accept", "summary": "RPC verified", "evidence": ["fixture://rpc-review"], "reviewer": MAIN})
        self.assertFalse(reviewed.get("isError"))
        self.assertEqual(self.node("work")["review_status"], "accepted")


if __name__ == "__main__":
    unittest.main()
