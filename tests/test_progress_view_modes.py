import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"
sys.path.insert(0, str(PLUGIN_DIR))
import server  # noqa: E402


@contextmanager
def isolated_session_storage():
    with tempfile.TemporaryDirectory(prefix="task-progress-view-test-") as temporary_dir:
        with mock.patch.object(server, "SESSION_DIR", Path(temporary_dir)):
            yield Path(temporary_dir)


class ProgressViewModeTests(unittest.TestCase):
    def setUp(self):
        storage = isolated_session_storage()
        self.session_dir = storage.__enter__()
        self.addCleanup(storage.__exit__, None, None, None)

    def test_only_explicit_display_tool_descriptor_links_the_ui_resource(self):
        response = json.loads(server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
        tools = response["result"]["tools"]
        display_tools = [
            tool["name"]
            for tool in tools
            if tool.get("_meta", {}).get("ui", {}).get("resourceUri") == server.RESOURCE_URI
        ]

        self.assertEqual(display_tools, ["render_progress"])

    def test_render_result_contains_latest_data_without_repeating_resource_metadata(self):
        session_id = "00000000-0000-4000-8000-000000000031"
        try:
            initialized = json.loads(server.handle({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "init_task_network", "arguments": {
                    "task_title": "Display metadata fixture", "session_id": session_id,
                }},
            }))['result']
            read = json.loads(server.handle({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "get_progress", "arguments": {"session_id": session_id}},
            }))['result']
            rendered = json.loads(server.handle({
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "render_progress", "arguments": {"session_id": session_id}},
            }))['result']

            self.assertNotIn("_meta", initialized)
            self.assertNotIn("_meta", read)
            self.assertNotIn("_meta", rendered)
            self.assertEqual(rendered["structuredContent"]["session_id"], session_id)
            self.assertTrue(rendered["structuredContent"]["bootstrap"])
            self.assertNotIn("progress", rendered["structuredContent"])
            self.assertEqual(
                rendered["structuredContent"]["root_id"],
                "root",
            )
        finally:
            server.clear_progress(session_id)

    def test_isolated_storage_preserves_an_existing_session_with_the_same_id(self):
        session_id = "00000000-0000-4000-8000-000000000031"
        preserved = {
            f"{session_id}.json": b'{"task_title":"Existing conversation"}\n',
            f"{session_id}.view.json": b'{"view_id":"existing-view"}\n',
            f"{session_id}.order.json": b'{"state_revision":71,"view_sequence":23}\n',
            f".{session_id}.lock": b"existing-lock\n",
        }
        for name, contents in preserved.items():
            (self.session_dir / name).write_bytes(contents)

        existing_directory = server.SESSION_DIR
        with isolated_session_storage() as fixture_directory:
            self.assertNotEqual(fixture_directory, existing_directory)
            server.init_task_network("Disposable fixture", session_id=session_id)
            server.render_progress(session_id)
            self.assertTrue((fixture_directory / f"{session_id}.json").exists())
            server.clear_progress(session_id)
            self.assertFalse((fixture_directory / f"{session_id}.json").exists())

        self.assertEqual(server.SESSION_DIR, existing_directory)
        self.assertFalse(fixture_directory.exists())
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.session_dir.iterdir()},
            preserved,
        )

    def test_progress_resource_prefers_inline_and_supports_fullscreen(self):
        response = json.loads(
            server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": server.RESOURCE_URI},
                }
            )
        )
        metadata = response["result"]["contents"][0]["_meta"]["openai/ui"]

        self.assertEqual(metadata["availableDisplayModes"], ["inline", "fullscreen"])
        self.assertEqual(metadata["preferredDisplayMode"], "inline")

    def test_versioned_resource_keeps_old_history_uri_renderable(self):
        version = json.loads((PLUGIN_DIR / ".codex-plugin" / "plugin.json").read_text())['version']
        initialized = json.loads(server.handle({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}}))
        self.assertEqual(initialized['result']['serverInfo']['version'], version)
        self.assertEqual(server.RESOURCE_URI, "ui://task-progress/progress-inspector/v3")
        listed = json.loads(server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}))
        uris = {item['uri'] for item in listed['result']['resources']}
        self.assertEqual(uris, {server.RESOURCE_URI, *server.LEGACY_RESOURCE_URIS})

        current_html = None
        for index, uri in enumerate(sorted(uris), start=5):
            response = json.loads(server.handle({
                "jsonrpc": "2.0", "id": index, "method": "resources/read", "params": {"uri": uri},
            }))
            content = response['result']['contents'][0]
            self.assertEqual(content['uri'], uri)
            self.assertEqual(content['_meta']['openai/ui']['preferredDisplayMode'], 'inline')
            if current_html is None:
                current_html = content['text']
            else:
                self.assertEqual(content['text'], current_html)
        self.assertIn('duplicate_fullscreen', current_html)


if __name__ == "__main__":
    unittest.main()
