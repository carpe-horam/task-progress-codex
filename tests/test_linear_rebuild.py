"""A legacy linear plan can be continued as a network without losing history."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("linear_rebuild_server", Path(__file__).resolve().parents[1] / "plugins/task-progress/server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)):
    spec.loader.exec_module(server)


class LinearRebuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        server.SESSION_DIR = Path(self.tmp.name)
        server.LEGACY_DB_PATH = Path(self.tmp.name) / "absent.sqlite"
        result = server.init_progress(["原步骤"], "旧计划")["structuredContent"]["progress"]
        self.sid, self.iid = result["session_id"], result["task_instance_id"]

    def test_rebuild_archives_original_and_creates_network(self):
        result = server.rebuild_task_network(self.sid, "继续同一目标", "新计划", self.iid)["structuredContent"]["progress"]
        self.assertEqual(result["schema_version"], 2)
        self.assertNotEqual(result["task_instance_id"], self.iid)
        archived = server._read(self.sid)["task_history"]
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["task_instance_id"], self.iid)
        self.assertEqual(archived[0]["steps"][0]["status"], "pending")
        self.assertEqual(archived[0]["task_title"], "旧计划")

    def test_running_linear_task_is_not_replaced(self):
        server.update_step(self.sid, 0, "in_progress", assignment={"execution_mode": "main_session", "self_execution_reason": "测试"}, task_instance_id=self.iid)
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaisesRegex(server.ProgressError, "执行中"):
            server.rebuild_task_network(self.sid, "继续同一目标", task_instance_id=self.iid)
        self.assertEqual(before, server._session_file(self.sid).read_bytes())


if __name__ == "__main__":
    unittest.main()
