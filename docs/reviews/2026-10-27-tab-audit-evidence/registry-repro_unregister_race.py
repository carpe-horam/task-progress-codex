import importlib.util,json,uuid
from pathlib import Path
from unittest import mock
out=Path('/tmp/task-progress-tab-audit-registry')
spec=importlib.util.spec_from_file_location('audit_race_server',Path('/Users/machengling/.codex/worktrees/4d11/task-process/plugins/task-progress/server.py'))
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s);s.SESSION_DIR=out/'race-sessions';s.SESSION_DIR.mkdir(exist_ok=True)
sid=str(uuid.uuid4());c=s.init_task_network('Close/heartbeat race',sid,atomic_reason='One fixture')['structuredContent']
a=dict(view_sequence=c['view_sequence'],state_revision=c['state_revision'],task_instance_id=c['progress']['task_instance_id'])
with mock.patch.object(s.time,'time',return_value=1000):
    s.register_ui_view(sid,'inline',**a)
    s.register_ui_view(sid,'tab',**a,display_mode='fullscreen')
    s.unregister_ui_view(sid,'tab')
    closed=s.check_ui_view(sid,'inline')['structuredContent']['status']
with mock.patch.object(s.time,'time',return_value=1001):
    late=s.register_ui_view(sid,'tab',**a,display_mode='fullscreen')['structuredContent']['status']
    after=s.check_ui_view(sid,'inline')['structuredContent']['status']
r={'after_unregister':closed,'late_heartbeat_register':late,'after_late_heartbeat':after,'registry':json.loads(s._ui_view_file(sid).read_text())}
assert closed=='latest' and late=='registered' and after=='hidden_by_fullscreen'
(out/'unregister-race-results.json').write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n');print(json.dumps(r,indent=2,ensure_ascii=False))
