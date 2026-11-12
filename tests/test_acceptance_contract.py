"""Optional acceptance contracts and immutable artifact/version checks."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("acceptance_contract_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory(prefix="acceptance-import-") as isolated:
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "One bounded synthesis decision"}
SUB = {"execution_mode": "subagent", "model": "fixture-model", "reasoning_effort": "high",
       "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Inspect the exact versioned fixture."}
CONTRACT = {"version": 1, "criteria": [
    {"id": "behavior", "text": "Required behavior is correct", "method": "command"},
    {"id": "docs", "text": "Documentation is present", "method": "inspection", "required": False},
]}
PASS = [{"criterion_id": "behavior", "verdict": "pass", "evidence": ["test://behavior"]}]


class AcceptanceContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="acceptance-contract-")
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        result = server.init_task_network("Acceptance fixture", tasks=[
            {"id": "work", "title": "Contracted work", "assignment": SUB, "acceptance": CONTRACT},
            {"id": "main", "title": "Main session work", "assignment": MAIN},
        ])["structuredContent"]
        self.sid = result["session_id"]
        self.iid = result["progress"]["task_instance_id"]

    def node(self, task_id):
        p = server.get_progress_view(self.sid, include_history=True, task_instance_id=self.iid)["structuredContent"]["progress"]
        return next(node for node in p["nodes"] if node["id"] == task_id)

    def update(self, task_id, status, **values):
        return server.update_task(self.sid, task_id, status, task_instance_id=self.iid, **values)

    def begin(self, task_id="work", assignment=SUB):
        self.update(task_id, "running", assignment=assignment)
        return self.node(task_id)["attempt_id"]

    def bind(self, task_id, name):
        return server.bind_task_agent(self.sid, task_id, self.node(task_id)["attempt_id"], name, self.iid)

    def submit(self, task_id="work", outcome="resolved", **extra):
        attempt = self.begin(task_id)
        self.bind(task_id, task_id + "-owner")
        self.update(task_id, "finished", outcome=outcome, attempt_id=attempt,
                    summary="Worker submission", evidence=["test://submission"], **extra)
        return attempt

    def review(self, task_id="work", decision="accept", **extra):
        return server.review_task(self.sid, task_id, self.node(task_id)["attempt_id"], decision,
            "Main session checked the submission", ["test://review"], MAIN,
            task_instance_id=self.iid, **extra)

    def unchanged_rejection(self, action):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            action()
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def test_contract_is_validated_and_bound_to_attempt_without_weakening_legacy_tasks(self):
        for invalid in (
            {"version": True, "criteria": CONTRACT["criteria"]},
            {"version": 1, "criteria": []},
            {"version": 1, "criteria": [CONTRACT["criteria"][0], CONTRACT["criteria"][0]]},
            {"version": 1, "criteria": [{"id": "x", "text": "valid", "method": "unknown"}]},
        ):
            self.unchanged_rejection(lambda invalid=invalid: server.init_task_network(
                "Invalid contract", tasks=[{"id": "x", "title": "X", "assignment": SUB, "acceptance": invalid},
                                            {"id": "y", "title": "Y", "assignment": MAIN}]))
        attempt = self.submit(acceptance_checks=[{"criterion_id": "behavior", "verdict": "pass", "evidence": ["test://self"]}])
        node = self.node("work")
        frozen = node["attempts"][-1]
        self.assertEqual(frozen["attempt_id"], attempt)
        self.assertEqual(frozen["acceptance"], server._normalize_acceptance(CONTRACT))
        self.assertEqual(frozen["acceptance_fingerprint"], server._acceptance_fingerprint(server._normalize_acceptance(CONTRACT)))
        self.assertEqual(node["submission"]["acceptance_checks"][0]["evidence"], ["test://self"])
        tree = server.get_progress(self.sid, view="tree", task_id="root", task_instance_id=self.iid)["structuredContent"]["progress"]
        self.assertEqual(tree["nodes"][1]["acceptance_summary"]["criterion_count"], 2)
        self.assertEqual(tree["nodes"][1]["acceptance_summary"]["required_pass_count"], 1)
        self.assertEqual(tree["nodes"][1]["acceptance_summary"]["required_count"], 1)
        self.assertNotIn("criteria", tree["nodes"][1].get("acceptance", {}))

    def test_subagent_resolved_requires_independent_required_checks_and_preserves_both_records(self):
        self.submit(acceptance_checks=[{"criterion_id": "behavior", "verdict": "pass", "evidence": ["test://self"]}])
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError) as missing:
            self.review()
        self.assertEqual(missing.exception.error_code, "acceptance_checks_required")
        self.assertIn("behavior", str(missing.exception))
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)
        self.unchanged_rejection(lambda: self.review(acceptance_checks=[
            {"criterion_id": "behavior", "verdict": "fail", "evidence": ["test://failed"]}]))
        reviewer_checks = [*PASS, {"criterion_id": "docs", "verdict": "not_run", "note": "Optional criterion"}]
        self.review(acceptance_checks=reviewer_checks)
        node = self.node("work")
        self.assertEqual(node["outcome"], "resolved")
        self.assertEqual(node["submission"]["acceptance_checks"][0]["evidence"], ["test://self"])
        self.assertEqual(node["review"]["acceptance_checks"], server._normalize_acceptance_checks(reviewer_checks, CONTRACT))

    def test_open_acceptance_and_reject_require_a_recorded_finding(self):
        self.submit(outcome="open")
        self.unchanged_rejection(lambda: self.review(acceptance_checks=PASS))
        findings = [{"criterion_id": "behavior", "verdict": "fail", "note": "Expected result absent", "evidence": ["test://missing"]}]
        self.review(acceptance_checks=findings)
        self.assertEqual(self.node("work")["outcome"], "open")
        self.assertEqual(self.node("work")["review_status"], "accepted")

    def test_reject_without_findings_names_the_field_and_current_criteria(self):
        self.submit(acceptance_checks=PASS)
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError) as rejected:
            self.review(decision="reject")
        self.assertEqual(rejected.exception.error_code, "review_finding_required")
        self.assertIn("acceptance_checks", str(rejected.exception))
        self.assertIn("behavior", str(rejected.exception))
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)
        self.review(decision="reject", acceptance_checks=[
            {"criterion_id": "behavior", "verdict": "not_run", "note": "The candidate has changed."},
        ])
        self.assertEqual(self.node("work")["review_status"], "rejected")

    def test_main_open_without_findings_names_the_field_and_frozen_criteria(self):
        receipt = server.init_task_network("Open result fixture", atomic_reason="One result",
                                          root_acceptance=CONTRACT)["structuredContent"]
        sid, iid, root_id = receipt["session_id"], receipt["progress"]["task_instance_id"], receipt["progress"]["root_id"]
        server.update_task(sid, root_id, "running", assignment=MAIN, task_instance_id=iid)
        aid = server._read(sid)["nodes"][root_id]["attempt_id"]
        args = {"session_id": sid, "task_instance_id": iid, "task_id": root_id, "attempt_id": aid,
                "execution_status": "finished", "outcome": "open", "summary": "Validation is pending"}
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "update_task", "arguments": args}}
        before = server._session_file(sid).read_bytes()
        rejected = json.loads(server.handle(request))["result"]
        self.assertEqual(rejected.get("error_code"), "open_finding_required")
        self.assertIn("acceptance_checks", rejected["content"][0]["text"])
        self.assertIn("behavior", rejected["content"][0]["text"])
        self.assertEqual(server._session_file(sid).read_bytes(), before)

        args["acceptance_checks"] = [{"criterion_id": "behavior", "verdict": "not_run", "note": "Awaiting validation"}]
        accepted = json.loads(server.handle(request))["result"]
        self.assertFalse(accepted.get("isError"), accepted)
        self.assertEqual(accepted["structuredContent"]["affected"]["outcome"], "open")

    def test_main_session_root_contract_requires_checks_and_atomic_init_supports_root_acceptance(self):
        contract = {"version": 1, "criteria": [{"id": "delivery", "text": "Delivery was checked", "method": "inspection"}]}
        p = server.init_task_network("Atomic acceptance", atomic_reason="One self-contained result",
            root_acceptance=contract)["structuredContent"]["progress"]
        sid, iid, root_id = p["session_id"], p["task_instance_id"], p["root_id"]
        server.update_task(sid, root_id, "running", assignment=MAIN, task_instance_id=iid)
        aid = next(node for node in server.get_progress_view(sid, include_history=True, task_instance_id=iid)["structuredContent"]["progress"]["nodes"]
                   if node["id"] == root_id)["attempt_id"]
        before = server._session_file(sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            server.update_task(sid, root_id, "finished", outcome="resolved", attempt_id=aid,
                summary="Delivered", evidence=["test://delivery"], task_instance_id=iid)
        self.assertEqual(server._session_file(sid).read_bytes(), before)
        server.update_task(sid, root_id, "finished", outcome="resolved", attempt_id=aid,
            summary="Delivered", evidence=["test://delivery"], acceptance_checks=[
                {"criterion_id": "delivery", "verdict": "pass", "evidence": ["test://checked"]}], task_instance_id=iid)
        result = server.get_progress(sid, view="tasks", task_ids=[root_id], task_instance_id=iid)["structuredContent"]["progress"]["nodes"][0]
        self.assertEqual(result["acceptance_checks"][0]["verdict"], "pass")

    def test_root_contract_can_be_revised_only_before_any_descendant_starts(self):
        p = server.get_progress(self.sid, view="overview", task_instance_id=self.iid)["structuredContent"]["progress"]
        revision = p["revision"]
        root_contract = {"version": 1, "criteria": [{"id": "synthesis", "text": "Synthesis checked", "method": "inspection"}]}
        server.revise_task_plan(self.sid, self.iid, "root", "Add final acceptance checklist", p["epoch"], revision,
                                acceptance=root_contract)
        result = server.get_progress(self.sid, view="history", history_kind="plan_revisions", task_instance_id=self.iid)["structuredContent"]["progress"]["records"][0]
        self.assertIsNone(result["before"]["acceptance"])
        self.assertEqual(result["after"]["acceptance"], server._normalize_acceptance(root_contract))
        attempt = self.begin("work")
        current = server.get_progress(self.sid, view="overview", task_instance_id=self.iid)["structuredContent"]["progress"]
        self.unchanged_rejection(lambda: server.revise_task_plan(self.sid, self.iid, "root", "Too late", current["epoch"],
            current["revision"], acceptance=CONTRACT))
        self.assertEqual(self.node("work")["attempt_id"], attempt)

    def test_required_independent_review_cannot_start_as_a_leaf(self):
        plan = server.get_progress(self.sid, view="overview", task_instance_id=self.iid)["structuredContent"]["progress"]
        contract = {"version": 1, "criteria": [
            {"id": "verify", "text": "Check the report", "method": "independent_review"},
        ]}
        server.revise_task_plan(self.sid, self.iid, "work", "Plan the report check",
                                plan["epoch"], plan["revision"], acceptance=contract)
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError) as rejected:
            self.begin()
        self.assertEqual(rejected.exception.error_code, "independent_review_requires_group")
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)
        self.assertIsNone(self.node("work")["attempt_id"])

        plan = server.get_progress(self.sid, view="overview", task_instance_id=self.iid)["structuredContent"]["progress"]
        contract["criteria"][0]["method"] = "inspection"
        server.revise_task_plan(self.sid, self.iid, "work", "Main session inspects the report",
                                plan["epoch"], plan["revision"], acceptance=contract)
        self.assertIsNotNone(self.begin())

    def test_legacy_leaf_review_reports_the_contract_problem_and_allows_rejection(self):
        attempt_id = self.submit(acceptance_checks=PASS)
        data = server._read(self.sid)
        node = data["nodes"]["work"]
        legacy_contract = server._normalize_acceptance(CONTRACT)
        legacy_contract["criteria"][0]["method"] = "independent_review"
        node["acceptance"] = legacy_contract
        node["attempts"][-1]["acceptance"] = legacy_contract
        node["attempts"][-1]["acceptance_fingerprint"] = server._acceptance_fingerprint(legacy_contract)
        original_submission = node["submission"]
        server._write(self.sid, data)

        args = {"session_id": self.sid, "task_instance_id": self.iid, "task_id": "work",
                "attempt_id": attempt_id, "decision": "accept", "summary": "Review the report",
                "evidence": ["test://review"], "reviewer": MAIN, "acceptance_checks": PASS}
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "review_task", "arguments": args}}
        before = server._session_file(self.sid).read_bytes()
        rejected = json.loads(server.handle(request))["result"]
        self.assertEqual(rejected.get("error_code"), "independent_review_requires_group")
        self.assertIn("reject", rejected["content"][0]["text"])
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

        args["decision"] = "reject"
        args["acceptance_checks"] = [{"criterion_id": "behavior", "verdict": "not_run",
                                      "note": "The frozen leaf contract requires a group"}]
        response = json.loads(server.handle(request))["result"]
        self.assertFalse(response.get("isError"), response)
        recovered = self.node("work")
        self.assertEqual(recovered["review_status"], "rejected")
        self.assertEqual(recovered["outcome"], "open")
        self.assertEqual(recovered["submission"], original_submission)

    def test_independent_review_binds_two_agents_to_the_same_frozen_artifact_version(self):
        root_contract = {"version": 1, "criteria": [{"id": "verify", "text": "Independent owner checked final artifact", "method": "independent_review"}]}
        result = server.init_task_network("Independent artifact review", root_acceptance=root_contract, tasks=[
            {"id": "implementation", "title": "Implementation", "assignment": SUB},
            {"id": "verification", "title": "Verification", "assignment": SUB, "depends_on": ["implementation"]},
        ])["structuredContent"]
        sid, iid = result["session_id"], result["progress"]["task_instance_id"]
        artifact = {"id": "bundle", "uri": "artifact://bundle", "version": "sha256:abc"}
        def run(task_id, agent_name, **extra):
            server.update_task(sid, task_id, "running", assignment=SUB, task_instance_id=iid)
            aid = server.get_progress(sid, view="tasks", task_ids=[task_id], task_instance_id=iid)["structuredContent"]["progress"]["nodes"][0]["attempt_id"]
            server.bind_task_agent(sid, task_id, aid, agent_name, iid)
            server.update_task(sid, task_id, "finished", outcome="resolved", attempt_id=aid,
                summary="Task completed", evidence=["test://result"], task_instance_id=iid, **extra)
            server.review_task(sid, task_id, aid, "accept", "Main session accepted", ["test://accepted"], MAIN, iid)
            return aid
        implementation_attempt = run("implementation", "implementer", artifacts=[artifact])
        verification_attempt = run("verification", "verifier", verified_artifacts=[{
            "source_task_id": "implementation", "source_attempt_id": implementation_attempt,
            "artifact_id": "bundle", "version": "sha256:abc"}])
        checks = [{"criterion_id": "verify", "verdict": "pass", "evidence": ["test://independent"],
            "verification_task_id": "verification", "verification_attempt_id": verification_attempt,
            "target_task_id": "implementation", "target_attempt_id": implementation_attempt,
            "checked_artifacts": [{"source_task_id": "implementation", "source_attempt_id": implementation_attempt,
                "artifact_id": "bundle", "version": "sha256:wrong"}]}]
        before = server._session_file(sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            server.update_task(sid, "root", "finished", outcome="resolved", assignment=MAIN,
                summary="Integrated and checked", evidence=["test://integration"], acceptance_checks=checks,
                task_instance_id=iid)
        self.assertEqual(server._session_file(sid).read_bytes(), before)
        checks[0]["checked_artifacts"][0]["version"] = "sha256:abc"
        server.update_task(sid, "root", "finished", outcome="resolved", assignment=MAIN,
            summary="Integrated and checked", evidence=["test://integration"], acceptance_checks=checks,
            task_instance_id=iid)
        self.assertEqual(server.get_progress(sid, view="tasks", task_ids=["root"], task_instance_id=iid)["structuredContent"]["progress"]["nodes"][0]["outcome"], "resolved")


if __name__ == "__main__":
    unittest.main()
