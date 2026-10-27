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

    def invoke(self, event, runtime_variable):
        env = dict(os.environ)
        env.update({"PATH": str(self.empty_path), "PLUGIN_ROOT": str(self.plugin),
                    "CODEX_MCP_PYTHON_PATH": "", "CODEX_PYTHON_PATH": ""})
        env[runtime_variable] = sys.executable
        command = self.hooks[event][0]["hooks"][0]["command"]
        return subprocess.run(
            ["/bin/sh", "-c", command],
            input=json.dumps({"hook_event_name": event, "source": "startup"}),
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
