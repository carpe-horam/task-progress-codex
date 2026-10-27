import importlib.util,json,uuid
from pathlib import Path
from unittest import mock
ROOT=Path('/Users/machengling/.codex/worktrees/4d11/task-process')
OUT=Path('/tmp/task-progress-tab-audit-registry')
spec=importlib.util.spec_from_file_location('audit_server',ROOT/'plugins/task-progress/server.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
s.SESSION_DIR=OUT/'sessions';s.SESSION_DIR.mkdir(exist_ok=True)

def init():
    sid=str(uuid.uuid4())
    r=s.init_task_network('Registry audit fixture',sid,atomic_reason='One registry fixture')
    return sid,r['structuredContent']
def source(c):return dict(view_sequence=c['view_sequence'],state_revision=c['state_revision'],task_instance_id=c['progress']['task_instance_id'])
def reg(sid,vid,c,mode='inline'):return s.register_ui_view(sid,vid,**source(c),display_mode=mode)['structuredContent']['status']
def check(sid,vid,mode='inline'):return s.check_ui_view(sid,vid,mode)['structuredContent']['status']
def meta(sid):return json.loads(s._ui_view_file(sid).read_text())
results={}

sid,a=init()
results['same_result_two_mounts']={
    'register_first':reg(sid,'mount-1',a),'register_second':reg(sid,'mount-2',a),
    'check_first':check(sid,'mount-1'),'check_second':check(sid,'mount-2'),
    'registry':meta(sid),
}
assert results['same_result_two_mounts']['check_first']==results['same_result_two_mounts']['check_second']=='latest'

sid,a=init();reg(sid,'original-inline',a)
b=s.get_progress(sid)['structuredContent'];reg(sid,'replacement-inline',b)
before=check(sid,'original-inline')
s.unregister_ui_view(sid,'replacement-inline')
results['unregister_latest_has_no_live_owner']={
    'original_before_unregister':before,
    'original_after_unregister':check(sid,'original-inline'),
    'removed_after_unregister':check(sid,'replacement-inline'),
    'original_reregister':reg(sid,'original-inline',a),'registry':meta(sid),
}
assert results['unregister_latest_has_no_live_owner']['original_after_unregister']=='superseded'
assert results['unregister_latest_has_no_live_owner']['original_reregister']=='superseded'

sid,a=init();reg(sid,'original-inline',a);reg(sid,'tab',a,'fullscreen')
# A state change is required for the real frontend to promote viewSource on fullscreen polling.
s.update_task(sid,'root','running',assignment={'execution_mode':'main_session'},task_instance_id=a['progress']['task_instance_id'])
b=s.get_progress(sid)['structuredContent'];reg(sid,'tab',b,'fullscreen')
was_hidden=check(sid,'original-inline')
returned=reg(sid,'tab',b,'inline')
original_after_return=check(sid,'original-inline')
s.unregister_ui_view(sid,'tab')
results['fullscreen_polled_source_becomes_inline_owner_then_removed']={
    'before_return':was_hidden,'return_to_inline':returned,
    'original_after_return':original_after_return,'original_after_tab_teardown':check(sid,'original-inline'),
    'registry':meta(sid),
}
assert original_after_return=='superseded'
assert results['fullscreen_polled_source_becomes_inline_owner_then_removed']['original_after_tab_teardown']=='superseded'

sid,a=init()
with mock.patch.object(s.time,'time',return_value=1000):
    reg(sid,'original-inline',a);reg(sid,'tab',a,'fullscreen')
    # Another render at the same state revision has a newer source sequence.
    b=s.render_progress(sid)['structuredContent'];reg(sid,'newer-inline',b)
    refused=reg(sid,'tab',a,'inline')
    retained_mode=meta(sid)['views']['tab']['display_mode']
    hidden=check(sid,'newer-inline')
with mock.patch.object(s.time,'time',return_value=1034):before_expiry=check(sid,'newer-inline')
with mock.patch.object(s.time,'time',return_value=1036):after_expiry=check(sid,'newer-inline')
results['rejected_fullscreen_to_inline_keeps_fullscreen_lease']={
    'return_to_inline':refused,'stored_mode_after_return':retained_mode,'newer_inline_after_return':hidden,
    'newer_inline_at_34s':before_expiry,'newer_inline_at_36s':after_expiry,
}
assert refused=='superseded' and retained_mode=='fullscreen' and hidden==before_expiry=='hidden_by_fullscreen' and after_expiry=='latest'

sid,a=init()
with mock.patch.object(s.time,'time',return_value=2000):reg(sid,'old-task-tab',a,'fullscreen')
b=s.init_task_network('New task',sid,atomic_reason='A different fixture task')['structuredContent']
with mock.patch.object(s.time,'time',return_value=2001):
    reg(sid,'new-task-inline',b)
    old_renew=reg(sid,'old-task-tab',a,'fullscreen')
    new_tab=reg(sid,'new-task-tab',b,'fullscreen')
    new_inline=check(sid,'new-task-inline')
results['task_switch_retains_old_fullscreen_lease']={
    'old_renew':old_renew,'new_task_tab_register':new_tab,'new_inline':new_inline,'registry':meta(sid),
}

(OUT/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(results,indent=2,ensure_ascii=False))
