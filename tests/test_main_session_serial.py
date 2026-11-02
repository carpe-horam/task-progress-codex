"""Main-session execution slots, including recovery of older in-flight data."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("serial_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)):
    spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "Implement this bounded task"}
SUB = {"execution_mode": "subagent", "model": "test", "reasoning_effort": "high",
       "tool_identifier": "spawn", "dispatch_text": "Do independent work"}


class MainSessionSerialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        server.SESSION_DIR = Path(self.temp.name)
        result = server.init_task_network("Serial", tasks=[
            {"id": "a", "title": "A", "assignment": MAIN},
            {"id": "b", "title": "B", "assignment": MAIN},
            {"id": "c", "title": "C", "assignment": SUB},
        ])["structuredContent"]
        self.sid = result["session_id"]
        self.instance = result["progress"]["task_instance_id"]

    def tearDown(self):
        self.temp.cleanup()

    def start(self, task, assignment=MAIN, **kw):
        server.update_task(self.sid, task, "running", assignment=assignment,
                           task_instance_id=self.instance, **kw)
        return server._read(self.sid)["nodes"][task]["attempt_id"]

    def finish(self, task, aid, outcome="resolved"):
        server.update_task(self.sid, task, "finished", outcome=outcome, attempt_id=aid,
                           summary="Work and remaining scope recorded", task_instance_id=self.instance)

    def test_serial_slot_projection_retry_and_parallel_subagent(self):
        a = self.start("a")
        view = server.get_progress_view(self.sid)["structuredContent"]["progress"]
        b = next(n for n in view["nodes"] if n["id"] == "b")
        self.assertTrue(b["graph_ready"])
        self.assertFalse(b["ready"])
        self.assertEqual(b["main_session_waiting_on"], ["a"])
        self.assertIn("c", [n["id"] for n in server.get_ready_tasks(self.sid)["structuredContent"]["ready_tasks"]])
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaisesRegex(server.ProgressError, "运行中的叶任务：a"):
            self.start("b")
        self.assertEqual(before, server._session_file(self.sid).read_bytes())
        self.start("c", SUB)
        self.finish("a", a, "open")
        b_id = self.start("b")
        with self.assertRaisesRegex(server.ProgressError, "运行中的叶任务：b"):
            self.start("a", retry=True)
        self.finish("b", b_id)
        self.assertNotEqual(a, self.start("a", retry=True))

    def test_simultaneous_starts_are_atomic(self):
        barrier = threading.Barrier(2)
        def run(task):
            barrier.wait()
            try:
                self.start(task)
                return "started"
            except server.ProgressError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(run, ["a", "b"])), ["started", "rejected"])

    def test_existing_multiple_running_records_can_be_closed_without_rewriting(self):
        a = self.start("a")
        path = server._session_file(self.sid)
        data = json.loads(path.read_text())
        other = data["nodes"]["b"]
        server._begin_attempt(other, MAIN)
        other["execution_status"] = "running"
        path.write_text(json.dumps(data))
        b = other["attempt_id"]
        server.update_task(self.sid, "a", "running", attempt_id=a, summary="Still running", task_instance_id=self.instance)
        self.finish("a", a, "open")
        self.assertEqual(server._read(self.sid)["nodes"]["b"]["execution_status"], "running")
        self.finish("b", b, "open")


if __name__ == "__main__":
    unittest.main()
