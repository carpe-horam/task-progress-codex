"""One-way host archive sync: real core, isolated files, unchanged results."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/task-progress'
spec = importlib.util.spec_from_file_location('host_archive_service', PLUGIN / 'shared_service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class HostArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = service.load_core(Path(self.temp.name) / 'sessions')
        self.store = service.WidgetStore(self.core)

    def rpc(self, method, params):
        return json.loads(self.core.handle({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}))

    def tool(self, name, **args):
        result = self.rpc('tools/call', {'name': name, 'arguments': args})
        self.assertNotIn('error', result)
        self.assertFalse(result['result'].get('isError'), result)
        return result['result']['structuredContent']

    def task(self, title='Host goal', **args):
        return self.tool('init_task_network', task_title=title, atomic_reason='One fixture operation', **args)

    def archive(self, sid, **args):
        return self.rpc('task-progress/archive-host-session',
                        {'session_id': sid, 'host_session_id': 'host-1', 'source': 'cindy', **args})

    def test_running_task_archives_without_rewriting_any_task_or_order_bytes(self):
        task = self.task()
        base = {key: task[key] for key in ('session_id', 'task_instance_id')}
        started = self.tool('update_task', **base, task_id='root', execution_status='running',
                            assignment={'execution_mode': 'main_session'})
        sid = task['session_id']
        before = self.core._session_file(sid).read_bytes()
        order = self.core._order_file(sid).read_bytes()
        self.assertFalse(self.store.catalogue()['tasks'][0]['archived'])  # Populate the cache.
        self.assertTrue(self.archive(sid)['result']['changed'])
        self.assertEqual(self.core._session_file(sid).read_bytes(), before)
        self.assertEqual(self.core._order_file(sid).read_bytes(), order)
        row = self.store.catalogue()['tasks'][0]
        self.assertTrue(row['archived'])
        self.assertFalse(row['closed'])
        self.assertEqual(row['status'], 'executing')
        snap = self.store.snapshot(sid)['structuredContent']
        self.assertTrue(snap['widget_view_binding']['archived'])
        self.assertEqual(snap['progress']['status'], 'in_progress')
        # A late legitimate result must not clear the independent archive marker.
        self.tool('update_task', **base, task_id='root', attempt_id=started['attempt_id'],
                  execution_status='finished', outcome='open', summary='Still unresolved')
        self.assertTrue(self.store.catalogue()['tasks'][0]['archived'])
        self.assertEqual(self.core._read(sid)['nodes']['root']['outcome'], 'open')

    def test_foreground_parallel_and_history_all_archive_but_other_sessions_do_not(self):
        old = self.task()
        sid = old['session_id']
        started = self.tool('update_task', session_id=sid, task_id='root', execution_status='running',
                            assignment={'execution_mode': 'main_session'})
        self.tool('update_task', session_id=sid, task_id='root', attempt_id=started['attempt_id'],
                  execution_status='finished', outcome='resolved', summary='Old goal finished')
        new = self.task('New foreground', session_id=sid, task_relationship='independent', new_task_reason='Independent fixture')
        self.tool('route_request', session_id=sid, task_instance_id=new['task_instance_id'],
                  request_id='parallel-fixture', request_text='Separate goal', task_title='Parallel', mode='parallel',
                  reason='Isolated fixture', acceptance_criteria=['Evidence'])
        other = self.task('Unrelated')
        before = self.core._session_file(sid).read_bytes()
        self.archive(sid)
        rows = self.store.catalogue()['tasks']
        related = [r for r in rows if r['session_id'] == sid]
        self.assertEqual(len(related), 1)
        self.assertTrue(all(r['archived'] for r in related))
        self.assertEqual(len(related[0]['instances']), 3)
        self.assertTrue(all(r['archived'] for r in related[0]['instances']))
        self.assertEqual(self.store.catalogue()['total'], 2)
        self.assertFalse(next(r for r in rows if r['session_id'] == other['session_id'])['archived'])
        self.assertEqual(before, self.core._session_file(sid).read_bytes())
        for row in related[0]['instances']:
            self.assertTrue(self.store.snapshot(sid, row['task_instance_id'])['structuredContent']['widget_view_binding']['archived'])

    def test_duplicate_concurrent_events_and_restart_keep_first_observation(self):
        sid = self.task()['session_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.archive(sid), range(12)))
        self.assertEqual(sum(r['result']['changed'] for r in results), 1)
        marker = self.core._host_archive_file(sid).read_bytes()
        fresh_core = service.load_core(self.core.SESSION_DIR)
        fresh_store = service.WidgetStore(fresh_core)
        self.assertTrue(fresh_store.catalogue()['tasks'][0]['archived'])
        self.assertFalse(self.archive(sid)['result']['changed'])
        self.assertEqual(marker, self.core._host_archive_file(sid).read_bytes())

    def test_session_archive_covers_future_instances_and_keeps_one_catalogue_row(self):
        task = self.task('Original')
        sid = task['session_id']
        self.archive(sid)
        run = self.tool('update_task', session_id=sid, task_id=task['root_id'], execution_status='running',
                        assignment={'execution_mode': 'main_session'})
        self.tool('update_task', session_id=sid, task_id=task['root_id'], attempt_id=run['attempt_id'],
                  execution_status='finished', outcome='resolved', summary='Original result')
        newer = self.task('Late foreground', session_id=sid, task_relationship='independent',
                          new_task_reason='Separate goal after original closed')
        self.tool('route_request', session_id=sid, task_instance_id=newer['task_instance_id'],
                  request_id='late-peer', request_text='Independent', task_title='Late peer', mode='parallel',
                  reason='Isolated fixture', acceptance_criteria=['Evidence'])
        document = self.core._session_file(sid).read_bytes()
        catalogue = self.store.catalogue()
        self.assertEqual(catalogue['total'], 1)
        row = catalogue['tasks'][0]
        self.assertTrue(row['archived'])
        self.assertFalse(row['closed'], 'Archival does not resolve unfinished execution')
        self.assertEqual(row['task_instance_id'], newer['task_instance_id'])
        self.assertEqual(len(row['instances']), 3)
        for record in row['instances']:
            self.assertTrue(record['archived'])
            view = self.store.snapshot(sid, record['task_instance_id'])['structuredContent']
            self.assertTrue(view['widget_view_binding']['archived'])
        self.assertEqual(document, self.core._session_file(sid).read_bytes())

    def test_invalid_conflicting_or_unarchive_requests_never_mutate(self):
        sid = self.task()['session_id']
        self.archive(sid)
        marker = self.core._host_archive_file(sid).read_bytes()
        for args in ({'source': 'other'}, {'host_session_id': '../host'},
                     {'host_session_id': 'different-host'}, {'archived': False}):
            self.assertIn('error', self.archive(sid, **args))
        self.assertEqual(marker, self.core._host_archive_file(sid).read_bytes())
        missing = '00000000-0000-4000-8000-000000000053'
        self.assertIn('error', self.archive(missing))
        self.assertFalse(self.core._host_archive_file(missing).exists())
        self.assertNotIn('archive_host_session', [t['name'] for t in self.core.TOOLS])

    def test_retry_after_commit_succeeded_but_acknowledgment_failed(self):
        sid = self.task()['session_id']
        original_write = self.core._atomic_json

        def write_then_fail(path, value):
            original_write(path, value)
            raise OSError('Simulate interrupted response after atomic replace')

        before = self.core._session_file(sid).read_bytes()
        with patch.object(self.core, '_atomic_json', side_effect=write_then_fail):
            self.assertIn('error', self.archive(sid))
        marker = self.core._host_archive_file(sid).read_bytes()
        retry = self.archive(sid)
        self.assertFalse(retry['result']['changed'])
        self.assertEqual(marker, self.core._host_archive_file(sid).read_bytes())
        self.assertEqual(before, self.core._session_file(sid).read_bytes())

    def test_failed_write_remains_retryable_without_creating_false_archive(self):
        sid = self.task()['session_id']
        before = self.core._session_file(sid).read_bytes()
        self.assertFalse(self.store.catalogue()['tasks'][0]['archived'])
        with patch.object(self.core, '_atomic_json', side_effect=OSError('Disk unavailable')):
            self.assertIn('error', self.archive(sid))
        self.assertFalse(self.core._host_archive_file(sid).exists())
        self.assertFalse(self.store.catalogue()['tasks'][0]['archived'])
        self.assertTrue(self.archive(sid)['result']['changed'])
        self.assertTrue(self.store.catalogue()['tasks'][0]['archived'])
        self.assertEqual(before, self.core._session_file(sid).read_bytes())

    def test_widget_capability_cannot_write_archive(self):
        task = self.task()
        with service.SharedMCPServer(0, 'Bearer fixture-secret', self.core) as server:
            response = json.loads(server.widget_rpc({'jsonrpc': '2.0', 'id': 1,
                'method': 'task-progress/archive-host-session', 'params': {
                    'session_id': task['session_id'], 'host_session_id': 'host-1', 'source': 'cindy'}}, {}))
        self.assertIn('error', response)
        self.assertFalse(self.core._host_archive_file(task['session_id']).exists())


if __name__ == '__main__':
    unittest.main()
