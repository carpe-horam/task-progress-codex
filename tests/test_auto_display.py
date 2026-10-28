"""One-time display intent arbitration; isolated synthetic state only."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from test_progress_view_modes import server, isolated_session_storage


class AutoDisplayTests(unittest.TestCase):
    def setUp(self):
        self.store = isolated_session_storage()
        folder = self.store.__enter__()
        self.legacy = patch.object(server, "LEGACY_DB_PATH", folder / "missing.sqlite3")
        self.legacy.__enter__()
        init = server.init_task_network('Display fixture', atomic_reason='Single display fixture')
        self.sid = init['structuredContent']['session_id']

    def tearDown(self):
        self.legacy.__exit__(None, None, None)
        self.store.__exit__(None, None, None)

    def render(self):
        return server.render_progress(self.sid)['structuredContent']

    def consume(self, bootstrap, view='view-a'):
        return server.consume_display_intent(self.sid, view, bootstrap['display_intent'])['structuredContent']['status']

    def test_latest_only_and_once_across_concurrent_mounts(self):
        old, new = self.render(), self.render()
        self.assertEqual(self.consume(old), 'superseded')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda v: self.consume(new, v), ['view-a', 'view-b']))
        self.assertCountEqual(results, ['granted', 'consumed'])
        self.assertEqual(self.consume(new, 'remount'), 'consumed')
        self.assertNotIn('progress', new)
        self.assertLess(len(json.dumps(new)), 1000)

    def test_new_render_during_pending_request_does_not_duplicate(self):
        self.assertEqual(self.consume(self.render()), 'granted')
        latest = self.render()
        self.assertEqual(self.consume(latest, 'view-b'), 'already_open')
        self.assertEqual(self.consume(latest, 'view-b'), 'consumed')
        with patch.object(server.time, 'time', return_value=server.time.time() + 30):
            self.assertEqual(self.consume(self.render(), 'view-c'), 'granted')

    def test_existing_fullscreen_close_replay_and_explicit_reopen(self):
        first = self.render()
        self.assertEqual(self.consume(first), 'granted')
        server.register_ui_view(self.sid, 'view-a', first['view_sequence'], first['state_revision'],
                                first['task_instance_id'], 'fullscreen', 0)
        duplicate = self.render()
        self.assertEqual(self.consume(duplicate, 'view-b'), 'already_open')
        server.unregister_ui_view(self.sid, 'view-a')
        self.assertEqual(self.consume(first, 'remount'), 'superseded')
        self.assertEqual(self.consume(duplicate, 'remount'), 'consumed')
        self.assertEqual(self.consume(self.render(), 'reopened'), 'granted')

    def test_teardown_before_claim_and_cleared_task_cannot_auto_open(self):
        current = self.render()
        server.unregister_ui_view(self.sid, 'view-a')
        self.assertEqual(self.consume(current), 'superseded')
        server.clear_progress(self.sid)
        self.assertEqual(self.consume(current, 'other'), 'superseded')

    def test_app_visibility_and_resource_preference(self):
        tool = next(t for t in server.TOOLS if t['name'] == 'consume_display_intent')
        self.assertEqual(tool['_meta']['ui']['visibility'], ['app'])
        response = json.loads(server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'resources/read',
                                             'params': {'uri': server.RESOURCE_URI}}))
        self.assertEqual(response['result']['contents'][0]['_meta']['openai/ui']['preferredDisplayMode'], 'fullscreen')
