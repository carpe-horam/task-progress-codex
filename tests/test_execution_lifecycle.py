"""Replay lost closeouts and repeated runs using isolated sessions and real workers."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("execution_lifecycle_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as isolated:
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)
MAIN = {"execution_mode": "main_session", "self_execution_reason": "Own isolated execution fixture"}


class ExecutionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-execution-")
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        server.LEGACY_DB_PATH = Path(self.temp.name) / "absent.sqlite3"
        receipt = self.rpc("init_task_network", task_title="Execution fixture", tasks=[
            {"id": "check", "title": "One check result", "assignment": MAIN},
            {"id": "deliver", "title": "Delivery", "depends_on": ["check"], "assignment": MAIN}])["structuredContent"]
        self.sid, self.iid = receipt["session_id"], receipt["task_instance_id"]
        self.aid = self.write("update_task", execution_status="running", assignment=MAIN)["structuredContent"]["attempt_id"]

    def rpc(self, name, **args):
        return json.loads(server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": name, "arguments": args}}))["result"]

    def write(self, name, **args):
        return self.rpc(name, session_id=self.sid, task_instance_id=self.iid, task_id="check", **args)

    def node(self):
        return self.rpc("get_progress", session_id=self.sid, task_instance_id=self.iid,
                        view="tasks", task_ids=["check"])["structuredContent"]["progress"]["nodes"][0]

    def event(self, run="run-1", status="running", **args):
        return self.write("record_task_execution", attempt_id=self.aid, execution_id=run,
                          provider="dagu", status=status, summary="Check execution" if status == "running" else "Check ended " + status,
                          **args)

    def test_failed_receipt_closes_attempt_and_retry_retains_each_run(self):
        self.assertFalse(self.event().get("isError"))
        missing_evidence = self.event(status="failed", exit_code=1)
        self.assertTrue(missing_evidence["isError"])
        self.assertIn("status=failed 的执行回执至少须提供一条非空 evidence",
                      missing_evidence["content"][0]["text"])
        self.assertEqual(self.node()["execution"]["status"], "running")
        failed = self.event(status="failed", exit_code=1, evidence=["fixture://run-1/failure"])
        self.assertEqual(failed["structuredContent"]["affected"]["execution_status"], "failed")
        old = self.node()
        self.assertTrue(old["finished_at"])
        self.assertEqual(old["failure_reason"], "Check ended failed")
        self.assertFalse(self.event(status="failed", exit_code=1, evidence=["fixture://run-1/failure"]).get("isError"))
        self.assertTrue(self.event("run-2").get("isError"))
        started = self.write("update_task", execution_status="running", retry=True, assignment=MAIN)
        self.aid = started["structuredContent"]["attempt_id"]
        self.assertNotEqual(self.aid, old["attempt_id"])
        self.assertTrue(self.event("run-1").get("isError"), "a retry must not reuse the old external run")
        self.assertFalse(self.event("run-2").get("isError"))
        self.event("run-2", "blocked", exit_code=2, evidence=["fixture://run-2/review"])
        node = self.node()
        self.assertEqual((node["execution_status"], node["outcome"]), ("finished", "open"))
        records = self.rpc("get_progress", session_id=self.sid, task_instance_id=self.iid,
                           view="records", task_id="check")["structuredContent"]["progress"]["records"]
        self.assertEqual([a["execution"]["execution_id"] for a in records], ["run-1", "run-2"])
        self.assertEqual([a["execution_status"] for a in records], ["failed", "finished"])

    def test_external_failure_reason_belongs_in_receipt_summary(self):
        self.assertFalse(self.event().get("isError"))
        before = server._session_file(self.sid).read_bytes()
        receipt = dict(attempt_id=self.aid, execution_id="run-1", provider="dagu",
                       status="failed", summary="Validation command exited with code 1",
                       exit_code=1, evidence=["fixture://failure"])
        rejected = self.write("record_task_execution", **receipt, failure_reason="Validation failed")
        self.assertTrue(rejected["isError"])
        self.assertIn("请将外部执行的失败或阻塞原因写入 summary", rejected["content"][0]["text"])
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

        accepted = self.write("record_task_execution", **receipt)
        self.assertFalse(accepted.get("isError"), accepted)
        self.assertEqual(self.node()["failure_reason"], receipt["summary"])

    def test_second_run_and_in_place_retry_are_rejected(self):
        self.event()
        self.assertTrue(self.event("different-run").get("isError"))
        self.assertTrue(self.write("update_task", execution_status="running", retry=True,
                                   attempt_id=self.aid).get("isError"))
        self.assertEqual(self.node()["attempt"], 1)

    def test_late_receipt_cannot_close_new_attempt_and_real_provider_times_are_retained(self):
        self.event()
        old = self.aid
        self.event(status="failed", exit_code=1, evidence=["fixture://old"])
        self.aid = self.write("update_task", execution_status="running", retry=True, assignment=MAIN)["structuredContent"]["attempt_id"]
        result = self.write("record_task_execution", attempt_id=old, execution_id="run-1", provider="dagu",
                            status="failed", summary="Late failure", evidence=["fixture://late"])
        self.assertTrue(result["isError"])
        self.assertEqual(self.node()["execution_status"], "running")
        self.event("run-2", started_at="2026-01-01T01:00:00+00:00")
        self.event("run-2", "failed", finished_at="2026-01-01T01:03:00+00:00", evidence=["fixture://time"], exit_code=1)
        self.assertEqual(self.node()["finished_at"], "2026-01-01T01:03:00+00:00")

    def test_conflicting_terminal_receipt_is_rejected(self):
        self.event()
        self.event(status="blocked", exit_code=2, evidence=["fixture://review"])
        self.assertTrue(self.event(status="failed", exit_code=1, evidence=["fixture://failure"]).get("isError"))
        self.assertEqual(self.node()["outcome"], "open")

    def test_wrong_attempt_has_recovery_and_durable_rejection_without_closing_current(self):
        result = self.write("update_task", attempt_id=str(uuid.uuid4()), execution_status="failed", summary="Old callback")
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["recovery"]["current_attempt_id"], self.aid)
        node = self.node()
        self.assertEqual(node["execution_status"], "running")
        self.assertEqual(node["write_rejection_count"], 1)
        blocked = self.write("update_task", attempt_id=self.aid, execution_status="running", summary="Ignore the failed write")
        self.assertEqual(blocked["structuredContent"]["recovery"]["code"], "closeout_recovery_required")
        self.assertTrue(self.event().get("isError"), "a rejected closeout must be recovered before a new execution")
        self.write("update_task", attempt_id=self.aid, execution_status="failed", summary="Correct closeout")
        records = self.rpc("get_progress", session_id=self.sid, task_instance_id=self.iid, view="records",
                           task_id="check", record_kind="write_rejections")["structuredContent"]["progress"]["records"]
        self.assertTrue(records[0]["recovered_at"])

    def test_long_leaf_cannot_bypass_gate_with_summary_and_terminal_closeout_stays_possible(self):
        data = server._read(self.sid)
        data["nodes"]["check"]["started_at"] = "2020-01-01T00:00:00+00:00"
        data["nodes"]["check"]["summary"] = "拆分复核：无需拆分，因为串行等待门禁。"
        server._write(self.sid, data)
        self.assertTrue(self.node()["split_review_required"])
        result = self.write("update_task", attempt_id=self.aid, execution_status="running", summary="继续工作")
        self.assertEqual(result["structuredContent"]["recovery"]["code"], "split_review_required")
        self.assertTrue(self.write("review_task_split", attempt_id=self.aid, reason="Two outcomes",
                                  remaining_outcomes=["Gate", "Push"], evidence=["fixture://scope"],
                                  summary="No split needed").get("isError"))
        review = self.write("review_task_split", attempt_id=self.aid, reason="Only the existing check exit remains",
                            remaining_outcomes=["Check result"], evidence=["fixture://scope"], valid_for_seconds=60)
        self.assertFalse(review.get("isError"), review)
        self.assertFalse(self.node()["split_review_required"])
        self.assertFalse(self.event().get("isError"))
        self.assertTrue(self.node()["split_review_required"], "a new execution changes the reviewed scope")
        self.event(status="failed", exit_code=1, evidence=["fixture://failure"])
        self.assertEqual(self.node()["execution_status"], "failed")

    def test_split_review_missing_evidence_returns_validation_without_writing(self):
        before = server._session_file(self.sid).read_bytes()
        result = self.write("review_task_split", attempt_id=self.aid, reason="One remaining result",
                            remaining_outcomes=["Check result"], summary="Wait for the current check")
        self.assertTrue(result["isError"])
        self.assertIn("至少一条非空 evidence", result["content"][0]["text"])
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def test_optional_split_review_summary_is_validated_and_persisted(self):
        schema = next(tool["inputSchema"] for tool in server.TOOLS if tool["name"] == "review_task_split")
        self.assertNotIn("summary", schema["required"])
        self.assertEqual(schema["properties"]["summary"]["maxLength"], 12000)
        args = dict(attempt_id=self.aid, reason="One remaining result",
                    remaining_outcomes=["Check result"], evidence=["fixture://scope"])
        for invalid in ("  ", "x" * 12001):
            with self.subTest(length=len(invalid)):
                result = self.write("review_task_split", **args, summary=invalid)
                self.assertTrue(result.get("isError"), result)
        result = self.write("review_task_split", **args, summary="  Wait for the current check.  ")
        self.assertFalse(result.get("isError"), result)
        records = self.rpc("get_progress", session_id=self.sid, task_instance_id=self.iid, view="records",
                           task_id="check", record_kind="split_reviews")["structuredContent"]["progress"]["records"]
        self.assertEqual([record["summary"] for record in records], ["Wait for the current check."])

    def test_split_review_checks_the_registered_execution_id(self):
        self.assertFalse(self.event("current-run").get("isError"))
        review = dict(attempt_id=self.aid, reason="One remaining result",
                      remaining_outcomes=["Check result"], evidence=["fixture://scope"])
        rejected = self.write("review_task_split", **review, execution_id="stale-run")
        self.assertTrue(rejected.get("isError"), rejected)
        self.assertIn("与当前 attempt 已登记的执行不匹配", rejected["content"][0]["text"])

        accepted = self.write("review_task_split", **review, execution_id="current-run",
                              summary="Checked the current execution")
        self.assertFalse(accepted.get("isError"), accepted)
        records = self.rpc("get_progress", session_id=self.sid, task_instance_id=self.iid, view="records",
                           task_id="check", record_kind="split_reviews")["structuredContent"]["progress"]["records"]
        self.assertEqual([record["summary"] for record in records], ["Checked the current execution"])

    def test_split_decision_expires(self):
        self.write("review_task_split", attempt_id=self.aid, reason="One remaining result",
                   remaining_outcomes=["Check result"], evidence=["fixture://scope"], valid_for_seconds=1)
        data = server._read(self.sid)
        data["nodes"]["check"]["started_at"] = "2020-01-01T00:00:00+00:00"
        data["nodes"]["check"]["decomposition_review"]["expires_at"] = "2020-01-01T00:00:01+00:00"
        server._write(self.sid, data)
        self.assertTrue(self.node()["split_review_required"])

    def test_unknown_legacy_start_is_not_invented_as_long_running(self):
        data = server._read(self.sid)
        data["nodes"]["check"]["started_at"] = None
        server._write(self.sid, data)
        self.assertFalse(self.node()["split_review_required"])

    def test_supervisor_survives_its_mcp_process_and_captures_failure(self):
        broker = subprocess.Popen([sys.executable, "-c",
            "import importlib.util,pathlib,sys; s=importlib.util.spec_from_file_location('broker',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.SESSION_DIR=pathlib.Path(sys.argv[2]); m.main()",
            str(PLUGIN / "server.py"), self.temp.name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        gate = Path(self.temp.name) / "release-command"
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "start_task_execution", "arguments": {
                "session_id": self.sid, "task_instance_id": self.iid, "task_id": "check", "attempt_id": self.aid,
                "command": [sys.executable, "-c", "import pathlib,time; p=pathlib.Path('release-command'); exec('while not p.exists(): time.sleep(0.01)'); raise SystemExit(11)"],
                "cwd": self.temp.name, "summary": "Survive MCP exit"}}}
        try:
            broker.stdin.write(json.dumps(request) + "\n"); broker.stdin.flush()
            response = json.loads(broker.stdout.readline())
            self.assertFalse(response["result"].get("isError"), response)
            broker.kill(); broker.wait(timeout=5)
            gate.touch()
            node = self.wait_for_execution("failed")
            self.assertEqual(node["execution"]["exit_code"], 11)
            self.assertTrue(node["finished_at"])
        finally:
            gate.touch()
            if broker.poll() is None:
                broker.kill()
            broker.communicate(timeout=5)

    def wait_for_execution(self, expected):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            node = self.node()
            if (node.get("execution") or {}).get("status") == expected:
                return node
            time.sleep(0.02)
        self.fail("worker did not finish: " + repr(node))

    def test_managed_exit_closes_failure_without_agent_closeout(self):
        result = self.write("start_task_execution", attempt_id=self.aid,
                            command=[sys.executable, "-c", "raise SystemExit(7)"], cwd=self.temp.name,
                            summary="Failing isolated command")
        self.assertFalse(result.get("isError"), result)
        node = self.wait_for_execution("failed")
        self.assertEqual(node["execution_status"], "failed")
        self.assertEqual(node["execution"]["exit_code"], 7)
        self.assertEqual(node["execution"]["verification"], "managed_worker")

    def test_invalid_managed_execution_id_reports_the_offending_field(self):
        before = server._session_file(self.sid).read_bytes()
        result = self.write("start_task_execution", attempt_id=self.aid, execution_id="review-run",
                            command=[sys.executable, "-c", "pass"], cwd=self.temp.name,
                            summary="Invalid managed execution identity")
        self.assertTrue(result.get("isError"), result)
        self.assertIn("execution_id 必须是 UUID", result["content"][0]["text"])
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)
        self.assertFalse((Path(self.temp.name) / ".executions").exists())

    def test_failed_closeout_lists_all_invalid_result_fields_before_retry(self):
        before = server._session_file(self.sid).read_bytes()
        args = {"attempt_id": self.aid, "execution_status": "failed", "summary": "Validation failed"}
        rejected = self.write("update_task", outcome="open", acceptance_checks=[],
                              artifacts=[], verified_artifacts=[], **args)
        self.assertTrue(rejected.get("isError"), rejected)
        self.assertIn("execution_status=failed 时请省略 outcome, acceptance_checks, artifacts, verified_artifacts",
                      rejected["content"][0]["text"])
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

        accepted = self.write("update_task", **args)
        self.assertFalse(accepted.get("isError"), accepted)
        self.assertEqual(accepted["structuredContent"]["affected"]["execution_status"], "failed")

    def test_managed_success_needs_acceptance_and_replay_does_not_repeat_command(self):
        count = Path(self.temp.name) / "count"
        command = [sys.executable, "-c", "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]
        eid = str(uuid.uuid4())
        kwargs = dict(attempt_id=self.aid, execution_id=eid, command=command, cwd=self.temp.name, summary="One isolated write")
        self.assertFalse(self.write("start_task_execution", **kwargs).get("isError"))
        self.assertFalse(self.write("start_task_execution", **kwargs).get("isError"))
        node = self.wait_for_execution("succeeded")
        self.assertEqual(node["execution_status"], "running")
        self.assertEqual(count.read_text(), "x")
        self.assertFalse(self.write("start_task_execution", **kwargs).get("isError"))
        self.assertEqual(count.read_text(), "x")
        self.write("update_task", attempt_id=self.aid, execution_status="finished", outcome="resolved", summary="Validated command output")
        self.assertEqual(self.node()["outcome"], "resolved")

    def test_persisted_receipt_reconciles_after_service_recovery(self):
        eid = str(uuid.uuid4())
        with patch.object(server.subprocess, "Popen", side_effect=OSError("launch unavailable")):
            self.write("start_task_execution", attempt_id=self.aid, execution_id=eid,
                       command=[sys.executable, "-c", "pass"], cwd=self.temp.name, summary="Restart fixture")
        # Simulate a supervisor that exited while its MCP service was absent.
        data = server._read(self.sid)
        node = data["nodes"]["check"]
        node.update(execution_status="running", finished_at=None)
        node["execution"].update(status="running", finished_at=None)
        server._write(self.sid, data)
        path = Path(self.temp.name) / ".executions" / (eid + ".result.json")
        path.write_text(json.dumps({"execution_id": eid, "status": "failed", "exit_code": 9,
                                  "summary": "Recorded while service absent", "finished_at": server._now()}))
        self.assertEqual(self.node()["execution_status"], "failed")
        self.assertEqual(self.node()["execution"]["exit_code"], 9)


if __name__ == "__main__":
    unittest.main()
