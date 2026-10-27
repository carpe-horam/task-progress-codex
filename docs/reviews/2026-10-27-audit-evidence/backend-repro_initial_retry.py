import importlib.util,json
from pathlib import Path
root=Path('/Users/machengling/.codex/worktrees/4d11/task-process')
out=Path('/tmp/task-progress-audit-20261027-backend')
spec=importlib.util.spec_from_file_location('server',root/'plugins/task-progress/server.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
s.SESSION_DIR=out/'initial-retry-sessions';s.SESSION_DIR.mkdir(exist_ok=True)
args={'task_title':'Network-timeout retry fixture','atomic_reason':'One isolated operation','request_id':'same-initial-request'}
a=s.init_task_network(**args)['structuredContent'];b=s.init_task_network(**args)['structuredContent']
result={'same_arguments':args,'first_session_id':a['session_id'],'second_session_id':b['session_id'],'first_instance':a['progress']['task_instance_id'],'second_instance':b['progress']['task_instance_id'],'session_file_count':len(list(s.SESSION_DIR.glob('*.json'))),'second_history_count':len(b['progress']['task_history'])}
assert a['session_id']!=b['session_id']
assert a['progress']['task_instance_id']!=b['progress']['task_instance_id']
(out/'initial-retry-results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(result,indent=2,ensure_ascii=False))
