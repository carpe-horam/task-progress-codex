"""Build Cindy from shared sources without requiring a checked-in core copy."""
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CINDY = ROOT / "plugins/task-progress/cindy"
SHARED = CINDY.parent


class CindyPrepareTest(unittest.TestCase):
    def test_mismatched_versions_fail_before_creating_output(self):
        spec = importlib.util.spec_from_file_location("cindy_prepare", CINDY / "prepare.py")
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        for mismatch in (".codex-plugin/plugin.json", "cindy/ghost.json", "dsh/package.json"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temp:
                shared = Path(temp) / "shared"
                for name in (".codex-plugin/plugin.json", "cindy/ghost.json", "dsh/package.json"):
                    path = shared / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"version": "9.8.6" if name == mismatch else "9.8.7"}))
                destination = Path(temp) / "output"
                with mock.patch.object(prepare, "SHARED", shared), \
                     mock.patch.object(prepare, "CINDY", shared / "cindy"):
                    with self.assertRaisesRegex(ValueError, "platform versions must match"):
                        prepare.generate(destination)
                self.assertFalse(destination.exists())

    def test_clean_stage_is_self_contained_and_preserves_adapter_behavior(self):
        with tempfile.TemporaryDirectory(prefix="cindy-stage-test-") as temp:
            stage = Path(temp) / "plugin"
            subprocess.run([sys.executable, str(CINDY / "prepare.py"), "--stage", str(stage)],
                           check=True, capture_output=True, text=True)
            core = stage / "core"
            self.assertEqual(json.loads((stage / "ghost.json").read_text())["version"],
                             json.loads((core / ".codex-plugin/plugin.json").read_text())["version"])
            self.assertEqual(json.loads((stage / "ghost.json").read_text())["id"], "task-progress")
            self.assertEqual((stage / "ghost.json").read_bytes(), (core / "ghost.json").read_bytes())
            skill = json.loads((stage / "ghost.json").read_text())["skill"]["items"][0]
            self.assertEqual(skill["dir"], "skills/track-progress")
            skill_file = stage / skill["dir"] / "SKILL.md"
            self.assertTrue(skill_file.is_file())
            frontmatter = skill_file.read_text().split("---", 2)[1]
            self.assertIn(f"name: {skill['name']}", frontmatter)
            self.assertIn(f"description: {skill['description']}", frontmatter)
            self.assertEqual((stage / skill["dir"] / "references/cindy-orca-worker.md").read_bytes(),
                             (core / "skills/track-progress/references/cindy-orca-worker.md").read_bytes())
            self.assertEqual((core / "execution_lifecycle.py").read_bytes(),
                             (SHARED / "execution_lifecycle.py").read_bytes())
            self.assertIn('Path(__file__).with_name("ghost.json")', (core / "server.py").read_text())
            self.assertIn('return self.plugin_root / "ghost.json"', (core / "service_manager.py").read_text())
            bridge=(core / "mcp_bridge.py").read_text()
            self.assertIn('_healthy_newer_compatible_service(manager)', bridge)
            self.assertIn('if not _healthy_newer_compatible_service(manager):', bridge)
            self.assertIn('running_version > client_version', bridge)
            self.assertIn('id="task-history-list"', (core / "panel-app.html").read_text())
            self.assertIn('manager.install()', bridge)
            self.assertFalse((stage / "patches").exists())
            self.assertFalse((stage / "prepare.py").exists())
            self.assertFalse(any(stage.rglob("*.pyc")))


if __name__ == "__main__":
    unittest.main()
