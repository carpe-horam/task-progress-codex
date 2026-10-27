import sys
sys.dont_write_bytecode = True
import importlib.util, json, uuid, hashlib, shutil, inspect
from pathlib import Path
from unittest import mock
ROOT = Path('/Users/machengling/.codex/worktrees/4d11/task-process')
OUT = Path('/tmp/task-progress-fix-verify-registry')
OUT.mkdir(parents=True, exist_ok=True)
SRC = ROOT / 'plugins/task-progress/server.py'
source_before = SRC.read_bytes()
spec = importlib.util.spec_from_file_location('verify_registry_server', SRC)
s = importlib.util.module_from_spec(spec)
with mock.patch.object(Path, 'home', return_value=OUT/'isolated-home'):
    spec.loader.exec_module(s)
s.SESSION_DIR = OUT/'sessions'
s.SESSION_DIR.mkdir(parents=True, exist_ok=True)
A = {'execution_mode':'main_session','model':'verify-executor-A','reasoning_effort':'high'}
B = {**A, 'model':'verify-executor-B'}
results = []
def sc(r): return r['structuredContent']
def prog(r): return sc(r)['progress']
def init():
    sid=str(uuid.uuid4())
    return sid, sc(s.init_task_network('Registry positive acceptance',sid,atomic_reason='One isolated registry verification'))
def source(c): return dict(view_sequence=c['view_sequence'],state_revision=c['state_revision'],task_instance_id=c['progress']['task_instance_id'])
def reg(sid,vid,c,mode='inline'): return sc(s.register_ui_view(sid,vid,**source(c),display_mode=mode))['status']
def check(sid,vid,mode='inline'): return sc(s.check_ui_view(sid,vid,mode))['status']
def meta(sid): return json.loads(s._ui_view_file(sid).read_text())
def run(name,fn):
    record={'case':name,'assertion_direction':'positive acceptance','observed':{}}
    try: fn(record['observed']); record['passed']=True
    except AssertionError as e: record.update(passed=False, failure=str(e))
    except Exception as e: record.update(passed=False,error=f'{type(e).__name__}: {e}')
    results.append(record)
def owner_recovery(o):
    sid,a=init();reg(sid,'old',a);b=sc(s.get_progress(sid));reg(sid,'new',b)
    o['before']=check(sid,'old');s.unregister_ui_view(sid,'new')
    o.update(after=check(sid,'old'),reregister=reg(sid,'old',a),registry=meta(sid))
    assert o['before']=='superseded' and o['after']=='latest' and o['reregister']=='registered', 'Previously eligible surviving old card must regain ownership immediately'
def fullscreen_return(o):
    sid,a=init();reg(sid,'inline',a);reg(sid,'tab',a,'fullscreen')
    s.update_task(sid,'root','running',assignment=A,task_instance_id=a['progress']['task_instance_id'])
    b=sc(s.get_progress(sid));reg(sid,'tab',b,'fullscreen')
    o['hidden']=check(sid,'inline');o['return']=reg(sid,'tab',b,'inline')
    o['after_return']=check(sid,'inline');o['registry_after_return']=meta(sid)
    s.unregister_ui_view(sid,'tab');o['after_teardown']=check(sid,'inline')
    assert o['hidden']=='hidden_by_fullscreen' and o['after_return']=='latest' and o['after_teardown']=='latest', 'Fullscreen poll must not take original inline source ownership'
def same_source(o):
    sid,a=init();reg(sid,'mount-1',a);reg(sid,'mount-2',a)
    o['checks']=[check(sid,v) for v in ['mount-1','mount-2']]
    o['retry_first']=reg(sid,'mount-1',a);o['checks_after_retry']=[check(sid,v) for v in ['mount-1','mount-2']]
    s.unregister_ui_view(sid,'mount-2');o['after_owner_close']=check(sid,'mount-1');o['registry']=meta(sid)
    assert o['checks']==['superseded','latest'] and o['retry_first']=='superseded' and o['checks_after_retry']==o['checks'] and o['after_owner_close']=='latest', 'Exactly one same-source mount may own inline and retries may not steal it'
