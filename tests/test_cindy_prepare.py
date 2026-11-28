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
            manifest = json.loads((stage / "ghost.json").read_text())
            self.assertIn("open-progress-widget", [item["name"] for item in manifest["skill"]["items"]])
            for item in manifest["skill"]["items"]:
                self.assertEqual((stage / item["dir"] / "SKILL.md").read_bytes(),
                                 (core / item["dir"] / "SKILL.md").read_bytes())
            for name in ("open_progress.py", "ProgressWidget.swift"):
                relative = Path("skills/open-progress-widget/scripts") / name
                self.assertEqual((stage / relative).read_bytes(), (core / relative).read_bytes())
            skill = json.loads((stage / "ghost.json").read_text())["skill"]["items"][0]
            self.assertEqual(skill["dir"], "skills/track-progress")
            skill_file = stage / skill["dir"] / "SKILL.md"
            self.assertTrue(skill_file.is_file())
            frontmatter = skill_file.read_text().split("---", 2)[1]
            self.assertIn(f"name: {skill['name']}", frontmatter)
            self.assertIn(f"description: {skill['description']}", frontmatter)
            packaged_skill = stage / skill["dir"]
            shared_skill = SHARED / "skills/track-progress"
            self.assertIn("Cindy Lead 发起的委派统一走", skill_file.read_text())
            route = Path("references/cindy-orca-worker.md")
            self.assertIn("一次性子任务和跨阶段工作包均使用 Orca Worker",
                          (packaged_skill / route).read_text())
            entrypoints = ("delegation", "htn-planning", "htn-dispatch", "htn-retry",
                           "htn-review", "legacy-linear", "request-routing", "startup")
            self.assertIn("(references/cindy-orca-worker.md)", skill_file.read_text())
            for name in entrypoints:
                reference = Path("references") / f"{name}.md"
                with self.subTest(reference=reference):
                    self.assertIn("(cindy-orca-worker.md)", (packaged_skill / reference).read_text())
                    self.assertEqual((packaged_skill / reference).read_bytes(),
                                     (shared_skill / reference).read_bytes())
            self.assertEqual((packaged_skill / route).read_bytes(),
                             (core / "skills/track-progress" / route).read_bytes())
            for relative in ('widget_store.py', 'assets/widget-panel.html',
                             'skills/open-progress-widget/SKILL.md',
                             'skills/open-progress-widget/scripts/open_progress.py',
                             'skills/open-progress-widget/scripts/ProgressWidget.swift'):
                with self.subTest(widget_source=relative):
                    if relative.endswith('open_progress.py'):
                        self.assertEqual((core / relative).read_text(), (SHARED / relative).read_text().replace(
                            "DEFAULT_SOURCE = 'codex'", "DEFAULT_SOURCE = 'cindy'"))
                    else:
                        self.assertEqual((core / relative).read_bytes(), (SHARED / relative).read_bytes())
            self.assertIn("CLIENT_SOURCE = 'cindy'", (core / 'mcp_bridge.py').read_text())
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
