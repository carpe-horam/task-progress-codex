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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], old['session_id'])
        self.assertEqual(rows[0]['title'], 'New foreground')
        self.assertFalse(rows[0]['archived'])
        self.assertFalse(rows[0]['closed'])
        archived = next(row for row in rows[0]['instances'] if row['historical'])
        self.assertEqual(archived['task_instance_id'], old['task_instance_id'])
        self.assertEqual(archived['percent'], 100)
        view = self.store.snapshot(old['session_id'], old['task_instance_id'], include_history=True)
        self.assertEqual(view['structuredContent']['progress']['nodes'][0]['summary'], 'ARCHIVED_RESULT')
        self.assertEqual(self.store.snapshot(new['session_id'])['structuredContent']['progress']['task_title'], 'New foreground')
        self.assertEqual(before, path.read_bytes())
        # Search finds historical records but pagination/counts stay session based.
        self.task('Another session')
        page = self.store.catalogue(query='original', limit=1)
        self.assertEqual(page['total'], 1)
        self.assertFalse(page['has_more'])
        self.assertEqual(page['tasks'][0]['task_instance_id'], new['task_instance_id'])
        self.assertEqual(self.store.catalogue(limit=1)['total'], 2)
        self.assertNotEqual(self.store.catalogue(limit=1)['tasks'][0]['id'],
                            self.store.catalogue(offset=1, limit=1)['tasks'][0]['id'])

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
        row = self.store.catalogue()['tasks'][0]
        self.assertEqual(self.store.catalogue()['total'], 1)
        self.assertEqual({i['task_instance_id'] for i in row['instances']}, {main['task_instance_id'], instance})
        self.assertEqual(row['task_instance_id'], main['task_instance_id'])
        self.assertEqual(before, path.read_bytes())

    def test_closed_foreground_does_not_hide_unfinished_parallel_instance(self):
        main = self.task('Main')
        self.call('route_request', session_id=main['session_id'], task_instance_id=main['task_instance_id'],
                  request_id='live-peer', request_text='Independent work', task_title='Peer', mode='parallel',
                  reason='Independent fixture', acceptance_criteria=['Evidence'])
        base = {key: main[key] for key in ('session_id', 'task_instance_id')}
        run = self.call('update_task', **base, task_id=main['root_id'], execution_status='running',
                        assignment={'execution_mode': 'main_session'})
        self.call('update_task', **base, task_id=main['root_id'], attempt_id=run['attempt_id'],
                  execution_status='finished', outcome='resolved', summary='Main complete')
        row = self.store.catalogue()['tasks'][0]
        self.assertEqual(row['status'], 'completed')
        self.assertFalse(row['closed'], 'Unfinished peer keeps the session in unfinished filtering')
        self.assertEqual(len(row['instances']), 2)

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

    def test_legacy_linear_history_remains_readable_after_session_archive(self):
        old = self.call('init_progress', steps=['Original linear step'], task_title='Legacy record')
        sid = old['session_id']
        iid = old['task_instance_id']
        self.call('update_step', session_id=sid, task_instance_id=iid, step_index=0,
                  status='in_progress', assignment={'execution_mode': 'main_session',
                  'self_execution_reason': 'Legacy fixture'})
        attempt = self.core._read(sid)['steps'][0]['attempt_id']
        self.call('update_step', session_id=sid, task_instance_id=iid, step_index=0,
                  status='completed', attempt_id=attempt, note='LEGACY_FROZEN_RESULT')
        current = self.call('init_task_network', session_id=sid, task_title='Current HTN',
                            atomic_reason='Independent fixture', task_relationship='independent',
                            new_task_reason='Previous linear goal completed')
        for archived in (False, True):
            if archived:
                self.core.archive_host_session(sid, 'legacy-host', 'cindy')
            before = self.core._session_file(sid).read_bytes()
            for include_history in (False, True):
                view = self.store.snapshot(sid, iid, include_history=include_history)['structuredContent']
                self.assertEqual(view['progress']['schema_version'], 1)
                self.assertEqual(view['progress']['steps'][0]['note'], 'LEGACY_FROZEN_RESULT')
                self.assertEqual(view['widget_view_binding']['task_instance_id'], iid)
                self.assertTrue(view['widget_view_binding']['archived'])
            self.assertEqual(self.store.catalogue()['total'], 1)
            self.assertEqual(self.store.catalogue()['tasks'][0]['task_instance_id'], current['task_instance_id'])
            self.assertEqual(self.core._session_file(sid).read_bytes(), before)

    def test_source_persistence_failure_and_corruption_do_not_leak_into_other_lists(self):
        from unittest.mock import patch
        task = self.task('Unknown origin')
        sid = task['session_id'];path = self.core._session_file(sid);before = path.read_bytes()
        with patch.object(self.core,'_atomic_json',side_effect=OSError('Source storage unavailable')):
            with self.assertRaises(OSError):
                self.store.claim_source(sid,'cindy')
        self.assertEqual(self.store.catalogue(source='cindy')['total'],0)
        self.assertEqual(self.store.catalogue(source='unknown')['total'],1)
        self.store.claim_source(sid,'cindy')
        with self.assertRaises(ValueError):
            self.store.claim_source(sid,'codex')
        marker = self.core.SESSION_DIR/(sid+'.source.json')
        marker.write_text('{"version":1,"source":"invalid"}')
        for source in ('codex','cindy','unknown'):
            self.assertEqual(self.store.catalogue(source=source)['total'],0)
        with self.assertRaises(ValueError):
            self.store.snapshot(sid,source='cindy')
        self.assertEqual(path.read_bytes(),before)

    def test_source_claim_between_snapshot_reads_rejects_unknown_view(self):
        from unittest.mock import patch
        task = self.task('Unknown before read')
        sid = task['session_id']
        original = self.core.get_progress_view

        def claim_then_project(*args, **kwargs):
            self.store.claim_source(sid, 'cindy')
            document = self.core._read(sid)
            self.call('revise_task_title', session_id=sid, task_instance_id=task['task_instance_id'],
                      task_id=task['root_id'], display_title='Cindy update after claim',
                      reason='Controlled read interleaving', expected_epoch=document['epoch'],
                      expected_revision=document['revision'])
            result = original(*args, **kwargs)
            self.assertEqual(result['structuredContent']['progress']['display_title'],
                             'Cindy update after claim')
            return result

        # The current-instance path releases its initial lock before the real
        # get_progress_view lock; claim and update in precisely that gap.
        with patch.object(self.core, 'get_progress_view', side_effect=claim_then_project):
            with self.assertRaisesRegex(ValueError, 'outside this source'):
                self.store.snapshot(sid, source='unknown')
        self.assertEqual(self.store.snapshot(sid, source='cindy')['structuredContent']
                         ['progress']['display_title'], 'Cindy update after claim')

    def test_source_claim_before_catalogue_lock_excludes_reclassified_session(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        task = self.task('Unknown before catalogue')
        sid = task['session_id']
        original_lock = self.core._session_lock
        claimed = False

        @contextmanager
        def claim_before_lock(target):
            nonlocal claimed
            if not claimed:
                claimed = True
                self.store.claim_source(sid, 'cindy')
                document = self.core._read(sid)
                self.call('revise_task_title', session_id=sid, task_instance_id=task['task_instance_id'],
                          task_id=task['root_id'], display_title='Cindy catalogue update',
                          reason='Controlled catalogue interleaving', expected_epoch=document['epoch'],
                          expected_revision=document['revision'])
            with original_lock(target):
                yield

        # No thread timing guesses: enter the actual registration/update path
        # after the initial source read and before the catalogue's data lock.
        with patch.object(self.core, '_session_lock', side_effect=claim_before_lock):
            self.assertEqual(self.store.catalogue(source='unknown')['total'], 0)
        self.assertTrue(claimed)
        self.assertEqual(self.store.catalogue(source='cindy')['tasks'][0]['title'],
                         'Cindy catalogue update')


if __name__ == '__main__':
    unittest.main()