def close_accepted(o):
    sid,a=init();reg(sid,'inline',a);reg(sid,'tab',a,'fullscreen')
    o['before']=check(sid,'inline');s.unregister_ui_view(sid,'tab')
    o['after']=check(sid,'inline');o['late_registration']=reg(sid,'tab',a,'fullscreen');o['after_late']=check(sid,'inline');o['registry']=meta(sid)
    assert o['after']==o['after_late']=='latest' and o['late_registration']=='superseded', 'Closing an accepted registration must release it and reject renewal'
def close_before_register(o):
    sid,a=init();reg(sid,'inline',a);s.unregister_ui_view(sid,'tab')
    o['late_initial_registration']=reg(sid,'tab',a,'fullscreen');o['inline']=check(sid,'inline');o['registry']=meta(sid)
    assert o['late_initial_registration']=='superseded' and o['inline']=='latest', 'Teardown before in-flight registration arrives must tombstone mount'
def stale_demote(o):
    sid,a=init()
    with mock.patch.object(s.time,'time',return_value=1000):
        reg(sid,'old-inline',a);reg(sid,'tab',a,'fullscreen');b=sc(s.render_progress(sid));reg(sid,'newer-inline',b)
        o['return']=reg(sid,'tab',a,'inline');o['stored_mode']=meta(sid)['views']['tab']['display_mode'];o['newer_inline']=check(sid,'newer-inline');o['registry']=meta(sid)
    assert o['return']=='superseded' and o['stored_mode']=='inline' and o['newer_inline']=='latest', 'Rejected stale inline must still release prior fullscreen lease immediately'
def unregister_late_heartbeat(o):
    sid,a=init()
    with mock.patch.object(s.time,'time',return_value=2000):
        reg(sid,'inline',a);reg(sid,'tab',a,'fullscreen');s.unregister_ui_view(sid,'tab');o['after_unregister']=check(sid,'inline')
    with mock.patch.object(s.time,'time',return_value=2001):
        o['late_heartbeat']=reg(sid,'tab',a,'fullscreen');o['after_heartbeat']=check(sid,'inline')
    with mock.patch.object(s.time,'time',return_value=100000):
        o['much_later_heartbeat']=reg(sid,'tab',a,'fullscreen');o['after_much_later']=check(sid,'inline')
    o['registry']=meta(sid)
    assert o['after_unregister']==o['after_heartbeat']==o['after_much_later']=='latest' and o['late_heartbeat']==o['much_later_heartbeat']=='superseded', 'Unregister must terminally reject same-mount renewals'
def historical_remount(o):
    sid,a=init();reg(sid,'original',a);b=sc(s.get_progress(sid));reg(sid,'replacement',b)
    o['late_old_mount']=reg(sid,'historic-remount',a);s.unregister_ui_view(sid,'replacement')
    o['original_after_close']=check(sid,'original');o['historic_after_close']=check(sid,'historic-remount');o['registry']=meta(sid)
    assert o['late_old_mount']=='superseded' and o['original_after_close']=='latest' and o['historic_after_close']=='superseded', 'Only formerly eligible old card may recover; late historical mount must stay compact'
def demote_late_heartbeat(o):
    sid,a=init();reg(sid,'inline',a);reg(sid,'tab',a,'fullscreen');reg(sid,'tab',a,'inline')
    o['after_demote']=check(sid,'inline');o['late_old_fullscreen_heartbeat']=reg(sid,'tab',a,'fullscreen');o['after_late_heartbeat']=check(sid,'inline');o['registry']=meta(sid)
    assert o['after_late_heartbeat']=='latest', 'Old fullscreen heartbeat after completed demotion must not restore the old mode/lease'
def no_session_retry(o):
    args=dict(task_title='Lost-response retry',atomic_reason='One isolated operation',request_id='verify-initial-request')
    a=sc(s.init_task_network(**args));b=sc(s.init_task_network(**args))
    o.update(first_session=a['session_id'],second_session=b['session_id'],first_instance=a['progress']['task_instance_id'],second_instance=b['progress']['task_instance_id'])
    assert a['session_id']==b['session_id'] and a['progress']['task_instance_id']==b['progress']['task_instance_id'], 'Identical request_id retry without session must recover original allocation'
