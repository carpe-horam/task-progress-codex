"""The Cindy composition hook reads reminder facts without changing task state."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("task_progress_hook_peek_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as isolated:
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)


class HookPeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-hook-peek-")
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        created = server.init_task_network("Hook read", tasks=[
            {"id": "first", "title": "First", "assignment": {"execution_mode": "main_session"}},
            {"id": "second", "title": "Second", "assignment": {"execution_mode": "main_session"}},
        ])["structuredContent"]
        self.sid, self.iid = created["session_id"], created["progress"]["task_instance_id"]

    def call(self, **args):
        return json.loads(server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "peek_hook_overview", "arguments": args}}))["result"]

    def test_real_rpc_does_not_reconcile_or_advance_order(self):
        self.assertNotIn("peek_hook_overview", {tool["name"] for tool in server.TOOLS})
        task_file = server._session_file(self.sid)
        order_file = server._order_file(self.sid)
        before = (task_file.read_bytes(), order_file.read_bytes())
        with patch.object(server, "_reconcile_executions", side_effect=AssertionError("hook reconciled")):
            result = self.call(session_id=self.sid, task_instance_id=self.iid)
        source = result["structuredContent"]
        self.assertFalse(result.get("isError"), result)
        self.assertEqual((source["session_id"], source["task_instance_id"], source["view"]),
                         (self.sid, self.iid, "overview"))
        self.assertEqual(source["progress"]["schema_version"], 2)
        self.assertEqual((task_file.read_bytes(), order_file.read_bytes()), before)

    def test_cleared_session_returns_explicit_null_progress_without_writes(self):
        server.clear_progress(self.sid)
        order_file = server._order_file(self.sid)
        before = order_file.read_bytes()
        source = self.call(session_id=self.sid)["structuredContent"]
        self.assertIsNone(source["progress"])
        self.assertNotIn("task_instance_id", source)
        self.assertEqual(order_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
