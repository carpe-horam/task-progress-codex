"""The native viewer discovers real instances without changing task ownership."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/task-progress'
spec = importlib.util.spec_from_file_location('widget_store_test_service', PLUGIN / 'shared_service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class WidgetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.core = service.load_core(Path(self.temporary.name) / 'sessions')
        self.store = service.WidgetStore(self.core)

    def call(self, name, **arguments):
        wire = json.loads(self.core.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                           'params': {'name': name, 'arguments': arguments}}))
        self.assertNotIn('error', wire)
        self.assertFalse(wire['result'].get('isError'), wire)
        return wire['result']['structuredContent']

    def task(self, title='One task'):
        return self.call('init_task_network', task_title=title, atomic_reason='One fixture operation')

    def test_current_and_archived_instances_keep_identity_and_frozen_results(self):
        old = self.task('Archived original')
        base = dict(session_id=old['session_id'], task_instance_id=old['task_instance_id'], task_id=old['root_id'])
        started = self.call('update_task', **base, execution_status='running', assignment={'execution_mode': 'main_session'})
        self.call('update_task', **base, attempt_id=started['attempt_id'], execution_status='finished', outcome='resolved',
                  summary='ARCHIVED_RESULT')
        new = self.call('init_task_network', session_id=old['session_id'], task_title='New foreground',
                        atomic_reason='Independent fixture', task_relationship='independent', new_task_reason='Old goal complete')
        path = self.core._session_file(old['session_id'])
        before = path.read_bytes()
        rows = self.store.catalogue()['tasks']
        self.assertEqual({row['title'] for row in rows}, {'Archived original', 'New foreground'})
        archived = next(row for row in rows if row['archived'])
        self.assertEqual(archived['task_instance_id'], old['task_instance_id'])
        self.assertEqual(archived['percent'], 100)
        view = self.store.snapshot(old['session_id'], old['task_instance_id'], include_history=True)
        self.assertEqual(view['structuredContent']['progress']['nodes'][0]['summary'], 'ARCHIVED_RESULT')
        self.assertEqual(self.store.snapshot(new['session_id'])['structuredContent']['progress']['task_title'], 'New foreground')
        self.assertEqual(before, path.read_bytes())

    def test_parallel_view_does_not_follow_foreground_or_change_shared_state(self):
        main = self.task('Foreground')
        routed = self.call('route_request', session_id=main['session_id'], task_instance_id=main['task_instance_id'],
            request_id='parallel-test', request_text='Independent work', task_title='Parallel', mode='parallel',
            reason='Isolated output', acceptance_criteria=['Return evidence'], constraints=['No publication'])
        instance = routed['session_work']['requests'][-1]['task_instance_id'] if 'session_work' in routed else None
        if instance is None:
            instance = self.core._read(main['session_id'])['session_work_state']['requests'][-1]['task_instance_id']
        path = self.core._session_file(main['session_id'])
        before = path.read_bytes()
        snapshot = self.store.snapshot(main['session_id'], instance)['structuredContent']
        self.assertEqual(snapshot['progress']['task_instance_id'], instance)
        self.assertNotIn('session_work', snapshot)
        self.assertNotIn('session_work', snapshot['progress'])
        self.assertNotIn('parallel_tasks', snapshot['progress'])
        self.assertEqual(len(self.store.catalogue()['tasks']), 2)
        self.assertEqual(before, path.read_bytes())

    def test_catalogue_cache_updates_search_pagination_and_corrupt_session_warning(self):
        one = self.task('First')
        self.task('Second')
        self.assertEqual(self.store.catalogue(limit=1)['total'], 2)
        self.assertTrue(self.store.catalogue(limit=1)['has_more'])
        self.assertEqual(len(self.store.catalogue(offset=1, limit=1)['tasks']), 1)
        self.assertEqual(self.store.catalogue(query='first')['tasks'][0]['title'], 'First')
        self.core._session_file(one['session_id']).unlink()
        self.assertEqual(self.store.catalogue()['total'], 1)
        (self.core.SESSION_DIR / '00000000-0000-4000-8000-000000000088.json').write_text('broken')
        (self.core.SESSION_DIR / 'irrelevant.order.json').write_text('broken')
        self.assertEqual(self.store.catalogue()['unreadable_sessions'], 1)
        for args in ({'offset': -1}, {'limit': 101}, {'limit': True}, {'query': []}):
            with self.assertRaises(ValueError):
                self.store.catalogue(**args)


if __name__ == '__main__':
    unittest.main()
