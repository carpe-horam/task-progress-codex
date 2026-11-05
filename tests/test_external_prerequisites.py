"""Retiring manual prerequisites must preserve old tasks and dependency gates."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dependency_only_server', Path(__file__).resolve().parents[1] / 'plugins/task-progress/server.py')
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, 'home', return_value=Path(home)):
    spec.loader.exec_module(server)
MAIN = {'execution_mode': 'main_session', 'self_execution_reason': 'Isolated dependency compatibility fixture'}


class RemovedExternalPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        server.SESSION_DIR = Path(self.tmp.name)
        server.LEGACY_DB_PATH = Path(self.tmp.name) / 'missing.sqlite'
        p = server.init_task_network('Task', tasks=[
            {'id': 'a', 'title': 'A', 'assignment': MAIN},
            {'id': 'b', 'title': 'B', 'assignment': MAIN, 'depends_on': ['a']},
        ])['structuredContent']['progress']
        self.sid, self.iid = p['session_id'], p['task_instance_id']

    def read(self, **kwargs):
        return server.get_progress(self.sid, task_instance_id=self.iid, **kwargs)['structuredContent']['progress']

    def nodes(self):
        return {n['id']: n for n in self.read(view='full')['nodes']}

    def test_new_tasks_have_dependency_state_without_manual_prerequisites(self):
        for nodes in (self.read(view='full')['nodes'], self.read(view='tree')['nodes'],
                      self.read(view='tasks', task_ids=['a', 'b'])['nodes'],
                      server.get_ready_tasks(self.sid, self.iid)['structuredContent']['ready_tasks']):
            for node in nodes:
                self.assertFalse(any(k.startswith('external_prerequisite') or k == 'actionable' for k in node))
        self.assertEqual((self.nodes()['a']['graph_ready'], self.nodes()['b']['graph_ready']), (True, False))
        self.assertIsNone(self.nodes()['root']['outcome'])

    def test_old_records_remain_readable_and_do_not_gate_dependencies_or_retry(self):
        data = server._read(self.sid)
        # Old blocked claims cannot stop A; old satisfied claims cannot release B.
        for task_id, status in (('a', 'blocked'), ('b', 'satisfied')):
            data['nodes'][task_id].update(external_prerequisite_status=status,
                external_prerequisite_reviews=[{'status': status, 'source_attempt_id': 'old-attempt',
                    'reason': 'Historical manual record', 'evidence': ['fixture://old-record']}])
        server._write(self.sid, data)
        frozen = server._session_file(self.sid).read_bytes()
        before = deepcopy(data['nodes'])
        self.assertEqual((self.nodes()['a']['ready'], self.nodes()['b']['ready']), (True, False))
        self.assertEqual(server._session_file(self.sid).read_bytes(), frozen)
        server.snapshot_task_network(self.sid, self.iid, 'legacy', 'Preserve historical data')
        self.assertEqual(server._read(self.sid)['plan_snapshots'][0]['nodes'], before)
        snapshot = self.read(view='full')['plan_snapshots'][0]['nodes']
        self.assertEqual(snapshot['a']['external_prerequisite_reviews'], before['a']['external_prerequisite_reviews'])
        history = self.read(view='history', history_kind='plan_snapshots')['records']
        self.assertEqual(history[0]['nodes']['a']['external_prerequisite_reviews'],
                         before['a']['external_prerequisite_reviews'])
        compact_nodes = server.get_progress_view(self.sid)['structuredContent']['progress']['nodes']
        for nodes in (compact_nodes, self.read(view='tasks', task_ids=['a', 'b'])['nodes']):
            for node in nodes:
                self.assertFalse(node['records_included'])
                self.assertNotIn('external_prerequisite_reviews', node)
        self.assertEqual(server._read(self.sid)['nodes'], before)
        server.update_task(self.sid, 'a', 'running', assignment=MAIN, task_instance_id=self.iid)
        attempt = server._read(self.sid)['nodes']['a']['attempt_id']
        server.update_task(self.sid, 'a', 'finished', outcome='open', summary='Work remains',
                           attempt_id=attempt, task_instance_id=self.iid)
        self.assertFalse(self.nodes()['b']['ready'])
        server.update_task(self.sid, 'a', 'running', retry=True, assignment=MAIN, task_instance_id=self.iid)
        attempt = server._read(self.sid)['nodes']['a']['attempt_id']
        server.update_task(self.sid, 'a', 'finished', outcome='resolved', summary='Dependency completed',
                           attempt_id=attempt, task_instance_id=self.iid)
        nodes = self.nodes()
        self.assertTrue(nodes['b']['ready'])
        self.assertIsNone(nodes['root']['outcome'])
        self.assertEqual(nodes['a']['external_prerequisite_reviews'], before['a']['external_prerequisite_reviews'])
        self.assertNotIn('external_prerequisite_stale', nodes['a'])

    def test_retired_rpc_and_record_kind_cannot_modify_tasks(self):
        listed = json.loads(server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}))['result']['tools']
        self.assertNotIn('set_external_prerequisite', {tool['name'] for tool in listed})
        frozen = server._session_file(self.sid).read_bytes()
        result = json.loads(server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'set_external_prerequisite', 'arguments': {'session_id': self.sid,
                'task_instance_id': self.iid, 'task_id': 'a', 'status': 'satisfied'}}}))
        self.assertTrue('error' in result or result.get('result', {}).get('isError'))
        with self.assertRaises(server.ProgressError):
            self.read(view='records', task_id='a', record_kind='external_prerequisite_reviews')
        self.assertEqual(server._session_file(self.sid).read_bytes(), frozen)


if __name__ == '__main__':
    unittest.main()
