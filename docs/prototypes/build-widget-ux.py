#!/usr/bin/env python3
"""Embed the unchanged production App and isolated backend fixtures in the UX preview."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1] / 'plugins/task-progress'


def build():
    tasks = json.loads((HERE / 'task-progress-widget-demo.json').read_text())
    with tempfile.TemporaryDirectory(prefix='task-progress-ux-build-') as temporary:
        with patch.object(Path, 'home', return_value=Path(temporary)):
            spec = importlib.util.spec_from_file_location('widget_ux_core', PLUGIN / 'server.py')
            core = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(core)
            sequence = 0

            def call(name, args):
                nonlocal sequence
                sequence += 1
                wire = json.loads(core.handle({'jsonrpc': '2.0', 'id': sequence, 'method': 'tools/call',
                                              'params': {'name': name, 'arguments': args}}))
                if 'error' in wire or wire['result'].get('isError'):
                    raise RuntimeError(f'{name}: {wire}')
                return wire['result']

            main = {'execution_mode': 'main_session', 'self_execution_reason': '隔离的UX演示数据，不代表真实执行。'}
            review = {'execution_mode': 'subagent', 'tool_identifier': 'ux_fixture',
                      'dispatch_text': '仅用于隔离的UX演示，生成待验收状态。'}
            for task in tasks:
                plan, leaves, groups = [], [], []
                for i, group in enumerate(task.get('groups', [{'children': task.get('leaves', [])}])):
                    parent = f'package-{i + 1}' if task.get('groups') else None
                    if parent:
                        groups.append((parent, group))
                        plan.append({'id': parent, 'title': group['title'], 'assignment': main})
                    ids = []
                    for j, leaf in enumerate(group['children']):
                        node_id = f'leaf-{i + 1}-{j + 1}'
                        node = {'id': node_id, 'title': leaf['title'],
                                'assignment': review if leaf['status'] == 'review' else main}
                        if parent:
                            node['parent_id'] = parent
                        if leaf['status'] == 'blocked':
                            node['depends_on'] = [ids[-1]]
                        plan.append(node)
                        leaves.append((node_id, leaf))
                        ids.append(node_id)
                created = call('init_task_network', {'task_title': task['title'], 'tasks': plan})['structuredContent']
                base = {'session_id': created['session_id'], 'task_instance_id': created['task_instance_id']}
                # Complete finished leaves before starting the one running main-session leaf.
                ordered = sorted(leaves, key=lambda item: {'done': 0, 'review': 1, 'running': 2}.get(item[1]['status'], 3))
                for node_id, leaf in ordered:
                    status = leaf['status']
                    if status not in ('done', 'running', 'review'):
                        continue
                    assignment = review if status == 'review' else main
                    started = call('update_task', {**base, 'task_id': node_id, 'execution_status': 'running',
                                                  'assignment': assignment})['structuredContent']
                    attempt = started['attempt_id']
                    if status == 'review':
                        call('bind_task_agent', {**base, 'task_id': node_id, 'attempt_id': attempt,
                                                'agent_name': 'isolated-ux-fixture-agent'})
                    if status in ('done', 'review'):
                        call('update_task', {**base, 'task_id': node_id, 'attempt_id': attempt,
                            'execution_status': 'finished', 'outcome': 'resolved',
                            'summary': leaf.get('note') or 'UX演示：该项已完成，实际执行与证据以真实任务为准。',
                            'evidence': ['fixture://isolated-ux-preview']})
                for group_id, group in groups:
                    if all(leaf['status'] == 'done' for leaf in group['children']):
                        call('update_task', {**base, 'task_id': group_id, 'execution_status': 'finished',
                            'outcome': 'resolved', 'assignment': main, 'summary': 'UX演示工作包验收。',
                            'evidence': ['fixture://isolated-ux-preview']})
                if task['status'] == 'done':
                    call('update_task', {**base, 'task_id': created['root_id'], 'execution_status': 'finished',
                        'outcome': 'resolved', 'assignment': main, 'summary': 'UX演示整体验收。',
                        'evidence': ['fixture://isolated-ux-preview']})
                task['fixture'] = call('get_progress_view', {**base, 'include_history': True})
            app = core._resource_html()
    payload = {'tasks': tasks, 'appBase64': base64.b64encode(app.encode()).decode(),
               'appSource': 'plugins/task-progress/app.html',
               'appSha256': hashlib.sha256(app.encode()).hexdigest()}
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    template = (PLUGIN / 'assets/widget-panel.html').read_text()
    template = template.replace('data-runtime="true"', 'data-runtime="false"', 1)
    output = HERE / 'task-progress-widget-ux.html'
    marker = '<script type="application/json" id="prototype-payload">null</script>'
    if template.count(marker) != 1:
        raise ValueError('The widget shell must contain one preview data marker.')
    output.write_text(template.replace(marker, '<script type="application/json" id="prototype-payload">' + data + '</script>'))
    print(json.dumps({'output': str(output), 'tasks': len(tasks), 'appSha256': payload['appSha256'],
                      'appBytes': len(app.encode())}, ensure_ascii=False))


if __name__ == '__main__':
    build()
