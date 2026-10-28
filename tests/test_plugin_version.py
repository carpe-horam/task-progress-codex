"""A packaged plugin must report its own manifest version at both boundaries."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"


class PluginVersionTests(unittest.TestCase):
    def test_packaged_manifest_controls_mcp_and_app_versions(self):
        with tempfile.TemporaryDirectory(prefix="task-progress-package-version-") as temporary:
            root = Path(temporary)
            package = root / "plugin"
            (package / ".codex-plugin").mkdir(parents=True)
            for name in ("server.py", "app.html"):
                shutil.copy2(PLUGIN / name, package / name)
            manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
            manifest["version"] = "9.8.7"
            (package / ".codex-plugin/plugin.json").write_text(json.dumps(manifest))
            with mock.patch.object(Path, "home", return_value=root / "home"):
                spec = importlib.util.spec_from_file_location("packaged_version_server", package / "server.py")
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)
                handshake = json.loads(server.handle({"jsonrpc": "2.0", "id": 1,
                                                       "method": "initialize", "params": {}}))
                self.assertEqual(handshake["result"]["serverInfo"]["version"], "9.8.7")
                for uri in (server.RESOURCE_URI, *server.LEGACY_RESOURCE_URIS):
                    response = json.loads(server.handle({"jsonrpc": "2.0", "id": 2,
                                                        "method": "resources/read", "params": {"uri": uri}}))
                    html = response["result"]["contents"][0]["text"]
                    self.assertIn("version: '9.8.7'", html)
                    self.assertNotIn("__TASK_PROGRESS_VERSION__", html)


if __name__ == "__main__":
    unittest.main()
