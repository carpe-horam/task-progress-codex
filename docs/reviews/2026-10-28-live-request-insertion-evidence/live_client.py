"""Exercise the installed MCP service. No direct session-file writes."""
import datetime
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path

OUT = Path('/tmp/task-progress-live-run-20261028')
SID = '08cfece3-45ac-40f5-a797-c2b846c92464'
A = '14d849be-ee26-4346-9d4b-42d5c08eefc4'
CACHE = Path('/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.2.0')
spec = importlib.util.spec_from_file_location('live_installed_manager', CACHE / 'service_manager.py')
manager_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager_module)
manager = manager_module.ServiceManager()

def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()

def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2))

def rpc(method, params):
    connection = http.client.HTTPConnection('127.0.0.1', manager_module.PORT, timeout=10)
    try:
        connection.request('POST', '/mcp', json.dumps({'jsonrpc':'2.0','id':701,'method':method,'params':params}),
            headers={**manager._headers(), 'Content-Type':'application/json', 'Accept':'application/json',
                     'MCP-Protocol-Version':'2025-11-25'})
        response=connection.getresponse()
        result=json.loads(response.read())
        assert response.status==200 and 'error' not in result, 'MCP transport failed'
        return result['result']
    finally:
        connection.close()

def call(name, args, expect_error=False):
    result=rpc('tools/call', {'name':name, 'arguments':args})
    if expect_error:
        assert result.get('isError'), 'Expected service guard to reject the operation'
    else:
        assert not result.get('isError'), result.get('content')
    return result

def progress(iid=None):
    return call('get_progress', {'session_id':SID, **({'task_instance_id':iid} if iid else {})})['structuredContent']['progress']

def snapshot(name, iid=None):
    p=progress(iid)
    work=p.get('session_work',{})
    record={'captured_at':now(), 'task_instance_id':p['task_instance_id'], 'task_title':p['task_title'],
            'state_revision':p['state_revision'], 'status':p['status'], 'ready_tasks':p['ready_tasks'],
            'can_finish_session':work.get('can_finish_session'), 'return_to':work.get('return_to'),
            'next_actions':p.get('next_actions'), 'task_context':p.get('task_context'),
            'nodes':[{k:n.get(k) for k in ('id','title','status','execution_status','outcome','review_status','attempt_id','started_at','finished_at','summary','evidence','dispatch')} for n in p['nodes']],
            'requests':[{k:r.get(k) for k in ('request_id','task_instance_id','task_title','mode','status','return_to','origin_return_to','summary')} for r in work.get('requests',[]) if r.get('request_id','').startswith('live-20261028') or r.get('task_instance_id')==A]}
    write(name, record)
    return record
