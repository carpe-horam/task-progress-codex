"""Public JSON-RPC projections use only disposable synthetic sessions."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/task-progress'
spec = importlib.util.spec_from_file_location('selective_query_server', PLUGIN / 'server.py')
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as isolated:
    with patch.object(Path, 'home', return_value=Path(isolated)):
        spec.loader.exec_module(server)
MAIN = {'execution_mode': 'main_session', 'self_execution_reason': 'Bounded fixture action'}
SUB = {'execution_mode': 'subagent', 'model': 'fixture', 'reasoning_effort': 'high',
       'tool_identifier': 'collaboration.spawn_agent', 'dispatch_text': 'Fixture dispatch ' * 1000}
LONG = 'Unique complete evidence retained in explicit queries. ' * 500


class SelectiveWireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='selective-wire-')
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        server.LEGACY_DB_PATH = Path(self.temp.name) / 'absent.sqlite3'
        self.seen = set()
        init = self.rpc('init_task_network', task_title='Selective fixture', tasks=[
            {'id': 'work', 'title': 'Worker', 'assignment': SUB},
            {'id': 'other', 'title': 'Other', 'assignment': MAIN}])
        self.sid = init['structuredContent']['session_id']
        self.iid = init['structuredContent']['task_instance_id']

    def rpc(self, name, **args):
        response = json.loads(server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': args}}))
        self.assertNotIn('error', response)
        result = response['result']
        self.assertFalse(result.get('isError'), result)
        if name in server._MUTATION_TOOLS:
            self.seen.add(name)
            self.assertTrue(result['structuredContent']['receipt'])
            for key in ('progress', 'session_work', 'nodes', 'summary', 'evidence', 'assignment'):
                self.assertNotIn(key, result['structuredContent'])
            self.assertLess(len(json.dumps(result)), 3000)
            self.assertNotIn(LONG, json.dumps(result))
        return result

    def write(self, name, **args):
        return self.rpc(name, session_id=self.sid, task_instance_id=self.iid, **args)['structuredContent']

    def read(self, **args):
        return self.rpc('get_progress', session_id=self.sid, task_instance_id=self.iid, **args)['structuredContent']

    def test_mutation_receipts_have_routing_identity_and_never_contain_snapshot(self):
        started = self.write('update_task', task_id='work', execution_status='running', assignment=SUB)
        aid = started['attempt_id']
        self.assertEqual(started['affected']['id'], 'work')
        self.write('bind_task_agent', task_id='work', attempt_id=aid, agent_name='fixture-worker')
        submitted = self.write('update_task', task_id='work', execution_status='finished', outcome='open',
                   summary=LONG, evidence=['fixture://' + LONG], attempt_id=aid)
        self.assertEqual(submitted['affected']['review_status'], 'pending')
        self.write('review_task', task_id='work', attempt_id=aid, decision='accept', summary=LONG,
                   evidence=['fixture://' + LONG], reviewer=MAIN)
        revision = self.read()['state_revision']
        self.write('review_stage_inventory', task_id='work', attempt_id=aid, expected_state_revision=revision,
                   decisions=[{'task_id':'work','decision':'continue','reason':'Continue open work'},
                              {'task_id':'other','decision':'continue','reason':'Still planned'}])
        self.write('reassign_task', task_id='other', assignment=MAIN, reason='Fixture adjustment', reviewer=MAIN)
        self.write('decompose_task', task_id='other', tasks=[{'id': 'child', 'title': 'Child', 'assignment': MAIN}])
        self.write('update_task_context', goal=LONG[:12000], constraints=['Fixture'], acceptance_criteria=['Proof'],
                   resume_note=LONG[:12000], reason='Context fixture')
        peer = self.write('route_request', request_id='peer', request_text=LONG[:12000], task_title='Peer',
                          mode='parallel', reason='Independent fixture', acceptance_criteria=['Proof'])
        self.assertNotEqual(peer['request_task_instance_id'], self.iid)
        self.write('cancel_request', request_id='peer', reason='Unused fixture')
        self.write('route_request', request_id='queued', request_text='Queued request', task_title='Queued',
                   mode='queue', reason='FIFO fixture', acceptance_criteria=['Proof'])
        self.write('update_request', request_id='queued', reason='Fixture revision', task_title='Queued revised')
        rebuilt = self.write('rebuild_task_network', reason='Explicit fixture replan')
        self.assertEqual(rebuilt['epoch'], 1)
        self.assertNotEqual(rebuilt['root_id'], 'root')
        server.decompose_task(self.sid, rebuilt['root_id'], [{'id': 'last', 'title': 'Last fixture', 'assignment': MAIN}], task_instance_id=self.iid)
        server.update_task(self.sid, 'last', 'running', assignment=MAIN, task_instance_id=self.iid)
        server.update_task(self.sid, 'last', 'finished', outcome='resolved', summary='Done',
                           attempt_id=server._read(self.sid)['nodes']['last']['attempt_id'], task_instance_id=self.iid)
        server.update_task(self.sid, rebuilt['root_id'], 'finished', outcome='resolved', summary='Accepted fixture', evidence=['fixture://all'], assignment=MAIN, task_instance_id=self.iid)
        activated = self.write('activate_request', request_id='queued')
        self.assertNotEqual(activated['task_instance_id'], self.iid)
        linear = self.rpc('init_progress', steps=['One legacy step'])['structuredContent']
        legacy_start = self.rpc('update_step', session_id=linear['session_id'], step_index=0,
                                status='in_progress', assignment=MAIN)['structuredContent']
        self.assertTrue(legacy_start['attempt_id'])
        self.rpc('clear_progress', session_id=linear['session_id'])
        # Both audit mutations keep their full evidence out of acknowledgements.
        init = self.rpc('init_task_network', task_title='Audit wire', tasks=[
            {'id':'target','title':'Target','assignment':MAIN}, {'id':'later','title':'Later','assignment':MAIN}])['structuredContent']
        sid, iid = init['session_id'], init['task_instance_id']
        target = self.rpc('update_task', session_id=sid, task_instance_id=iid, task_id='target', execution_status='running', assignment=MAIN)['structuredContent']['attempt_id']
        peer = self.rpc('route_request', session_id=sid, task_instance_id=iid, request_id='transfer', request_text='Transfer', task_title='Transfer', mode='parallel', reason='Isolated', acceptance_criteria=['Delivery'])['structuredContent']['request_task_instance_id']
        server.decompose_task(sid, 'root', [{'id':'cancelled','title':'Cancelled','assignment':SUB}], task_instance_id=peer)
        server.update_task(sid, 'cancelled', 'cancelled', summary='Transferred', task_instance_id=peer)
        server.update_task(sid, 'root', 'finished', outcome='open', summary='Source ended', assignment=MAIN, task_instance_id=peer)
        binding = server.get_progress(sid, task_instance_id=peer)['structuredContent']['progress']['closure_review_binding']
        self.rpc('review_task_closure', session_id=sid, task_instance_id=peer, task_id='root', review_id='continue', decision='continue', summary=LONG, evidence=[LONG], reviewer=MAIN, **binding)
        self.rpc('handoff_request', session_id=sid, task_instance_id=iid, request_id='transfer', handoff_id='transfer', source_task_instance_id=peer, summary=LONG, evidence=[LONG], reviewer=MAIN, mappings=[{'source_task_id':'cancelled','source_attempt_id':None,'target_task_instance_id':iid,'target_epoch':0,'target_task_id':'target','target_attempt_id':target}], **binding)
        self.assertEqual(self.seen, server._MUTATION_TOOLS)

    def test_overview_tree_and_tasks_are_distinct_explicit_layers(self):
        data = server._read(self.sid)
        data['nodes']['work'].update(summary=LONG, evidence=[LONG], title=LONG)
        for i in range(65):
            child = deepcopy(data['nodes']['other']); child.update(id=f'x{i}', parent_id='other', children=[])
            data['nodes'][child['id']] = child
            data['nodes']['other']['children'].append(child['id'])
        server._write(self.sid, data)
        before = deepcopy(server._read(self.sid))
        overview = self.read()
        self.assertEqual(overview['view'], 'overview')
        self.assertNotIn('nodes', overview['progress'])
        self.assertNotIn(LONG, json.dumps(overview))
        self.assertNotIn('session_work', overview)
        shallow = self.read(view='tree', depth=0)['progress']
        self.assertEqual(len(shallow['nodes']), 1)
        self.assertTrue(shallow['depth_limited'])
        first = self.read(view='tree', task_id='other', depth=1, limit=20)
        revision = first['state_revision']
        ids = [n['id'] for n in first['progress']['nodes']]
        offset = first['progress']['pagination']['next_offset']
        while offset is not None:
            page = self.read(view='tree', task_id='other', depth=1, limit=20, offset=offset, expected_state_revision=revision)
            ids.extend(n['id'] for n in page['progress']['nodes'])
            offset = page['progress']['pagination']['next_offset']
        self.assertEqual(len(ids), 66)
        self.assertEqual(len(set(ids)), 66)
        details = self.read(view='tasks', task_ids=['work'])['progress']['nodes'][0]
        self.assertEqual(details['summary'], LONG)
        self.assertEqual(details['evidence'], [LONG])
        self.assertNotIn('attempts', details)
        title = self.read(view='tree', depth=1)['progress']['nodes'][1]
        self.assertTrue(title['title_truncated'])
        self.assertEqual(len(title['title']), 200)
        self.assertEqual(server._read(self.sid), before)

    def test_records_full_parallel_history_and_epoch_scopes_do_not_drop_or_mix_data(self):
        data = server._read(self.sid)
        data['nodes']['work']['attempts'] = [{'attempt_id': f'a{i}', 'summary': LONG + str(i)} for i in range(7)]
        data['nodes']['work']['invalidated_results'] = [{'summary': LONG}]
        server._write(self.sid, data)
        page = self.read(view='records', task_id='work', offset=2, limit=3)['progress']
        self.assertEqual([r['attempt_id'] for r in page['records']], ['a2', 'a3', 'a4'])
        self.assertTrue(page['pagination']['has_more'])
        full = self.read(view='full')['progress']
        compatible = self.read(include_history=True)['progress']
        self.assertEqual(full, compatible)
        self.assertEqual(next(n for n in full['nodes'] if n['id'] == 'work')['attempts'], data['nodes']['work']['attempts'])
        peer = self.write('route_request', request_id='peer', request_text='Peer original', task_title='Peer', mode='parallel', reason='Independent', acceptance_criteria=['Proof'])
        peer_full = self.rpc('get_progress', session_id=self.sid, task_instance_id=peer['request_task_instance_id'], view='full')['structuredContent']['progress']
        self.assertNotIn('work', [n['id'] for n in peer_full['nodes']])
        self.write('rebuild_task_network', reason='Next epoch')
        archived = self.read(view='history', history_kind='history', limit=1)['progress']
        self.assertEqual(archived['pagination']['total'], 1)
        self.assertEqual(archived['records'][0]['epoch'], 0)
        self.assertEqual(next(n for n in archived['records'][0]['nodes'].values() if n['id'] == 'work')['attempts'], data['nodes']['work']['attempts'])
        self.assertIsNotNone(server._read(self.sid)['session_work_state']['instances'][peer['request_task_instance_id']])

    def test_bad_pagination_selection_and_revision_do_not_change_task_data(self):
        before = deepcopy(server._read(self.sid))
        for args in ({'limit': 0}, {'limit': 201}, {'offset': -1}, {'depth': True}, {'depth': 9},
                     {'view': 'tasks'}, {'view': 'tasks', 'task_ids': ['missing']},
                     {'view': 'records'}, {'view': 'records', 'task_id': 'missing'},
                     {'view': 'history', 'history_kind': 'missing'}, {'view': 'tree', 'task_id': 'missing'},
                     {'view': 'tree', 'include_history': True}, {'expected_state_revision': -1}, {'expected_state_revision': 999999}):
            result = json.loads(server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'get_progress', 'arguments': {'session_id': self.sid, **args}}}))['result']
            self.assertTrue(result.get('isError'), args)
            self.assertEqual(server._read(self.sid), before)

    def test_context_requests_are_paginated_and_current_task_context_is_complete(self):
        self.write('update_task_context', goal='Current goal', constraints=['Limit'], acceptance_criteria=['Proof'],
                   resume_note='Resume worker', reason='Record explicit context')
        for i in range(3):
            self.write('route_request', request_id=f'queued{i}', request_text=f'Original {i}', task_title=f'Q{i}',
                       mode='queue', reason='FIFO', acceptance_criteria=['Proof'])
        context = self.read(view='context', offset=1, limit=2)['progress']
        self.assertEqual(context['task_context']['resume_note'], 'Resume worker')
        self.assertEqual(len(context['session_work']['requests']), 2)
        self.assertTrue(context['session_work']['pagination']['has_more'])
        self.assertFalse(context['session_work']['can_finish_session'])
        self.assertNotIn('nodes', context)
        revisions = self.read(view='history', history_kind='context_revisions')['progress']
        self.assertTrue(revisions['history_included'])

    def test_ready_dependency_evidence_is_returned_once_without_repeated_summary(self):
        data = server._read(self.sid)
        data['nodes']['work'].update(execution_status='finished', outcome='resolved', review_status='accepted',
                                     summary=LONG, evidence=['fixture://dependency'])
        data['nodes']['other']['depends_on'] = ['work']
        data['nodes']['other']['dependency_policy'] = 'finished'
        for i in range(3):
            extra = deepcopy(data['nodes']['other']); extra.update(id=f'consumer{i}', parent_id='other', children=[], depends_on=[])
            data['nodes'][extra['id']] = extra; data['nodes']['other']['children'].append(extra['id'])
        server._write(self.sid, data)
        result = self.rpc('get_ready_tasks', session_id=self.sid)['structuredContent']
        self.assertEqual(len(result['ready_tasks']), 3)
        self.assertEqual(result['dependency_evidence']['work']['summary'], LONG)
        self.assertEqual(result['dependency_evidence']['work']['evidence'], ['fixture://dependency'])
        for node in result['ready_tasks']:
            self.assertNotIn('summary', node['dependencies'][0])
            self.assertEqual(node['dependencies'][0]['inherited_from'], 'other')
            self.assertEqual(node['dependencies'][0]['dependency_policy'], 'finished')
        self.assertEqual(json.dumps(result).count(LONG), 1)

    def test_expired_split_review_is_actionable_and_empty_render_bootstraps(self):
        data = server._read(self.sid)
        data['nodes']['work'].update(execution_status='running', started_at='2000-01-01T00:00:00+00:00')
        server._write(self.sid, data)
        self.assertEqual(self.read()['progress']['split_review_due_ids'], ['work'])
        tree = self.read(view='tree')['progress']
        self.assertTrue(next(n for n in tree['nodes'] if n['id'] == 'work')['split_review_due'])
        node = self.read(view='tasks', task_ids=['work'])['progress']['nodes'][0]
        self.assertGreater(node['split_review']['elapsed_seconds'], 600)
        empty = self.rpc('render_progress', session_id='00000000-0000-4000-8000-000000000011')['structuredContent']
        self.assertTrue(empty['bootstrap'])
        self.assertNotIn('task_instance_id', empty)
        self.assertIsNone(self.rpc('get_progress_view', session_id=empty['session_id'])['structuredContent']['progress'])

    def test_render_bootstrap_app_visibility_and_ready_read_exclude_whole_tree(self):
        rendered = self.rpc('render_progress', session_id=self.sid)['structuredContent']
        self.assertTrue(rendered['bootstrap'])
        self.assertEqual(rendered['view_tool'], 'get_progress_view')
        self.assertNotIn('progress', rendered)
        self.assertLess(len(json.dumps(rendered)), 1000)
        tools = {t['name']: t for t in server.TOOLS}
        self.assertEqual(tools['get_progress_view']['_meta']['ui']['visibility'], ['app'])
        self.assertNotIn('resourceUri', tools['get_progress_view']['_meta']['ui'])
        view = self.rpc('get_progress_view', session_id=self.sid)['structuredContent']['progress']
        self.assertFalse(view['records_included'])
        self.assertEqual(len(view['nodes']), 3)
        ready = self.rpc('get_ready_tasks', session_id=self.sid, limit=1)['structuredContent']
        self.assertNotIn('progress', ready)
        self.assertNotIn('session_work', ready)
        self.assertEqual(len(ready['ready_tasks']), 1)
        self.assertTrue(ready['pagination']['has_more'])


if __name__ == '__main__':
    unittest.main()
