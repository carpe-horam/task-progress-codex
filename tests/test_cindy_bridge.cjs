'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function main() {
  let hostMessage;
  const broadcasts = [];
  const replies = [];
  const calls = [];
  let panelChannel;
  const kv = {};
  let failKV = false;
  const context = {
    BroadcastChannel: class { constructor(name) { assert.equal(name, 'task-progress-panel'); panelChannel = this; } postMessage(message) { broadcasts.push(message); } },
    cindy: {
      onHostMessage(handler) { hostMessage = handler; },
      node: { request: async request => {
        calls.push(request);
        return { ok: true, result: { structuredContent: { session_id: 'session-current', state_revision: 7, progress: { session_id: 'session-current', task_title: 'Current task' } } } };
      } },
      send: async message => { replies.push(message); }
    },
    fetch: async (url, options) => {
      if (failKV) throw new Error('KV unavailable');
      if (options?.method === 'PUT') Object.assign(kv, JSON.parse(options.body));
      return { ok: true, json: async () => ({ ...kv }) };
    },
    Date,
    setTimeout,
    clearTimeout,
    AbortController,
    String,
    JSON,
    Error
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../plugins/task-progress/cindy/main.js'), 'utf8'), context);
  await hostMessage({ type: 'tool-call', callId: 'call-1', tool: 'init_task_network', args: { session_context: { session_id: 'cindy-session-A', workdir: '/tmp/project' } } });
  assert.equal(broadcasts[0].type, 'panel-diagnostic');
  assert.equal(broadcasts[0].diagnostic.hasHostSession, true);
  assert.equal(broadcasts[1].type, 'update');
  assert.equal(broadcasts[1].session_id, 'session-current');
  assert.equal(broadcasts[1].result.structuredContent.state_revision, 7);
  assert.equal(replies[0].type, 'tool-result');
  assert.equal(replies[0].ok, true);
  assert.equal(calls[0].params.arguments.session_context, undefined, 'host-only session context is not forwarded to Task Progress');
  assert.equal(kv.taskProgressSessionsByHostSession['cindy-session-A'], 'session-current');
  await hostMessage({ type: 'event', name: 'did-session-switched', data: { sessionId: 'cindy-session-A' } });
  assert.equal(broadcasts[2].type, 'bind-session');
  assert.equal(broadcasts[2].session_id, 'session-current', 'switching back to a Cindy session restores its matching task session');
  assert.equal(kv.taskProgressActiveHostSessionId, 'cindy-session-A', 'the active Cindy session is persisted for a panel opened after the switch');
  assert.equal(kv.taskProgressPanelDiagnostic.kind, 'session-switch');
  assert.equal(kv.taskProgressPanelDiagnostic.mapped, true);
  await panelChannel.onmessage({ data: { type: 'panel-tool-request', requestId: 'panel-1', name: 'get_progress_view', arguments: { session_id: 'session-current' } } });
  assert.equal(calls[1].params.name, 'get_progress_view', 'the native Cindy plugin context executes tools requested by the side panel');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].params.arguments)), { session_id: 'session-current' });
  assert.equal(broadcasts.at(-1).type, 'panel-tool-result');
  assert.equal(broadcasts.at(-1).requestId, 'panel-1');
  assert.equal(broadcasts.at(-1).result.structuredContent.session_id, 'session-current');
  await panelChannel.onmessage({ data: { type: 'panel-tool-request', requestId: 'panel-1', name: 'get_progress_view', arguments: { session_id: 'session-current' } } });
  assert.equal(calls.length, 2, 'retransmitting the same request id does not repeat the service call');
  context.cindy.node.request = async () => ({ ok: false, message: 'service unavailable' });
  await panelChannel.onmessage({ data: { type: 'panel-tool-request', requestId: 'panel-2', name: 'get_progress_view', arguments: {} } });
  assert.equal(broadcasts.at(-1).type, 'panel-tool-error', 'service failures are returned to the requesting panel');
  assert.match(broadcasts.at(-1).message, /service unavailable/);
  context.cindy.node.request = async request => {
    calls.push(request);
    return { ok: true, result: { structuredContent: { session_id: 'session-current', state_revision: 7, progress: { session_id: 'session-current', task_title: 'Current task' } } } };
  };
  failKV = true;
  await hostMessage({ type: 'tool-call', callId: 'call-2', tool: 'get_progress', args: { session_id: 'session-current' } });
  assert.equal(broadcasts[6].type, 'panel-diagnostic');
  assert.equal(broadcasts[6].diagnostic.hasHostSession, false, 'diagnostics distinguish tool calls without host context');
  assert.equal(broadcasts[7].type, 'update', 'a KV write failure must not suppress live updates');
  assert.equal(replies[1].ok, true);
  console.log(JSON.stringify({ pass: true, checks: ['tool result is returned to Cindy', 'host tool results are broadcast with task session id', 'host-only session context is not forwarded to the task service', 'Cindy session switches rebind the matching Task Progress session', 'native Cindy plugin context proxies side-panel tool calls', 'bridge failures are returned to the panel', 'bridge diagnostics record missing host context', 'live updates survive KV failure'] }, null, 2));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
