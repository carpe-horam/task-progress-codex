"""Run the skill's public protocol examples against isolated backend state.

The examples are execution inputs, not text snapshots: failures reveal a
documentation/API mismatch or a missing responsibility transition.
"""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("delegation_policy_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
SKILL = PLUGIN / "skills" / "track-progress" / "SKILL.md"


class DelegationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-policy-")
        self.old_dir = server.SESSION_DIR
        server.SESSION_DIR = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(setattr, server, "SESSION_DIR", self.old_dir)
        self.examples = {
            name: json.loads(body) for name, body in re.findall(
                r"<!-- delegation-example:(\w+) -->\s*```json\s*(.*?)\s*```",
                SKILL.read_text(), re.S,
            )
        }
        initialized = server.init_task_network("Exercise published delegation workflow")
        self.sid = initialized["structuredContent"]["session_id"]
        self.instance = initialized["structuredContent"]["progress"]["task_instance_id"]
        self.attempt = None
        server.decompose_task(**self.example("plan"))

    def example(self, name):
        replacements = {
            "<本会话 ID>": self.sid,
            "<当前任务实例 ID>": self.instance,
            "<当前尝试 ID>": self.attempt or "not-started",
            "<实际模型>": "test-model-from-dispatch",
            "<实际返回的子智能体标识>": "agent-returned-by-test-spawn",
            "<文件路径、报告或可复核的结果链接>": "fixture:investigation-report",
            "<主会话复核的证据或验证结果>": "fixture:main-review",
        }
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return replacements.get(value, value) if isinstance(value, str) else value
        return replace(self.examples[name])

    def node(self, task_id):
        progress = server.get_progress(self.sid)["structuredContent"]["progress"]
        return next(node for node in progress["nodes"] if node["id"] == task_id)

    def start_and_bind(self):
        server.update_task(**self.example("start"))
        self.attempt = self.node("inspect")["attempt_id"]
        server.bind_task_agent(**self.example("bind"))

    def test_complex_sequential_example_keeps_implementation_and_validation_delegated(self):
        for task_id in ("inspect", "fix", "verify"):
            with self.subTest(task_id=task_id):
                task = self.node(task_id)
                self.assertEqual(task["planned_assignment"]["execution_mode"], "subagent")
                self.assertEqual(task["planned_assignment"]["complexity"], "complex")
        self.assertEqual(self.node("fix")["depends_on"], ["inspect"])
        self.assertEqual(self.node("verify")["depends_on"], ["fix"])
        accept = self.node("accept")["planned_assignment"]
        self.assertEqual(accept["execution_mode"], "main_session")
        self.assertTrue(accept["self_execution_reason"])
        with self.assertRaises(server.ProgressError):
            server.update_task(self.sid, "inspect", "running", task_instance_id=self.instance,
                               assignment={"execution_mode": "main_session",
                                           "self_execution_reason": "Has dependencies"})

    def test_published_start_bind_submit_review_sequence_controls_dependency(self):
        server.update_task(**self.example("start"))
        self.attempt = self.node("inspect")["attempt_id"]
        self.assertEqual(self.node("inspect")["dispatch"]["status"], "pending")
        started_assignment = self.node("inspect")["assignment"]
        with self.assertRaises(server.ProgressError):
            server.update_task(**self.example("submit"))
        server.bind_task_agent(**self.example("bind"))
        self.assertEqual(self.node("inspect")["dispatch"]["agent_name"],
                         "agent-returned-by-test-spawn")
        self.assertEqual(self.node("inspect")["assignment"], started_assignment)
        self.assertNotIn("agent_name", started_assignment)
        server.update_task(**self.example("submit"))
        pending = self.node("inspect")
        self.assertEqual(pending["review_status"], "pending")
        self.assertIsNone(pending["outcome"])
        self.assertEqual(pending["submission"]["outcome"], "resolved")
        with self.assertRaises(server.ProgressError):
            fix_start = self.example("start")
            fix_start["task_id"] = "fix"
            server.update_task(**fix_start)
        server.review_task(**self.example("review"))
        self.assertEqual(self.node("inspect")["review_status"], "accepted")
        server.update_task(**fix_start)
        self.assertEqual(self.node("fix")["execution_status"], "running")

    def test_main_review_cannot_upgrade_an_open_submission(self):
        self.start_and_bind()
        submission = self.example("submit")
        submission["outcome"] = "open"
        server.update_task(**submission)
        server.review_task(**self.example("review"))
        accepted = self.node("inspect")
        self.assertEqual(accepted["outcome"], "open")
        self.assertEqual(accepted["review_status"], "accepted")
        self.assertEqual(self.node("fix")["execution_status"], "pending")

    def test_published_reassignment_is_explicit_and_preserves_history(self):
        before = self.node("assets")
        self.assertEqual(before["planned_assignment"]["execution_mode"], "subagent")
        reassignment = self.example("reassign")
        server.reassign_task(**reassignment)
        assets = self.node("assets")
        self.assertEqual(assets["planned_assignment"]["execution_mode"], "main_session")
        self.assertEqual(assets["reassignment_history"][-1]["reason"], reassignment["reason"])
        server.update_task(self.sid, "assets", "running", task_instance_id=self.instance,
                           assignment=reassignment["assignment"])
        self.assertEqual(self.node("assets")["execution_status"], "running")


if __name__ == "__main__":
    unittest.main()
