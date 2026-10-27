import importlib.util, json, tempfile, uuid
from pathlib import Path

ROOT = Path('/Users/machengling/.codex/worktrees/4d11/task-process')
OUT = Path('/tmp/task-progress-audit-20261027-backend')
spec = importlib.util.spec_from_file_location('audit_server', ROOT / 'plugins/task-progress/server.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
s.SESSION_DIR = OUT / 'sessions'
s.SESSION_DIR.mkdir(exist_ok=True)
A = {'execution_mode':'main_session','model':'audit-first','reasoning_effort':'high'}
B = {**A,'model':'audit-second'}
def p(result): return result['structuredContent']['progress']
result = {}

sid = str(uuid.uuid4())
kwargs = dict(task_title='Original initialization', session_id=sid, atomic_reason='A single isolated audit fixture', request_id='request-A')
a = p(s.init_task_network(**kwargs))
new = p(s.rebuild_task_network(sid, 'A revised plan', task_instance_id=a['task_instance_id']))
s.decompose_task(sid, new['root_id'], [{'id':'revised-work','title':'Revised work','assignment':A}], task_instance_id=a['task_instance_id'])
replayed = p(s.init_task_network(**kwargs))
result['init_retry_after_replan'] = {
    'initial_instance':a['task_instance_id'], 'replanned_instance':new['task_instance_id'],
    'replanned_epoch':new['epoch'], 'replayed_instance':replayed['task_instance_id'],
    'replayed_epoch':replayed['epoch'], 'current_nodes':[n['id'] for n in replayed['nodes']],
    'archived_instances':[x['task_instance_id'] for x in replayed['task_history']],
}
assert new['task_instance_id'] == a['task_instance_id']
assert replayed['task_instance_id'] != a['task_instance_id']
assert replayed['epoch'] == 0

sid = str(uuid.uuid4())
a = p(s.init_task_network('Task A', sid, atomic_reason='One fixture', request_id='request-A'))
b = p(s.init_task_network('Task B', sid, atomic_reason='Another fixture', request_id='request-B'))
run = p(s.update_task(sid, 'root', 'running', assignment=A, task_instance_id=b['task_instance_id']))
replayed = p(s.init_task_network('Task A', sid, atomic_reason='One fixture', request_id='request-A'))
result['init_retry_after_task_switch'] = {
    'a_instance':a['task_instance_id'], 'b_instance':b['task_instance_id'],
    'b_status_before_replay':run['nodes'][0]['execution_status'],
    'replayed_instance':replayed['task_instance_id'], 'current_title':replayed['task_title'],
    'current_status':replayed['nodes'][0]['execution_status'],
    'archived_task_b_status':replayed['task_history'][-1]['nodes'][0]['execution_status'],
}
assert replayed['task_instance_id'] not in {a['task_instance_id'],b['task_instance_id']}
assert replayed['task_history'][-1]['nodes'][0]['execution_status'] == 'running'

sid = str(uuid.uuid4())
linear = p(s.init_progress(['Only step'], session_id=sid))
instance = linear['task_instance_id']
s.update_step(sid, 0, 'in_progress', assignment=A, task_instance_id=instance)
s.update_step(sid, 0, 'pending', task_instance_id=instance)
new_run = p(s.update_step(sid, 0, 'in_progress', assignment=B, task_instance_id=instance))
late_result = p(s.update_step(sid, 0, 'completed', note='Late completion from the first executor', task_instance_id=instance))
result['linear_late_completion'] = {
    'before_status':new_run['steps'][0]['status'],
    'before_executor':new_run['steps'][0]['assignment']['model'],
    'late_completion_accepted':True,
    'after_status':late_result['steps'][0]['status'],
    'after_executor':late_result['steps'][0]['assignment']['model'],
    'after_note':late_result['steps'][0]['note'],
    'overall_status':late_result['status'],
}
assert late_result['status'] == 'completed'
assert late_result['steps'][0]['assignment']['model'] == 'audit-second'

(OUT/'results.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
print(json.dumps(result, indent=2, ensure_ascii=False))
