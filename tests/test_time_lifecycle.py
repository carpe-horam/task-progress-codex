"""Record-level lifecycle invariants against temporary sessions only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('time_lifecycle_server', ROOT / 'plugins/task-progress/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
MAIN = {'execution_mode':'main_session','self_execution_reason':'Isolated deterministic lifecycle regression'}

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='task-progress-lifecycle-')
        self.old = server.SESSION_DIR
        server.SESSION_DIR = Path(self.temp.name)
        with patch.object(server,'_now',return_value='2026-10-25T00:00:00+00:00'):
            r=server.init_task_network('Time lifecycle',tasks=[{'id':'a','title':'A','assignment':MAIN},{'id':'b','title':'B','assignment':MAIN}])
        self.sid=r['structuredContent']['session_id']
    def tearDown(self):
        server.SESSION_DIR=self.old
        self.temp.cleanup()
    def node(self,id):
        return next(n for n in server.get_progress_view(self.sid,include_history=True)['structuredContent']['progress']['nodes'] if n['id']==id)
    def change(self,id,status,time,**kw):
        with patch.object(server,'_now',return_value='2026-10-25T00:'+time+'+00:00'):
            return server.update_task(self.sid,id,status,**kw)
    def test_retry_preserves_execution_and_invalidates_synthesis_without_reusing_time(self):
        self.change('a','running','00:10',assignment=MAIN)
        first=self.node('a')['attempt_id']
        self.change('a','finished','00:20',attempt_id=first,outcome='resolved',summary='A result',evidence=['fixture://a'])
        self.change('b','cancelled','00:25',summary='B unnecessary')
        self.assertEqual(self.node('b')['started_at'],'2026-10-25T00:00:25+00:00')
        self.assertEqual(self.node('b')['time_boundary_sources']['started_at'],'unknown_start_fallback')
        self.assertEqual(self.node('b')['finished_at'],'2026-10-25T00:00:25+00:00')
        self.change('root','finished','00:30',assignment=MAIN,outcome='open',summary='Child cancelled')
        synthesis=self.node('root')
        self.assertEqual(synthesis['attempts'][0]['kind'],'synthesis')
        self.assertEqual(synthesis['started_at'],'2026-10-25T00:00:30+00:00')
        self.change('a','running','01:00',assignment=MAIN,retry=True)
        second=self.node('a')
        self.assertNotEqual(first,second['attempt_id'])
        self.assertEqual(second['started_at'],'2026-10-25T00:01:00+00:00')
        self.assertIsNone(second['finished_at'])
        self.assertEqual(second['attempts'][0]['started_at'],'2026-10-25T00:00:10+00:00')
        self.assertEqual(second['attempts'][0]['finished_at'],'2026-10-25T00:00:20+00:00')
        root=self.node('root')
        self.assertIsNone(root['started_at']);self.assertIsNone(root['finished_at'])
        self.assertEqual(root['attempts'][0]['started_at'],synthesis['started_at'])
        self.assertEqual(root['invalidated_results'][0]['finished_at'],synthesis['finished_at'])
    def test_running_split_retains_cancelled_attempt_with_no_fabricated_group_start(self):
        self.change('a','running','00:10',assignment=MAIN)
        old=self.node('a')['attempt_id']
        with patch.object(server,'_now',return_value='2026-10-25T00:00:25+00:00'):
            server.decompose_task(self.sid,'a',[{'id':'a1','title':'One','assignment':MAIN},{'id':'a2','title':'Two','assignment':MAIN}],attempt_id=old)
        group=self.node('a')
        self.assertIsNone(group['started_at']);self.assertIsNone(group['finished_at']);self.assertIsNone(group['attempt_id'])
        self.assertEqual(group['attempts'][0]['execution_status'],'cancelled')
        self.assertEqual(group['attempts'][0]['finished_at'],'2026-10-25T00:00:25+00:00')
        self.assertIsNone(self.node('a1')['started_at']);self.assertIsNone(self.node('a2')['finished_at'])

    def test_archive_fills_missing_bounds_without_changing_live_nodes(self):
        self.change('a','running','00:10',assignment=MAIN)
        first=self.node('a')['attempt_id']
        self.change('a','finished','00:20',attempt_id=first,outcome='resolved',summary='A result',evidence=['fixture://a'])
        raw=server._read(self.sid)
        snapshot=server._task_archive_snapshot(raw,archived_at='2026-10-25T00:02:00+00:00')
        nodes={n['id']:n for n in snapshot['nodes']}
        self.assertEqual(nodes['a']['started_at'],'2026-10-25T00:00:10+00:00')
        self.assertEqual(nodes['a']['finished_at'],'2026-10-25T00:00:20+00:00')
        for key in ('root','b'):
            self.assertEqual(nodes[key]['started_at'],'2026-10-25T00:02:00+00:00')
            self.assertEqual(nodes[key]['finished_at'],'2026-10-25T00:02:00+00:00')
            self.assertEqual(nodes[key]['time_boundary_sources']['started_at'],'unknown_start_fallback')
            self.assertEqual(nodes[key]['time_boundary_sources']['finished_at'],'archive_fallback')
        self.assertIsNone(raw['nodes']['b']['started_at'])
        self.assertEqual(nodes['b']['execution_status'],'pending')

    def test_replan_closes_old_snapshot_and_preserves_fresh_pending_nodes(self):
        with patch.object(server,'_now',return_value='2026-10-25T00:03:00+00:00'):
            server.rebuild_task_network(self.sid,'New plan')
        raw=server._read(self.sid)
        old=raw['history'][-1]
        for item in old['nodes'].values():
            self.assertEqual(item['started_at'],old['closed_at'])
            self.assertEqual(item['finished_at'],old['closed_at'])
            self.assertEqual(item['time_boundary_sources']['finished_at'],'replan_fallback')
        self.assertIsNone(raw['nodes'][raw['root_id']]['started_at'])

if __name__=='__main__':unittest.main()
