"""Manual external prerequisite claims must never grant graph execution or acceptance."""
import importlib.util
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('external_prerequisite_server', Path(__file__).resolve().parents[1] / 'plugins/task-progress/server.py')
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, 'home', return_value=Path(home)):
    spec.loader.exec_module(server)
MAIN = {'execution_mode': 'main_session', 'self_execution_reason': 'Isolated prerequisite fixture'}
REVIEWER = {'execution_mode': 'main_session', 'agent_name': 'Explicit fixture reviewer'}

class ExternalPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        server.SESSION_DIR = Path(self.tmp.name)
        server.LEGACY_DB_PATH = Path(self.tmp.name) / 'missing.sqlite'
        p = server.init_task_network('Task', tasks=[{'id':'a','title':'A','assignment':MAIN},
            {'id':'b','title':'B','assignment':MAIN,'depends_on':['a']}])['structuredContent']['progress']
        self.sid, self.iid = p['session_id'], p['task_instance_id']

    def data(self): return server._read(self.sid)
    def read(self, **kwargs): return server.get_progress(self.sid, task_instance_id=self.iid, **kwargs)['structuredContent']['progress']
    def nodes(self): return {n['id']:n for n in self.read(view='full')['nodes']}
    def record(self, status='satisfied', **extra):
        p=self.data()
        args=dict(session_id=self.sid,task_instance_id=self.iid,task_id='a',status=status,
                  reason='Reviewer inspected the relevant resource', evidence=['fixture://inspection'],reviewer=REVIEWER,
                  expected_epoch=p['epoch'],expected_revision=p['revision'])
        args.update(extra); return server.set_external_prerequisite(**args)

    def test_unknown_default_three_states_and_graph_ready_acceptance_unchanged(self):
        original=self.nodes()
        self.assertEqual(original['a']['external_prerequisite_status'],'unknown')
        self.assertIsNone(original['a']['external_prerequisite_review'])
        self.assertTrue(original['a']['graph_ready']); self.assertFalse(original['b']['graph_ready'])
        fingerprint=server._result_fingerprint(self.data())
        for status in ('blocked','satisfied','unknown'):
            self.record(status)
            a,b=self.nodes()['a'],self.nodes()['b']
            self.assertEqual(a['external_prerequisite_status'],status)
            self.assertEqual((a['ready'],a['graph_ready'],b['ready']),(True,True,False))
            self.assertEqual(a['actionable']['status'],'requires_on_site_judgment')
            self.assertEqual(server._result_fingerprint(self.data()),fingerprint)
            self.assertIsNone(a['outcome']); self.assertEqual(a['execution_status'],'pending')
        history=self.read(view='records',task_id='a',record_kind='external_prerequisite_reviews')['records']
        self.assertEqual([r['status'] for r in history],['blocked','satisfied','unknown'])
        self.assertEqual(history[0]['reviewer']['agent_name'],REVIEWER['agent_name'])
        tree=self.read(view='tree')['nodes']; self.assertEqual(next(n for n in tree if n['id']=='a')['external_prerequisite_status'],'unknown')
        compact=server.get_progress_view(self.sid)['structuredContent']['progress']['nodes']
        self.assertNotIn('external_prerequisite_reviews',next(n for n in compact if n['id']=='a'))

    def test_rejections_are_atomic(self):
        frozen=server._session_file(self.sid).read_bytes()
        for extra in ({'reason':''},{'evidence':[]},{'evidence':[' ']},{'reviewer':None},{'reviewer':{'execution_mode':'main_session'}},
                      {'reviewer':{'execution_mode':'subagent','agent_name':'Agent'}},{'reviewer':{'agent_name':1}},
                      {'expected_revision':99},{'expected_epoch':99},{'task_instance_id':'wrong'}, {'task_id':'wrong'},
                      {'source_attempt_id':'wrong'},{'status':'ready'},{'expected_revision':True}):
            with self.subTest(extra=extra),self.assertRaises(server.ProgressError): self.record(**extra)
            self.assertEqual(frozen,server._session_file(self.sid).read_bytes())
        p=self.data(); self.record()
        with self.assertRaises(server.ProgressError): self.record(expected_revision=p['revision'])

    def test_all_execution_states_and_retry_stales_attempt_bound_proof_preserving_history(self):
        server.update_task(self.sid,'a','running',assignment=MAIN,task_instance_id=self.iid)
        aid=self.data()['nodes']['a']['attempt_id']; self.record(source_attempt_id=aid)
        history=deepcopy(self.data()['nodes']['a']['external_prerequisite_reviews'])
        server.snapshot_task_network(self.sid,self.iid,'confirmed','Before retry')
        server.update_task(self.sid,'a','finished',outcome='open',summary='Work remains',attempt_id=aid,task_instance_id=self.iid)
        self.record('blocked',source_attempt_id=aid)
        old=deepcopy(self.data()['nodes']['a']['external_prerequisite_reviews'])
        server.update_task(self.sid,'a','running',assignment=MAIN,retry=True,task_instance_id=self.iid)
        a=self.nodes()['a']
        self.assertEqual(a['external_prerequisite_status'],'unknown'); self.assertTrue(a['external_prerequisite_stale'])
        self.assertEqual(a['external_prerequisite_review']['source_attempt_id'],aid)
        self.assertEqual(self.data()['nodes']['a']['external_prerequisite_reviews'],old)
        snap=self.read(view='full')['plan_snapshots'][0]['nodes']['a']
        self.assertEqual(snap['external_prerequisite_status'],'satisfied'); self.assertFalse(snap['external_prerequisite_stale'])
        self.assertEqual(snap['external_prerequisite_reviews'],history)
        aid=self.data()['nodes']['a']['attempt_id']
        server.update_task(self.sid,'a','failed',summary='Fixture failure',attempt_id=aid,task_instance_id=self.iid)
        self.record('unknown'); self.assertFalse(self.nodes()['a']['external_prerequisite_stale'])
        server.update_task(self.sid,'a','running',assignment=MAIN,retry=True,task_instance_id=self.iid)
        aid=self.data()['nodes']['a']['attempt_id']
        server.update_task(self.sid,'a','cancelled',summary='Fixture cancelled',attempt_id=aid,task_instance_id=self.iid)
        self.record('satisfied'); self.record('blocked',task_id='root')
        self.assertEqual(self.nodes()['a']['execution_status'],'cancelled')
        self.assertIsNone(self.nodes()['root']['outcome'])

    def test_plan_scoped_claim_survives_attempt_and_legacy_defaults_unknown(self):
        self.record()
        server.update_task(self.sid,'a','running',assignment=MAIN,task_instance_id=self.iid)
        self.assertEqual(self.nodes()['a']['external_prerequisite_status'],'satisfied')
        p=self.data(); p['nodes']['a'].pop('external_prerequisite_reviews'); p['nodes']['a'].pop('external_prerequisite_status'); server._write(self.sid,p)
        self.assertEqual(self.nodes()['a']['external_prerequisite_status'],'unknown')

if __name__=='__main__': unittest.main()
