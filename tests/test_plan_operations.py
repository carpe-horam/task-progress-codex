"""Plan snapshots and in-place revisions preserve real execution history."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("plan_operations_server", Path(__file__).resolve().parents[1] / "plugins/task-progress/server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)):
    spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "Isolated fixture"}


class PlanOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        server.SESSION_DIR = Path(self.tmp.name)
        server.LEGACY_DB_PATH = Path(self.tmp.name) / "missing.sqlite"
        result = server.init_task_network("Goal", tasks=[
            {"id": "a", "title": "Running", "assignment": MAIN},
            {"id": "b", "title": "Blocked", "assignment": MAIN, "depends_on": ["a"]},
            {"id": "c", "title": "Later", "assignment": MAIN}])["structuredContent"]["progress"]
        self.sid, self.iid = result["session_id"], result["task_instance_id"]

    def data(self):
        return server._read(self.sid)

    def test_snapshot_running_is_immutable_idempotent_and_survives_replan_and_archive(self):
        server.update_task(self.sid, "a", "running", assignment=MAIN, task_instance_id=self.iid)
        old = self.data()
        aid = old["nodes"]["a"]["attempt_id"]
        server.snapshot_task_network(self.sid, self.iid, "snapshot-1", "Before continuing")
        saved = self.data()
        self.assertEqual(saved["nodes"], old["nodes"])
        self.assertEqual(saved["epoch"], old["epoch"])
        self.assertEqual(saved["revision"], old["revision"])
        self.assertEqual(saved["plan_snapshots"][0]["nodes"]["a"]["attempt_id"], aid)
        revision = saved["state_revision"]
        server.snapshot_task_network(self.sid, self.iid, "snapshot-1", "Before continuing")
        self.assertEqual(self.data()["state_revision"], revision)
        with self.assertRaises(server.ProgressError):
            server.snapshot_task_network(self.sid, self.iid, "snapshot-1", "Different")
        server.update_task(self.sid, "a", "finished", "open", "Still open", attempt_id=aid,
                           task_instance_id=self.iid, failure_reason="Fixture remains open")
        server.rebuild_task_network(self.sid, "New plan", task_instance_id=self.iid)
        queried = server.get_progress(self.sid, task_instance_id=self.iid, view="history", history_kind="plan_snapshots")
        snap = queried["structuredContent"]["progress"]["records"][0]
        self.assertEqual(snap["nodes"]["a"]["execution_status"], "running")
        self.assertEqual(snap["nodes"]["a"]["attempt_id"], aid)
        server.init_task_network("Independent", self.sid, atomic_reason="Fixture", task_relationship="independent",
                                 new_task_reason="Independent fixture after prior plan")
        archived = self.data()["task_history"][0]
        # Public reads add derived metric/title/coverage fields. Compare the
        # persisted snapshot, while the queried projection above verifies the
        # immutable execution attempt remains visible after replanning.
        self.assertEqual(archived["plan_snapshots"][0], saved["plan_snapshots"][0])

    def test_revise_dependencies_and_order_without_changing_attempts(self):
        server.update_task(self.sid, "a", "running", assignment=MAIN, task_instance_id=self.iid)
        before = self.data()
        server.revise_task_plan(self.sid, self.iid, "b", "Independent work", 0, before["revision"],
                                depends_on=[], before_id="c")
        after = self.data()
        self.assertEqual(after["nodes"]["a"], before["nodes"]["a"])
        self.assertEqual(after["nodes"]["b"]["depends_on"], [])
        self.assertEqual(after["epoch"], 0)
        self.assertEqual(after["plan_revisions"][0]["before"]["depends_on"], ["a"])
        self.assertEqual(after["plan_revisions"][0]["after"]["depends_on"], [])
        self.assertEqual(len(server.get_progress(self.sid, task_instance_id=self.iid, view="history",
                                                 history_kind="plan_revisions")["structuredContent"]["progress"]["records"]), 1)
        frozen = server._session_file(self.sid).read_bytes()
        for fn in (
            lambda: server.revise_task_plan(self.sid, self.iid, "b", "Stale", 0, before["revision"], depends_on=["c"]),
            lambda: server.revise_task_plan(self.sid, self.iid, "a", "Started", 0, after["revision"], depends_on=[]),
            lambda: server.revise_task_plan(self.sid, self.iid, "b", "Cycle", 0, after["revision"], depends_on=["b"]),
        ):
            with self.assertRaises(server.ProgressError):
                fn()
            self.assertEqual(server._session_file(self.sid).read_bytes(), frozen)

    def test_old_document_without_new_fields_remains_unchanged_by_read(self):
        data = self.data()
        self.assertNotIn("plan_snapshots", data)
        original = server._session_file(self.sid).read_bytes()
        result = server.get_progress(self.sid, task_instance_id=self.iid, view="history", history_kind="plan_snapshots")
        self.assertEqual(result["structuredContent"]["progress"]["records"], [])
        self.assertEqual(server._session_file(self.sid).read_bytes(), original)

    def test_regroup_unstarted_branch_and_reject_started_affected_group(self):
        server.decompose_task(self.sid, "b", [{"id": "b1", "title": "B1", "assignment": MAIN},
                                               {"id": "b2", "title": "B2", "assignment": MAIN}], self.iid)
        server.decompose_task(self.sid, "c", [{"id": "c1", "title": "C1", "assignment": MAIN},
                                               {"id": "c2", "title": "C2", "assignment": MAIN}], self.iid)
        d = self.data()
        server.revise_task_plan(self.sid, self.iid, "b1", "Move unstarted work", 0, d["revision"], parent_id="c")
        d = self.data()
        self.assertEqual(d["nodes"]["b1"]["parent_id"], "c")
        self.assertIn("b1", d["nodes"]["c"]["children"])
        self.assertNotIn("b1", d["nodes"]["b"]["children"])
        self.assertEqual(d["plan_revisions"][-1]["before"]["parent_id"], "b")
        server.update_task(self.sid, "c1", "running", assignment=MAIN, task_instance_id=self.iid)
        d = self.data(); frozen = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            server.revise_task_plan(self.sid, self.iid, "b1", "Unsafe move", 0, d["revision"], parent_id="root")
        self.assertEqual(server._session_file(self.sid).read_bytes(), frozen)


if __name__ == "__main__":
    unittest.main()
