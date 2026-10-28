"""Exercise installed hook commands with a configured Python and a minimal PATH."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"


class HookRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-hook-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.plugin = Path(self.temp.name) / "plugin with spaces"
        for directory in ("hooks", "scripts"):
            shutil.copytree(PLUGIN / directory, self.plugin / directory)
        self.empty_path = Path(self.temp.name) / "empty-bin"
        self.empty_path.mkdir()
        self.hooks = json.loads((self.plugin / "hooks" / "hooks.json").read_text())["hooks"]

    def invoke(self, event, runtime_variable, source="startup"):
        env = dict(os.environ)
        env.update({"PATH": str(self.empty_path), "PLUGIN_ROOT": str(self.plugin),
                    "CODEX_MCP_PYTHON_PATH": "", "CODEX_PYTHON_PATH": ""})
        env[runtime_variable] = sys.executable
        command = self.hooks[event][0]["hooks"][0]["command"]
        return subprocess.run(
            ["/bin/sh", "-c", command],
            input=json.dumps({"hook_event_name": event, "source": source}),
            env=env, text=True, capture_output=True, timeout=2,
        )

    def test_both_hook_events_use_configured_mcp_python_without_python_on_path(self):
        for event in ("SessionStart", "UserPromptSubmit"):
            with self.subTest(event=event):
                result = self.invoke(event, "CODEX_MCP_PYTHON_PATH")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual(payload["hookEventName"], event)
                self.assertIn("plan", payload["additionalContext"])

    def test_hook_also_uses_codex_python_fallback(self):
        result = self.invoke("SessionStart", "CODEX_PYTHON_PATH")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("additionalContext", json.loads(result.stdout)["hookSpecificOutput"])

    def test_all_entry_points_remind_sequential_delegation_and_separate_review(self):
        for event, source in (("SessionStart", "startup"), ("SessionStart", "resume"),
                              ("SessionStart", "compact"),
                              ("SessionStart", "fork"), ("UserPromptSubmit", "startup")):
            with self.subTest(event=event, source=source):
                result = self.invoke(event, "CODEX_MCP_PYTHON_PATH", source)
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("even when sequential", context)
                self.assertIn("self_execution_reason", context)
                self.assertIn("Start the attempt, spawn, then bind", context)
                self.assertIn("main session reviews it separately", context)

    def test_resume_and_fork_preserve_explicit_node_ownership(self):
        resumed = self.invoke("SessionStart", "CODEX_MCP_PYTHON_PATH", "resume")
        resume_context = json.loads(resumed.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("get_progress", resume_context)
        self.assertIn("does not transfer parent ownership", resume_context)
        forked = self.invoke("SessionStart", "CODEX_MCP_PYTHON_PATH", "fork")
        fork_context = json.loads(forked.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Do not assume the parent's task ownership", fork_context)
        self.assertIn("Do not accept your own submission", fork_context)

    def test_request_insertion_and_compaction_restore_foreground_work(self):
        for event, source in (("UserPromptSubmit", "startup"), ("SessionStart", "compact")):
            with self.subTest(event=event):
                result = self.invoke(event, "CODEX_MCP_PYTHON_PATH", source)
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("route_request", context)
                self.assertIn("task_context and session_work", context)
                self.assertIn("one request completing does not finish the session", context)
                self.assertIn("Honor explicit user stops", context)
                self.assertIn("Follow only your assigned scope as a subagent", context)

    def test_all_entry_points_prefer_selective_queries_and_small_receipts(self):
        for event, source in (("SessionStart", "startup"), ("SessionStart", "resume"),
                              ("SessionStart", "compact"), ("SessionStart", "fork"),
                              ("UserPromptSubmit", "startup")):
            with self.subTest(event=event, source=source):
                result = self.invoke(event, "CODEX_MCP_PYTHON_PATH", source)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                for instruction in ("identity/status receipts only", "overview first",
                                    "expected_state_revision", "full only when needed",
                                    "queried submission/evidence", "all request pages",
                                    "Do not query full after every write", "Render only when"):
                    self.assertIn(instruction, context)
                self.assertNotIn("get_progress_view", context)
                self.assertLess(len(context.encode("utf-8")), 2700)

    def test_malformed_hook_input_still_fails_open_through_launcher(self):
        env = {**os.environ, "PATH": str(self.empty_path), "PLUGIN_ROOT": str(self.plugin),
               "CODEX_MCP_PYTHON_PATH": sys.executable}
        command = self.hooks["SessionStart"][0]["hooks"][0]["command"]
        result = subprocess.run(["/bin/sh", "-c", command], input="not json",
                                env=env, text=True, capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
