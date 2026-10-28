from pathlib import Path
from unittest.mock import patch
import importlib.util,json,tempfile,hashlib
BASE=Path('/Users/machengling/.codex/worktrees/3e36/task-process')
OUT=Path('/tmp/task-progress-thread-audit-20261028')
with tempfile.TemporaryDirectory(prefix='contract-isolated-') as td:
 with patch.object(Path,'home',return_value=Path(td)):
  spec=importlib.util.spec_from_file_location('contract_audit_server',BASE/'plugins/task-progress/server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.SESSION_DIR=Path(td)/'sessions';m.SESSION_DIR.mkdir()
 p=json.loads((OUT/'progress-snapshot.json').read_text()); data={**p,'nodes':{n['id']:n for n in p['nodes']}}
 before=hashlib.sha256((OUT/'progress-snapshot.json').read_bytes()).hexdigest()
 recomputed=m._network_progress_payload(data)
 keys=['completed','total','percent','resolved_leaf_count','pending_review_count','executing_count','blocked_count','ready_count','status','root_status']
 results={'snapshot_hash':before,'snapshot_counts':{k:p[k] for k in keys},'recomputed_counts':{k:recomputed[k] for k in keys},'historical_acceptance':{}}
 assert results['snapshot_counts']==results['recomputed_counts']
 # Review changes only copied temporary in-memory data, never writes the target session.
 for n in data['nodes'].values():
  if n.get('review_status')=='pending':n['review_status']='accepted';n['outcome']=n['submission']['outcome']
 reviewed=m._network_progress_payload(data)
 results['clone_all_seven_accepted']={k:reviewed[k] for k in keys}
 results['clone_ready']=reviewed['ready_tasks'];assert 'remaining-sdk-optional-tools' in reviewed['ready_tasks'];assert reviewed['status']=='in_progress'
 # Minimal actual backend submission fixture, entirely isolated.
 sub={'execution_mode':'subagent','model':'fixture','reasoning_effort':'high','tool_identifier':'fixture.spawn','dispatch_text':'Inspect isolated fixture'}
 r=m.init_task_network('Pending results fixture',tasks=[{'id':'a','title':'A','assignment':sub},{'id':'b','title':'B','assignment':sub}])['structuredContent']; sid=r['session_id'];inst=r['progress']['task_instance_id']
 for nid in ('a','b'):
  r=m.update_task(sid,nid,'running',assignment=sub,task_instance_id=inst)['structuredContent'];aid=next(n for n in r['progress']['nodes'] if n['id']==nid)['attempt_id'];m.bind_task_agent(sid,nid,aid,'fixture-'+nid,inst);m.update_task(sid,nid,'finished',outcome='resolved',attempt_id=aid,summary='Isolated submission',evidence=['fixture://result'],task_instance_id=inst)
 pending=m.get_progress(sid)['structuredContent']['progress'];assert pending['status']=='awaiting_review'; assert pending['pending_review_count']==2
 (OUT/'contract-pending-fixture.json').write_text(json.dumps(pending,ensure_ascii=False,indent=2))
 results['fixture_counts']={k:pending[k] for k in keys}
 assert hashlib.sha256((OUT/'progress-snapshot.json').read_bytes()).hexdigest()==before
 (OUT/'contract-backend-evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))
