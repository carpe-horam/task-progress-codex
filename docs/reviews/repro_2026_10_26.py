#!/usr/bin/env python3
"""Read-only implementation audit; all fixture state is isolated in a temp directory.

Run from any directory: python3 docs/reviews/repro_2026_10_26.py
This records observed defects, not a passing product regression suite.
"""
import base64
import importlib.util
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('audit_server', ROOT / 'plugins/task-progress/server.py')
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
A = {'execution_mode': 'main_session', 'model': 'audit-fixture', 'reasoning_effort': 'high'}
RESULTS = []


def progress(result):
    return result['structuredContent']['progress']


def fresh(tasks=None):
    p = progress(S.init_task_network('Isolated audit fixture'))
    sid = p['session_id']
    if tasks:
        S.decompose_task(sid, 'root', tasks)
    return sid


def node(sid, nid):
    return next(n for n in progress(S.get_progress(sid))['nodes'] if n['id'] == nid)


def start(sid, nid, **kwargs):
    S.update_task(sid, nid, 'running', assignment=A, **kwargs)
    return node(sid, nid)['attempt_id']


def finish(sid, nid, **kwargs):
    S.update_task(sid, nid, 'finished', outcome='resolved', attempt_id=node(sid, nid)['attempt_id'], **kwargs)


def denied(fn):
    try:
        fn()
    except S.ProgressError as exc:
        return str(exc)
    return None


def record(name, category, observed):
    RESULTS.append({'case': name, 'category': category, 'observed': observed})


def add_worker(session, index):
    S.decompose_task(session, 'root', [{'id': f'w{index}', 'title': f'Worker {index}'}])


