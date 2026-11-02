"""Evidence coverage labels must never rewrite execution or acceptance facts."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("coverage_server", Path(__file__).resolve().parents[1] / "plugins/task-progress/server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)):
    spec.loader.exec_module(server)
MAIN = {"execution_mode": "main_session", "self_execution_reason": "Bounded coverage fixture"}
SUB = {"execution_mode": "subagent", "model": "fixture", "reasoning_effort": "medium", "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Inspect endpoint"}


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        server.LEGACY_DB_PATH = Path(self.temp.name) / "missing.sqlite"
        p = server.init_task_network("Coverage fixture", tasks=[
            {"id": "T3", "title": "Full integration", "assignment": MAIN},
            {"id": "T4", "title": "Endpoint diagnostic", "assignment": MAIN},
            {"id": "rest", "title": "Other endpoint", "assignment": MAIN},
            {"id": "after", "title": "Dependent integration", "assignment": MAIN, "depends_on": ["T3"]},
            {"id": "sub", "title": "Peer evidence", "assignment": SUB},
        ])["structuredContent"]["progress"]
        self.sid, self.iid = p["session_id"], p["task_instance_id"]
        self.target = self.finish("T3", status="failed")

    def raw(self):
        return server._read(self.sid)

    def start(self, nid, assignment=MAIN, retry=False):
        server.update_task(self.sid, nid, "running", assignment=assignment, retry=retry, task_instance_id=self.iid)
        return self.raw()["nodes"][nid]["attempt_id"]

    def finish(self, nid, status="finished", outcome="resolved", assignment=MAIN, retry=False):
        aid = self.start(nid, assignment, retry)
        if assignment == SUB:
            server.bind_task_agent(self.sid, nid, aid, "coverage-agent", self.iid)
        server.update_task(self.sid, nid, status, outcome=outcome if status == "finished" else None,
                           summary="Endpoint proof" if status == "finished" else "Integration failed",
                           evidence=["fixture://endpoint"], attempt_id=aid, task_instance_id=self.iid)
        return aid

    def cover(self, aid, **kwargs):
        values = dict(session_id=self.sid, task_instance_id=self.iid, coverage_id="endpoint-proof", source_task_id="T4",
                      source_attempt_id=aid, target_task_instance_id=self.iid, target_epoch=0, target_task_id="T3",
                      target_attempt_id=self.target, scope="GET /health responds; other endpoints unverified", reason="Explicit narrow diagnosis",
                      evidence=["fixture://health"], reviewer=MAIN)
        values.update(kwargs)
        return server.record_coverage(**values)

    def full(self):
        return server.get_progress(self.sid, view="full", task_instance_id=self.iid)["structuredContent"]["progress"]

    def node(self, nid):
        return next(n for n in self.full()["nodes"] if n["id"] == nid)

    def reject(self, fn):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            fn()
        self.assertEqual(before, server._session_file(self.sid).read_bytes())

    def test_partial_diagnostic_keeps_failure_dependencies_root_and_todo(self):
        aid = self.finish("T4")
        before = self.raw()["nodes"]
        metrics = {k: self.full()[k] for k in ("accepted_resolved_leaf_count", "ready_count", "status", "resolved_leaf_count")}
        self.cover(aid)
        self.assertEqual(before, self.raw()["nodes"])
        self.assertEqual(metrics, {k: self.full()[k] for k in metrics})
        failed = self.node("T3")
        self.assertEqual(failed["execution_status"], "failed")
        self.assertFalse(failed["accepted_resolved"])
        self.assertEqual(failed["coverage_count"], 1)
        self.assertEqual(failed["current_coverage"][0]["extent"], "partial")
        self.assertIn("GET /health", failed["current_coverage"][0]["scope"])
        self.assertEqual(self.node("rest")["execution_status"], "pending")
        self.assertFalse(self.node("after")["ready"])
        self.assertIsNone(self.node("root")["outcome"])
        tree = server.get_progress(self.sid, view="tree", depth=1)["structuredContent"]["progress"]["nodes"]
        self.assertEqual(next(n for n in tree if n["id"] == "T3")["coverage_count"], 1)
        self.assertEqual(server.get_progress(self.sid)["structuredContent"]["progress"]["coverage_summary"]["current"], 1)

    def test_retry_stales_old_proof_and_new_accepted_attempt_cannot_revive_it(self):
        aid = self.finish("T4")
        self.cover(aid)
        newer = self.finish("T4", retry=True)
        self.assertNotEqual(aid, newer)
        target = self.node("T3")
        self.assertEqual(target["current_coverage"], [])
        self.assertEqual(target["coverage_records"][0]["status"], "stale")
        self.assertEqual(target["coverage_records"][0]["stale_reason"], "source_attempt_changed")
        records = server.get_progress(self.sid, view="records", task_id="T3", record_kind="coverage_records")["structuredContent"]["progress"]["records"]
        self.assertEqual(records[0]["source_attempt_id"], aid)
        self.assertEqual(records[0]["source_result"]["evidence"], ["fixture://endpoint"])
        history = server.get_progress(self.sid, view="history", history_kind="coverage_records")["structuredContent"]["progress"]["records"]
        self.assertEqual(records, history)
        self.cover(newer, coverage_id="fresh-explicit-proof")
        self.assertEqual(self.node("T3")["coverage_count"], 1)
        self.assertEqual(len(self.node("T3")["coverage_records"]), 2)

    def test_pending_rejected_failed_open_and_invalidated_sources_cannot_cover(self):
        running = self.start("T4")
        self.reject(lambda: self.cover(running))
        server.update_task(self.sid, "T4", "finished", outcome="open", attempt_id=running,
                           summary="Unresolved", evidence=["fixture://open"], task_instance_id=self.iid)
        self.reject(lambda: self.cover(running))
        failed = self.finish("T4", status="failed", retry=True)
        self.reject(lambda: self.cover(failed))
        pending = self.finish("sub", assignment=SUB)
        self.reject(lambda: self.cover(pending, source_task_id="sub"))
        server.review_task(self.sid, "sub", pending, "reject", "Insufficient", ["fixture://reject"], MAIN, self.iid)
        self.reject(lambda: self.cover(pending, source_task_id="sub"))
        accepted = self.finish("T4", retry=True)
        self.cover(accepted)
        data = self.raw()
        data["nodes"]["T4"]["invalidated_by"] = "fixture-upstream-retry"
        server._write(self.sid, data)
        self.assertEqual(self.node("T3")["current_coverage"], [])
        self.reject(lambda: self.cover(accepted, coverage_id="invalidated-proof"))

    def test_precise_target_attempt_and_idempotent_records(self):
        aid = self.finish("T4")
        self.reject(lambda: self.cover(aid, target_attempt_id="wrong"))
        self.reject(lambda: self.cover(aid, scope=" "))
        self.reject(lambda: self.cover(aid, target_task_id="T4", target_attempt_id=aid))
        self.cover(aid)
        self.cover(aid)
        self.assertEqual(len(self.full()["coverage_records"]), 1)
        self.reject(lambda: self.cover(aid, scope="A broader claim"))
        old_target = self.target
        new_target = self.finish("T3", status="failed", retry=True)
        self.assertNotEqual(old_target, new_target)
        self.assertEqual(self.node("T3")["coverage_count"], 0)
        self.assertEqual(self.node("T3")["coverage_records"][0]["target_attempt_id"], old_target)
        self.assertEqual(self.node("T3")["execution_status"], "failed")

    def test_cross_instance_evidence_projects_to_both_ends(self):
        result = server.route_request(self.sid, self.iid, "peer-evidence", "Independent diagnostic", "Endpoint evidence",
                                      "parallel", "Independent scope", ["Proof retained"])
        peer = next(r["task_instance_id"] for r in server._read(self.sid)["session_work_state"]["requests"] if r["request_id"] == "peer-evidence")
        server.decompose_task(self.sid, "root", [{"id": "proof", "title": "Health diagnostic", "assignment": SUB}], task_instance_id=peer)
        server.update_task(self.sid, "proof", "running", assignment=SUB, task_instance_id=peer)
        aid = server._read(self.sid)["session_work_state"]["instances"][peer]["nodes"]["proof"]["attempt_id"]
        server.bind_task_agent(self.sid, "proof", aid, "cross-instance-agent", peer)
        server.update_task(self.sid, "proof", "finished", outcome="resolved", attempt_id=aid,
                           summary="Endpoint confirmed", evidence=["fixture://peer-endpoint"], task_instance_id=peer)
        server.review_task(self.sid, "proof", aid, "accept", "Endpoint proof accepted", ["fixture://peer-reviewed"], MAIN, peer)
        self.cover(aid, task_instance_id=peer, source_task_id="proof")
        target = self.node("T3")
        self.assertEqual(target["current_coverage"][0]["source_task_instance_id"], peer)
        self.assertEqual(target["execution_status"], "failed")
        proof = server.get_progress(self.sid, task_instance_id=peer, view="records", task_id="proof", record_kind="coverage_records")["structuredContent"]["progress"]["records"][0]
        self.assertEqual(proof["target_task_instance_id"], self.iid)
        server.update_task(self.sid, "proof", "running", assignment=SUB, retry=True, task_instance_id=peer)
        self.assertEqual(self.node("T3")["current_coverage"], [])
        self.assertEqual(self.node("T3")["coverage_records"][0]["status"], "stale")

    def test_history_plan_projection_preserves_chain(self):
        aid = self.finish("T4")
        self.cover(aid)
        # Rebuild retains the failed attempt and the evidence relation in its plan history.
        for nid in ("rest", "after", "sub"):
            server.update_task(self.sid, nid, "cancelled", summary="Fixture ended", task_instance_id=self.iid)
        server.rebuild_task_network(self.sid, "Continue remaining work", task_instance_id=self.iid)
        full = self.full()
        self.assertEqual(full["history"][0]["nodes"]["T3"]["execution_status"], "failed")
        link = full["history"][0]["nodes"]["T3"]["coverage_records"][0]
        self.assertEqual(link["target_attempt_id"], self.target)
        self.assertEqual(link["source_attempt_id"], aid)
        self.assertIn("created_at", link)
        self.assertEqual(link["reviewer"]["execution_mode"], "main_session")
        self.assertEqual(len(server.get_progress(self.sid, view="history", history_kind="coverage_records")["structuredContent"]["progress"]["records"]), 1)
        server.decompose_task(self.sid, full["root_id"], [
            {"id": "newproof", "title": "New diagnostic", "assignment": MAIN},
            {"id": "todo", "title": "Remaining scope", "assignment": MAIN}], task_instance_id=self.iid)
        new_source = self.finish("newproof")
        self.cover(new_source, coverage_id="old-epoch-target", source_task_id="newproof")
        later = self.full()
        old_target = later["history"][0]["nodes"]["T3"]
        self.assertEqual(old_target["execution_status"], "failed")
        self.assertEqual(len(old_target["current_coverage"]), 2)
        newest = next(r for r in old_target["coverage_records"] if r["coverage_id"] == "old-epoch-target")
        self.assertEqual((newest["source_epoch"], newest["target_epoch"]), (1, 0))
        self.assertEqual(self.node("todo")["execution_status"], "pending")


if __name__ == "__main__":
    unittest.main()
