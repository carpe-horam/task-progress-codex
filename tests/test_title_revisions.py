"""Display-only title revisions preserve execution, graph and historical facts."""
import importlib.util
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('title_revision_server', Path(__file__).resolve().parents[1] / 'plugins/task-progress/server.py')
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as home, patch.object(Path, 'home', return_value=Path(home)):
    spec.loader.exec_module(server)
MAIN = {'execution_mode': 'main_session', 'self_execution_reason': 'Isolated fixture'}

class TitleRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        server.SESSION_DIR = Path(self.tmp.name)
        server.LEGACY_DB_PATH = Path(self.tmp.name) / 'missing.sqlite'
        p = server.init_task_network('Original root', tasks=[
            {'id': 'a', 'title': 'Original A', 'assignment': MAIN},
            {'id': 'b', 'title': 'Original B', 'assignment': MAIN, 'depends_on': ['a']}])['structuredContent']['progress']
        self.sid, self.iid = p['session_id'], p['task_instance_id']

    def data(self):
        return server._read(self.sid)

    def rename(self, nid='a', title='Display A', **extra):
        p = self.data()
        args = dict(session_id=self.sid, task_instance_id=self.iid, task_id=nid, display_title=title,
                    reason='Clarify scope without changing work', expected_epoch=p['epoch'], expected_revision=p['revision'])
        args.update(extra)
        return server.revise_task_title(**args)

    def assert_only_metadata_changed(self, before, after):
        before, after = deepcopy(before), deepcopy(after)
        for p in (before, after):
            for key in ('revision', 'state_revision', 'updated_at'): p.pop(key, None)
            for node in p['nodes'].values(): node.pop('title_revisions', None)
        self.assertEqual(before, after)

    def test_pending_running_terminal_and_root_keep_graph_status_evidence_and_acceptance(self):
        for status in ('pending', 'running', 'finished', 'failed', 'cancelled'):
            with self.subTest(status=status):
                if status == 'running':
                    server.update_task(self.sid, 'a', 'running', assignment=MAIN, task_instance_id=self.iid)
                elif status in ('finished', 'failed', 'cancelled'):
                    # Exercise independent terminal states via real attempts.
                    current = self.data()['nodes']['a']
                    if current['execution_status'] != 'running':
                        server.update_task(self.sid, 'a', 'running', assignment=MAIN, task_instance_id=self.iid, retry=True,
                                           attempt_id=current['attempt_id'])
                    node = self.data()['nodes']['a']
                    server.update_task(self.sid, 'a', status, outcome='resolved' if status=='finished' else None,
                                       summary='Evidence must survive', evidence=['fixture://proof'], attempt_id=node['attempt_id'], task_instance_id=self.iid)
                before = self.data(); fingerprint = server._result_fingerprint(before)
                self.rename(title='Title in ' + status)
                after = self.data()
                self.assert_only_metadata_changed(before, after)
                self.assertEqual(fingerprint, server._result_fingerprint(after))
                self.assertEqual(after['nodes']['a']['title'], 'Original A')
                self.assertNotIn('display_title', after['nodes']['a'])
        before=self.data(); self.rename('root', 'Current root')
        self.assert_only_metadata_changed(before,self.data())
        p=server.get_progress(self.sid, task_instance_id=self.iid)['structuredContent']['progress']
        self.assertEqual(p['display_title'], 'Current root')

    def test_rejects_empty_stale_wrong_instance_epoch_and_unknown_nodes_atomically(self):
        p=self.data(); frozen=server._session_file(self.sid).read_bytes()
        for extra in ({'display_title':'  '}, {'display_title':None}, {'reason':'  '},
                      {'expected_epoch':p['epoch']+1}, {'expected_revision':p['revision']+1},
                      {'task_instance_id':''}, {'task_instance_id':'obsolete'}, {'task_id':'missing'}, {'expected_revision':True}):
            with self.subTest(extra=extra), self.assertRaises(server.ProgressError): self.rename(**extra)
            self.assertEqual(frozen,server._session_file(self.sid).read_bytes())
        self.rename(); frozen=server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError): self.rename()
        with self.assertRaises(server.ProgressError): self.rename(title='Second',expected_revision=p['revision'])
        self.assertEqual(frozen,server._session_file(self.sid).read_bytes())
        server.rebuild_task_network(self.sid,'New epoch',task_instance_id=self.iid)
        with self.assertRaises(server.ProgressError): self.rename(expected_epoch=0)
        with self.assertRaises(server.ProgressError): self.rename()

    def test_attempt_titles_and_snapshots_freeze_while_projection_and_ledger_are_readonly(self):
        server.snapshot_task_network(self.sid,self.iid,'original','Before title changes')
        self.rename()
        server.update_task(self.sid,'a','running',assignment=MAIN,task_instance_id=self.iid)
        aid=self.data()['nodes']['a']['attempt_id']
        self.assertEqual(self.data()['nodes']['a']['attempts'][0]['title'],'Display A')
        server.snapshot_task_network(self.sid,self.iid,'running','During renamed attempt')
        snapshots=deepcopy(self.data()['plan_snapshots'])
        self.rename(title='Current A')
        self.assertEqual(snapshots,self.data()['plan_snapshots'])
        self.assertEqual(self.data()['nodes']['a']['attempts'][0]['title'],'Display A')
        frozen=server._session_file(self.sid).read_bytes()
        p=server.get_progress(self.sid,task_instance_id=self.iid,view='full')['structuredContent']['progress']
        a=next(n for n in p['nodes'] if n['id']=='a'); b=next(n for n in p['nodes'] if n['id']=='b')
        self.assertEqual((a['title'],a['display_title']),('Original A','Current A'))
        self.assertEqual(b['dependencies'][0]['display_title'],'Current A')
        self.assertEqual(p['plan_snapshots'][0]['nodes']['a']['title'],'Original A')
        self.assertNotIn('title_revisions',p['plan_snapshots'][0]['nodes']['a'])
        self.assertEqual(p['plan_snapshots'][1]['nodes']['a']['display_title'],'Display A')
        self.assertEqual(server.get_progress(self.sid,task_instance_id=self.iid,view='records',task_id='a',record_kind='title_revisions')['structuredContent']['progress']['records'],self.data()['nodes']['a']['title_revisions'])
        tree=server.get_progress(self.sid,task_instance_id=self.iid,view='tree')['structuredContent']['progress']['nodes']
        self.assertEqual(next(n for n in tree if n['id']=='a')['title_revision_count'],2)
        self.assertEqual(frozen,server._session_file(self.sid).read_bytes())
        with self.assertRaises(server.ProgressError):
            server.revise_task_plan(self.sid,self.iid,'a','Cannot change running structure',0,self.data()['revision'],depends_on=[])
        server.update_task(self.sid,'a','finished','open','Preserved',attempt_id=aid,task_instance_id=self.iid)
        server.rebuild_task_network(self.sid,'Next plan',task_instance_id=self.iid)
        old=server.get_progress(self.sid,task_instance_id=self.iid,view='history')['structuredContent']['progress']['records'][0]
        self.assertEqual(old['nodes']['a']['display_title'],'Current A')
        self.assertEqual(old['nodes']['a']['title'],'Original A')

    def test_atomic_root_has_no_structural_or_execution_change_and_tool_is_registered(self):
        p=server.init_task_network('Atomic root',atomic_reason='One operation')['structuredContent']['progress']
        self.sid,self.iid=p['session_id'],p['task_instance_id']; before=self.data()
        self.rename('root','Renamed atomic')
        self.assert_only_metadata_changed(before,self.data())
        self.assertIn('revise_task_title',server.TOOL_HANDLERS)
        result=server._wire_result('revise_task_title',{'task_id':'root'},server.get_progress_view(self.sid))
        self.assertTrue(result['structuredContent']['receipt'])
        self.assertNotIn('progress',result['structuredContent'])

    def test_completed_root_acceptance_and_bound_scope_survive_title_changes(self):
        for nid in ('a','b','root'):
            if nid != 'root':
                server.update_task(self.sid,nid,'running',assignment=MAIN,task_instance_id=self.iid)
            aid=self.data()['nodes'][nid]['attempt_id']
            server.update_task(self.sid,nid,'finished','resolved','Accepted delivery',evidence=['fixture://proof'],
                               attempt_id=aid,task_instance_id=self.iid,assignment=MAIN if nid=='root' else None)
        before=self.data()
        binding=server._stage_inventory_binding(before,'root')
        self.rename('a','Finished A'); self.rename('root','Finished root display')
        self.assertEqual(binding,server._stage_inventory_binding(self.data(),'root'))
        self.assert_only_metadata_changed(before,self.data())
        p=server.get_progress(self.sid,task_instance_id=self.iid)['structuredContent']['progress']
        self.assertEqual(p['status'],'completed')
        self.assertEqual(p['accepted_resolved_leaf_count'],2)

if __name__ == '__main__': unittest.main()