def run():
    sid = fresh([{'id': 'a', 'title': 'A'}, {'id': 'b', 'title': 'B', 'depends_on': ['a']}])
    assert denied(lambda: start(sid, 'b'))
    aid = start(sid, 'a')
    assert denied(lambda: S.update_task(sid, 'a', 'finished', outcome='resolved', attempt_id='old'))
    finish(sid, 'a', summary='Verified A', evidence=['fixture://a'])
    start(sid, 'b'); finish(sid, 'b')
    finish(sid, 'root')
    assert progress(S.get_progress(sid))['status'] == 'completed'
    record('basic_lifecycle_and_attempt_guard', 'pass', 'A -> B -> root; premature B and wrong attempt rejected')

    sid = fresh([{'id': 'a', 'title': 'A'}, {'id': 'b', 'title': 'B', 'depends_on': ['a'], 'dependency_policy': 'finished'}])
    aid = start(sid, 'a')
    S.update_task(sid, 'a', 'failed', attempt_id=aid, summary='Failure details', evidence=['fixture://failure'])
    ready = S.get_ready_tasks(sid)['structuredContent']['ready_tasks']
    assert ready[0]['id'] == 'b' and ready[0]['dependencies'][0]['evidence'] == ['fixture://failure']
    record('finished_policy_evidence', 'pass', ready[0]['dependencies'])

    sid = fresh()
    before = S._session_file(sid).read_bytes()
    err = denied(lambda: S.decompose_task(sid, 'root', [
        {'id': 'a', 'title': 'A', 'depends_on': ['b']}, {'id': 'b', 'title': 'B', 'depends_on': ['a']}]))
    assert err and S._session_file(sid).read_bytes() == before
    record('sibling_cycle_atomicity', 'pass', err)

    sid = fresh([{'id': 'a', 'title': 'Prerequisite'}, {'id': 'g', 'title': 'Group', 'depends_on': ['a']}])
    S.decompose_task(sid, 'g', [{'id': 'g1', 'title': 'Child'}])
    ready = [n['id'] for n in S.get_ready_tasks(sid)['structuredContent']['ready_tasks']]
    start(sid, 'g1')
    record('ancestor_dependency_bypass', 'defect', {'ready_before_start': ready, 'a': node(sid, 'a')['execution_status'], 'g1': node(sid, 'g1')['execution_status']})

    sid = fresh([{'id': 'g', 'title': 'Group'}])
    S.decompose_task(sid, 'g', [{'id': 'g1', 'title': 'Child depends on parent', 'depends_on': ['g']}])
    record('ancestor_dependency_deadlock', 'defect', {
        'accepted': True, 'ready': progress(S.get_progress(sid))['ready_tasks'],
        'start_child_error': denied(lambda: start(sid, 'g1')),
        'finish_parent_error': denied(lambda: finish(sid, 'g'))})

    sid = fresh([{'id': 'a', 'title': 'A'}])
    start(sid, 'a'); finish(sid, 'a'); finish(sid, 'root')
    start(sid, 'a', retry=True)
    p = progress(S.get_progress(sid))
    record('retry_under_completed_root', 'defect', {
        'status': p['status'], 'percent': p['percent'], 'executing_count': p['executing_count'],
        'root': node(sid, 'root')['execution_status'],
        'retry_root_error': denied(lambda: start(sid, 'root', retry=True))})

    sid = fresh([{'id': 'a', 'title': 'A'}, {'id': 'b', 'title': 'B', 'depends_on': ['a']}])
    start(sid, 'a'); finish(sid, 'a'); start(sid, 'b'); finish(sid, 'b')
    start(sid, 'a', retry=True); start(sid, 'b', retry=True)
    record('retry_bypasses_dependency', 'defect', {key: node(sid, key)['execution_status'] for key in ['a', 'b']})

    sid = fresh([{'id': 'root-e1', 'title': 'Old leaf'}])
    old_tid = progress(S.get_progress(sid))['task_instance_id']
    old_attempt = start(sid, 'root-e1')
    S.update_task(sid, 'root-e1', 'cancelled', attempt_id=old_attempt)
    S.rebuild_task_network(sid, 'Audit replan')
    S.update_task(sid, 'root-e1', 'finished', outcome='resolved', task_instance_id=old_tid, attempt_id=old_attempt, summary='Late old leaf result')
    p = progress(S.get_progress(sid))
    record('replan_generated_id_collision', 'defect', {'root_id': p['root_id'], 'epoch': p['epoch'], 'status': p['status'], 'summary': node(sid, p['root_id'])['summary']})

    sid = fresh([{'id': 'a', 'title': 'A'}])
    aid = start(sid, 'a')
    S.decompose_task(sid, 'a', [{'id': 'a1', 'title': 'Split child'}])
    p = progress(S.get_progress(sid))
    record('running_leaf_split', 'defect', {
        'running_parent': node(sid, 'a')['execution_status'], 'executing_count': p['executing_count'],
        'cancel_error': denied(lambda: S.update_task(sid, 'a', 'cancelled', attempt_id=aid)),
        'rebuild_error': denied(lambda: S.rebuild_task_network(sid, 'Cancel old execution'))})

    sid = fresh([{'id': 'a', 'title': 'A'}])
    start(sid, 'a'); finish(sid, 'a')
    finish(sid, 'root', assignment=A)
    n = node(sid, 'root')
    record('parent_synthesis_assignment_dropped', 'defect', {k: n[k] for k in ['assignment', 'attempt', 'attempts', 'started_at', 'finished_at']})

    sid = fresh()
    S.update_task(sid, 'root', 'cancelled')
    p = progress(S.get_progress(sid))
    record('cancelled_root_overall_status', 'defect', {k: p[k] for k in ['status', 'root_status', 'percent']})

    sid = fresh([{'id': 'g', 'title': 'Group'}])
    S.decompose_task(sid, 'g', [{'id': 'g1', 'title': 'Ready grandchild'}])
    p = progress(S.get_progress(sid))
    record('nested_ready_group_looks_blocked', 'defect', {
        'root_status': p['root_status'], 'ready_tasks': p['ready_tasks'], 'g_status': node(sid, 'g')['status']})

    sid = fresh([{'id': 'a', 'title': 'A'}])
    aid = start(sid, 'a')
    S.update_task(sid, 'a', 'running', attempt_id=aid, assignment={**A, 'model': 'different-fixture'})
    record('changed_assignment_silently_ignored', 'contract_gap', {'accepted': True, 'stored_model': node(sid, 'a')['assignment']['model']})

    sid = fresh([{'id': 'old', 'title': 'Old'}])
    old = progress(S.get_progress(sid))
    aid = start(sid, 'old'); finish(sid, 'old')
    start(sid, 'old', retry=True)
    assert denied(lambda: S.update_task(sid, 'old', 'failed', attempt_id=aid))
    S.init_task_network('Next task', sid)
    assert denied(lambda: S.decompose_task(sid, 'root', [{'id': 'wrong', 'title': 'Wrong'}], task_instance_id=old['task_instance_id']))
    assert denied(lambda: S.decompose_task(sid, 'root', [{'id': 'wrong', 'title': 'Wrong'}]))
    p = progress(S.get_progress(sid))
    assert len(p['task_history']) == 1 and p['task_history'][0]['task_instance_id'] == old['task_instance_id']
    record('stale_task_and_attempt_isolation', 'pass', 'old attempt, old task ID and omitted ID after archive rejected')

    sid = fresh([{'id': 'old', 'title': 'Old'}])
    S.rebuild_task_network(sid, 'Replan')
    assert denied(lambda: S.decompose_task(sid, 'root-e1', [{'id': 'old', 'title': 'Reuse'}]))
    assert denied(lambda: S.update_task(sid, 'old', 'finished', outcome='resolved'))
    record('ordinary_replan_id_guard', 'pass', 'ordinary historical task IDs cannot be reused or updated')

    sid = fresh()
    ctx = multiprocessing.get_context('fork')
    workers = [ctx.Process(target=add_worker, args=(sid, i)) for i in range(12)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(5)
        assert worker.exitcode == 0
    assert len(progress(S.get_progress(sid))['nodes']) == 13
    record('concurrent_process_writes', 'pass', '12 parallel independent additions retained; valid JSON')

    # Registration has no generation/token associated with task/tool-result order.
    sid = fresh()
    S.register_ui_view(sid, 'newer-card')
    S.register_ui_view(sid, 'old-card-remounted-later')
    state = S.check_ui_view(sid, 'newer-card')['structuredContent']
    record('view_registration_order', 'defect', state)

    sid2 = fresh()
    S.clear_progress(sid)
    assert progress(S.get_progress(sid)) is None and progress(S.get_progress(sid2)) is not None
    assert S.check_ui_view(sid, 'old-card')['structuredContent']['status'] == 'missing_progress'
    record('clear_session_isolation', 'pass', 'clearing one fixture preserves another and invalidates its view')

    # Only fixture exports are written, under the temporary directory.
    mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    files = [{'name': 'Task-Progress-audit.xlsx', 'mime_type': mime, 'encoding': 'base64', 'content': base64.b64encode(b'fixture').decode()},
             {'name': 'Task-Progress-audit.html', 'mime_type': 'text/html', 'encoding': 'utf-8', 'content': '<p>fixture</p>'}]
    with patch.object(Path, 'home', return_value=S.SESSION_DIR.parent):
        first = S.save_export_files(files)['structuredContent']
        second = S.save_export_files(files)['structuredContent']
        assert first['files'][0]['path'] != second['files'][0]['path']
        assert denied(lambda: S.save_export_files([{**files[0], 'name': '../Task-Progress-audit.xlsx'}, files[1]]))
        assert all(Path(f['path']).stat().st_mode & 0o777 == 0o600 for f in first['files'])
    record('export_save_collision_and_path_guard', 'pass', 'paired files, collision suffix, 0600 permissions, path rejection')

    # Exercise the actual launcher and stdio protocol without creating sessions.
    plugin = ROOT / 'plugins/task-progress'
    messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'resources/read', 'params': {'uri': S.RESOURCE_URI}}]
    proc = subprocess.run([str(plugin / 'scripts/launch_progress_mcp'), './server.py'], cwd=plugin,
                          input=''.join(json.dumps(m) + '\n' for m in messages), capture_output=True, text=True, timeout=5)
    responses = [json.loads(x) for x in proc.stdout.splitlines()]
    assert proc.returncode == 0 and len(responses) == 3 and len(responses[1]['result']['tools']) == 13
    assert responses[2]['result']['contents'][0]['text'] == (plugin / 'app.html').read_text()
    record('launcher_and_stdio_smoke', 'pass', 'initialize, 13 tools, original HTML resource')

    proc = subprocess.run([sys.executable, str(plugin / 'server.py')], input='[]\n' + json.dumps({'id': 2, 'method': 'ping'}) + '\n',
                          capture_output=True, text=True, timeout=5)
    record('malformed_rpc_crashes_server', 'robustness', {'exit': proc.returncode, 'response': proc.stdout, 'error': proc.stderr.splitlines()[-1] if proc.stderr else ''})


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='task-progress-audit-') as temp:
        S.SESSION_DIR = Path(temp) / 'sessions'
        S.SESSION_DIR.mkdir()
        run()
    print(json.dumps({'source': 'dafb74a', 'cases': RESULTS}, ensure_ascii=False, indent=2))
