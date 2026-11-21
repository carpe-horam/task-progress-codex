"""Build Cindy from shared sources without requiring a checked-in core copy."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CINDY = ROOT / "plugins/task-progress/cindy"
SHARED = CINDY.parent


class CindyPrepareTest(unittest.TestCase):
    def test_clean_stage_is_self_contained_and_preserves_adapter_behavior(self):
        with tempfile.TemporaryDirectory(prefix="cindy-stage-test-") as temp:
            stage = Path(temp) / "plugin"
            subprocess.run([sys.executable, str(CINDY / "prepare.py"), "--stage", str(stage)],
                           check=True, capture_output=True, text=True)
            core = stage / "core"
            self.assertEqual(json.loads((stage / "ghost.json").read_text())["id"], "task-progress")
            self.assertEqual((stage / "ghost.json").read_bytes(), (core / "ghost.json").read_bytes())
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