def replan_retry(o):
    sid=str(uuid.uuid4());args=dict(task_title='Task A',session_id=sid,atomic_reason='One isolated operation',request_id='request-A')
    a=prog(s.init_task_network(**args));r=prog(s.rebuild_task_network(sid,'Replan verification',task_instance_id=a['task_instance_id']))
    s.decompose_task(sid,r['root_id'],[{'id':'replanned-work','title':'Replanned work','assignment':A}],task_instance_id=a['task_instance_id'])
    replay=prog(s.init_task_network(**args));current=prog(s.get_progress(sid))
    o.update(initial_instance=a['task_instance_id'],replanned_epoch=r['epoch'],replayed_instance=replay['task_instance_id'],current_epoch=current['epoch'],current_nodes=[n['id'] for n in current['nodes']],history_instances=[h['task_instance_id'] for h in current['task_history']])
    assert current['task_instance_id']==a['task_instance_id'] and current['epoch']==r['epoch'] and 'replanned-work' in o['current_nodes'], 'Initialization replay may not replace progressed/replanned current task'
def switched_retry(o):
    sid=str(uuid.uuid4());a=prog(s.init_task_network('Task A',sid,atomic_reason='One fixture',request_id='A'))
    b=prog(s.init_task_network('Task B',sid,atomic_reason='Another fixture',request_id='B'))
    s.update_task(sid,'root','running',assignment=A,task_instance_id=b['task_instance_id'])
    replay=prog(s.init_task_network('Task A',sid,atomic_reason='One fixture',request_id='A'));current=prog(s.get_progress(sid))
    o.update(a_instance=a['task_instance_id'],b_instance=b['task_instance_id'],replayed_instance=replay['task_instance_id'],current_title=current['task_title'],current_status=current['nodes'][0]['execution_status'],history=[{'title':h['task_title'],'status':h['nodes'][0]['execution_status']} for h in current['task_history']])
    assert current['task_instance_id']==b['task_instance_id'] and current['nodes'][0]['execution_status']=='running', 'Replay of archived initialization must not replace current running task'
def linear_late_completion(o):
    sid=str(uuid.uuid4());p=prog(s.init_progress(['Only step'],session_id=sid));instance=p['task_instance_id']
    s.update_step(sid,0,'in_progress',assignment=A,task_instance_id=instance);s.update_step(sid,0,'pending',task_instance_id=instance)
    before=prog(s.update_step(sid,0,'in_progress',assignment=B,task_instance_id=instance));error=None
    try: s.update_step(sid,0,'completed',note='Late completion from executor A',task_instance_id=instance)
    except s.ProgressError as e: error=str(e)
    after=prog(s.get_progress(sid));o.update(before=before['steps'][0],error=error,after=after['steps'][0],overall_status=after['status'],update_step_parameters=list(inspect.signature(s.update_step).parameters))
    assert error and after['steps'][0]['status']=='in_progress' and after['steps'][0]['assignment']['model']==B['model'], 'Ambiguous old completion without current-attempt proof must not finish executor B'
for name,fn in [('UI02_owner_recovery',owner_recovery),('UI02_fullscreen_return_source',fullscreen_return),('UI03_same_source_single_owner',same_source),('UI04A_close_accepted',close_accepted),('UI04A_close_before_initial_register',close_before_register),('UI04B_stale_demote_releases_lease',stale_demote),('UI05_unregister_late_heartbeat',unregister_late_heartbeat),('old_card_recovery_vs_historical_remount',historical_remount),('mode_transition_late_heartbeat',demote_late_heartbeat),('F01_initial_no_session_retry',no_session_retry),('F01_replan_retry',replan_retry),('F01_task_switch_retry',switched_retry),('F02_linear_late_completion',linear_late_completion)]: run(name,fn)
source_after=SRC.read_bytes()
binding={'server':str(SRC),'sha256_before':hashlib.sha256(source_before).hexdigest(),'sha256_after':hashlib.sha256(source_after).hexdigest(),'source_stable_during_run':source_before==source_after,'session_dir':str(s.SESSION_DIR),'python_version':sys.version,'script':str(Path(__file__).resolve())}
(OUT/'results.json').write_text(json.dumps({'binding':binding,'results':results},indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'binding':binding,'cases':[{'case':r['case'],'passed':r['passed'],'failure':r.get('failure'),'error':r.get('error')} for r in results]},indent=2,ensure_ascii=False))
sys.exit(0 if all(r['passed'] for r in results) else 1)
