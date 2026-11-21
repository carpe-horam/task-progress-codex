"""Append work to semantic packages without flattening or rewriting old work."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("work_package_routing_server", Path(__file__).resolve().parents[1] / "plugins/task-progress/server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)):
    spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "Isolated package routing fixture"}


def task(key, title=None, **kwargs):
    return {"id": key, "title": title or key, "assignment": MAIN, **kwargs}


class WorkPackageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        server.SESSION_DIR = Path(self.tmp.name)
        server.LEGACY_DB_PATH = Path(self.tmp.name) / "missing.sqlite"
        p = server.init_task_network("Delivery", tasks=[
            task("build", "Implementation"), task("code", parent_id="build"),
            task("qa", "Acceptance", depends_on=["build"]), task("test", parent_id="qa"),
        ])["structuredContent"]["progress"]
        self.sid, self.iid = p["session_id"], p["task_instance_id"]

    def data(self):
        return server._read(self.sid)

    def test_overview_reports_only_actionable_synthesis(self):
        overview=lambda: server.get_progress(self.sid,view="overview")["structuredContent"]["progress"]
        self.assertNotIn("build",overview()["synthesis_ready_ids"])
        server.update_task(self.sid,"code","running",assignment=MAIN,task_instance_id=self.iid)
        attempt=self.data()["nodes"]["code"]["attempt_id"]
        server.update_task(self.sid,"code","finished","resolved","完成",evidence=["fixture"],attempt_id=attempt,task_instance_id=self.iid)
        p=overview();self.assertEqual(p["synthesis_ready_count"],1);self.assertEqual(p["synthesis_ready_ids"],["build"])
        queue=server.get_progress(self.sid,view="review_queue")["structuredContent"]
        record=queue["progress"]["records"][0]
        self.assertEqual((record["kind"],record["task_id"],record["actionable"]),("synthesis","build",True))
        self.assertEqual(record["source_fingerprint"],server._result_fingerprint(self.data(),"build"))
        server.update_task(self.sid,"build","finished","resolved","综合验收",evidence=["fixture"],assignment=MAIN,task_instance_id=self.iid)
        self.assertNotIn("build",overview()["synthesis_ready_ids"])
        self.assertNotIn("build",[item["task_id"] for item in server.get_progress(self.sid,view="review_queue")["structuredContent"]["progress"]["records"]])

    def test_open_child_never_implies_accepted_result(self):
        server.update_task(self.sid,"code","running",assignment=MAIN,task_instance_id=self.iid)
        attempt=self.data()["nodes"]["code"]["attempt_id"]
        server.update_task(self.sid,"code","finished","open","仍需确认",failure_reason="缺少现场证据",attempt_id=attempt,task_instance_id=self.iid)
        overview=server.get_progress(self.sid,view="overview")["structuredContent"]["progress"]
        self.assertIn("build",overview["synthesis_ready_ids"])
        with self.assertRaisesRegex(server.ProgressError,"resolved|未解决"):
            server.update_task(self.sid,"build","finished","resolved","误判",evidence=["fixture"],assignment=MAIN,task_instance_id=self.iid)
        self.assertEqual(self.data()["nodes"]["code"]["outcome"],"open")

    def test_pending_subagent_review_does_not_allow_synthesis(self):
        sub={"execution_mode":"subagent","tool_identifier":"fixture","dispatch_text":"produce result"}
        server.reassign_task(self.sid,"code",sub,"独立核验",MAIN,task_instance_id=self.iid)
        server.update_task(self.sid,"code","running",assignment=sub,task_instance_id=self.iid)
        attempt=self.data()["nodes"]["code"]["attempt_id"]
        server.bind_task_agent(self.sid,"code",attempt,"fixture-agent",task_instance_id=self.iid)
        server.update_task(self.sid,"code","finished","resolved","提交",evidence=["fixture"],attempt_id=attempt,task_instance_id=self.iid)
        overview=server.get_progress(self.sid,view="overview")["structuredContent"]["progress"]
        self.assertEqual(overview["pending_review_count"],1)
        self.assertNotIn("build",overview["synthesis_ready_ids"])

    def add(self, tasks, parent="root"):
        return server.decompose_task(self.sid, parent, tasks, task_instance_id=self.iid)

    def assert_unchanged_rejection(self, tasks, pattern, parent="root"):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaisesRegex(server.ProgressError, pattern):
            self.add(tasks, parent)
        self.assertEqual(server._session_file(self.sid).read_bytes(), before)

    def test_cross_package_batch_preserves_existing_nodes_and_inherited_dependencies(self):
        before = self.data()
        self.add([task("fix", parent_id="build", addition_reason="Fix discovered defect"),
                  task("regress", parent_id="qa", addition_reason="Cover defect")])
        nodes = self.data()["nodes"]
        self.assertEqual(nodes["root"]["children"], ["build", "qa"])
        self.assertEqual(nodes["build"]["children"], ["code", "fix"])
        self.assertEqual(nodes["qa"]["children"], ["test", "regress"])
        self.assertEqual(nodes["code"], before["nodes"]["code"])
        self.assertEqual(nodes["test"], before["nodes"]["test"])
        self.assertTrue(nodes["regress"]["is_added"])
        ready = server.get_ready_tasks(self.sid, task_instance_id=self.iid)["structuredContent"]["ready_tasks"]
        self.assertEqual({n["id"] for n in ready}, {"code", "fix"})

    def test_root_flattening_rejected_with_recovery_candidates(self):
        self.assert_unchanged_rejection([task("later")], "不能平铺.*build（Implementation）.*qa（Acceptance）")
        self.assert_unchanged_rejection([task("safe", parent_id="qa"), task("stray")], "不能平铺")

    def test_new_direction_is_a_package_and_can_share_batch_with_existing_package(self):
        self.add([task("delivery", "Release"), task("publish", parent_id="delivery"),
                  task("smoke", parent_id="qa")])
        nodes = self.data()["nodes"]
        self.assertEqual(nodes["root"]["children"], ["build", "qa", "delivery"])
        self.assertEqual(nodes["delivery"]["children"], ["publish"])
        self.assertEqual(nodes["qa"]["children"], ["test", "smoke"])
        # A direct scoped append remains backwards compatible.
        self.add([task("notes")], "delivery")
        self.assertEqual(self.data()["nodes"]["delivery"]["children"], ["publish", "notes"])

    def test_scope_leaf_conversion_and_cycles_rejected_atomically(self):
        self.assert_unchanged_rejection([task("outside", parent_id="qa")], "不在.*范围", "build")
        self.assert_unchanged_rejection([task("child", parent_id="code")], "已有叶任务")
        self.assert_unchanged_rejection([task("child", parent_id="missing")], "本批新增")
        self.assert_unchanged_rejection([task("safe", parent_id="build"),
            task("cycle", parent_id="qa", precedes=["build"], addition_reason="Invalid loop")], "循环依赖")

    def test_completed_group_and_ancestor_cannot_be_reopened(self):
        server.update_task(self.sid, "code", "running", assignment=MAIN, task_instance_id=self.iid)
        aid = self.data()["nodes"]["code"]["attempt_id"]
        server.update_task(self.sid, "code", "finished", "resolved", "Fixture", evidence=["Fixture"],
                           attempt_id=aid, task_instance_id=self.iid)
        server.update_task(self.sid, "build", "finished", "resolved", "Fixture package accepted", evidence=["Fixture"],
                           assignment=MAIN, task_instance_id=self.iid)
        self.assert_unchanged_rejection([task("safe", parent_id="qa"), task("late", parent_id="build")], "正在执行或已结束")

    def test_nested_existing_package_allowed_and_old_attempts_retained(self):
        self.add([task("docs"), task("readme", parent_id="docs")], "build")
        server.update_task(self.sid, "code", "running", assignment=MAIN, task_instance_id=self.iid)
        aid = self.data()["nodes"]["code"]["attempt_id"]
        before = self.data()["nodes"]["code"]
        self.add([task("guide", parent_id="docs")])
        self.assertEqual(self.data()["nodes"]["code"], before)
        self.assertEqual(self.data()["nodes"]["code"]["attempt_id"], aid)
        self.assertEqual(self.data()["nodes"]["docs"]["children"], ["readme", "guide"])

    def test_flat_legacy_and_atomic_plans_remain_compatible(self):
        p = server.init_task_network("Legacy flat", tasks=[task("a"), task("b")])["structuredContent"]["progress"]
        server.decompose_task(p["session_id"], "root", [task("c")], task_instance_id=p["task_instance_id"])
        self.assertEqual(server._read(p["session_id"])["nodes"]["root"]["children"], ["a", "b", "c"])
        a = server.init_task_network("Atomic", atomic_reason="One isolated action")["structuredContent"]["progress"]
        self.assertEqual(a["planning_status"], "atomic")
        result = server.get_progress(self.sid, view="overview")["structuredContent"]
        self.assertIn("task_existing_parent_v1", result["protocol_capabilities"])


if __name__ == "__main__":
    unittest.main()
