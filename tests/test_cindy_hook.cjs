const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../plugins/task-progress/cindy/main.js'), 'utf8');

function harness(initialKv = {}, nodeResult = null, {hangRead = false} = {}) {
  let handler;
  let kv = initialKv;
  const sent = [];
  const calls = [];
  const broadcasts = [];
  let bus;
  const sandbox = {
    BroadcastChannel: class { constructor() { bus = this; } postMessage(value) { broadcasts.push(value); } },
    fetch: async (_url, options) => {
      if (hangRead && options?.method !== 'PUT') return new Promise(()=>{});
      if (options?.method === 'PUT') kv = JSON.parse(options.body);
      return { ok: true, json: async () => kv };
    },
    cindy: {
      onHostMessage: callback => { handler = callback; },
      node: { request: async request => { calls.push(request); return typeof nodeResult === 'function' ? nodeResult(request) : nodeResult; } },
      send: value => { sent.push(value); },
    },
    Date, Map, Object, JSON, setTimeout, clearTimeout,
  };
  vm.runInNewContext(source, sandbox);
  return { dispatch: event => handler(event), deliverPanel: value => bus.onmessage({ data: value }),
    sent, calls, broadcasts, getKv: () => kv };
}

const session = '00000000-0000-0000-0000-000000000001';
const instance = 'instance-1';
const current = { taskProgressSessionsByHostSession: {host:session}, taskProgressContextsByHostSession: { host: { session_id: session, task_instance_id: instance } } };
const overview = { ok: true, result: { structuredContent: {
  view: 'overview', session_id: session, task_instance_id: instance, state_revision: 4,
  progress: { schema_version: 2, task_instance_id: instance, epoch: 1,
    planning_status: 'ready', pending_review_count: 1, stage_inventory_due_count: 1 }
} } };
const event = { type: 'event', name: 'will-compose-context', hookId: 'hook-1', data: { sessionId: 'host' } };

(async () => {
  const matched = harness(current, overview);
  await matched.dispatch(event);
  assert.equal(matched.sent[0].action, 'context');
  assert.match(matched.sent[0].blocks[0].text, /待主会话验收/);
  assert.match(matched.sent[0].blocks[0].text, /不代表用户原话/);
  assert.equal(matched.calls[0].params.arguments.task_instance_id, instance);
  assert.equal(matched.getKv().taskProgressHookDiagnostics.host.events['will-compose-context'].received, 1);
  assert.equal(matched.getKv().taskProgressHookDiagnostics.host.events['will-compose-context'].last.outcome, 'context');

  const simultaneous = harness(current,{ok:true,result:{structuredContent:{...overview.result.structuredContent,
    progress:{...overview.result.structuredContent.progress,planning_status:'required',synthesis_ready_count:1,synthesis_ready_ids:['package-a'],closure_review_due:true}}}});
  await simultaneous.dispatch(event);
  for(const expected of ['尚需完成','待主会话验收','package-a','阶段盘点','根任务收尾']) assert(simultaneous.sent[0].blocks[0].text.includes(expected));

  const synthesis = harness(current, {ok:true,result:{structuredContent:{...overview.result.structuredContent,
    progress:{...overview.result.structuredContent.progress,pending_review_count:0,stage_inventory_due_count:0,synthesis_ready_count:1,synthesis_ready_ids:['package-a']}}}});
  await synthesis.dispatch(event);
  assert.equal(synthesis.sent[0].action,'context');
  assert.match(synthesis.sent[0].blocks[0].text,/package-a.*综合验收/);
  const noSynthesis = harness(current, {ok:true,result:{structuredContent:{...overview.result.structuredContent,
    progress:{...overview.result.structuredContent.progress,pending_review_count:0,stage_inventory_due_count:0,synthesis_ready_count:0,synthesis_ready_ids:[]}}}});
  await noSynthesis.dispatch(event);
  assert.equal(noSynthesis.sent[0].action,'allow');
  const staleSynthesis = harness(current, {ok:true,result:{structuredContent:{...overview.result.structuredContent,task_instance_id:'foreign',
    progress:{...overview.result.structuredContent.progress,pending_review_count:0,stage_inventory_due_count:0,synthesis_ready_count:1,synthesis_ready_ids:['package-a']}}}});
  await staleSynthesis.dispatch(event);
  assert.equal(staleSynthesis.sent[0].action,'allow');

  const unknown = harness({}, overview);
  await unknown.dispatch(event);
  assert.equal(unknown.sent[0].action, 'allow');
  assert.equal(unknown.calls.length, 0);

  const wrongBinding=harness({...current,taskProgressSessionsByHostSession:{host:'other-session'}},overview);
  await wrongBinding.dispatch(event);
  assert.equal(wrongBinding.sent[0].action,'allow');
  assert.equal(wrongBinding.calls.length,0,'stale context must not query another session');

  const hung=harness(current,overview,{hangRead:true});
  const hungDispatch=hung.dispatch(event);
  await new Promise(resolve=>setTimeout(resolve,2350));
  assert.equal(hung.sent[0].action,'allow','unavailable KV cannot hold the host hook');

  const stale = harness(current, { ok: true, result: { structuredContent: {
    ...overview.result.structuredContent, task_instance_id: 'another-instance' } } });
  await stale.dispatch(event);
  assert.equal(stale.sent[0].action, 'allow');

  const recovered = harness({ ...current, taskProgressSessionsByHostSession: {host:session} }, request =>
    request.params.arguments.task_instance_id ? {ok:true,result:{isError:true,content:[{type:'text',text:'旧实例已归档'}]}} :
      {ok:true,result:{structuredContent:{...overview.result.structuredContent,task_instance_id:'new-instance',progress:{...overview.result.structuredContent.progress,task_instance_id:'new-instance'}}}});
  await recovered.dispatch(event);
  assert.equal(recovered.calls.length, 2);
  assert.equal(recovered.getKv().taskProgressContextsByHostSession.host.task_instance_id, 'new-instance');
  assert.equal(recovered.sent[0].action, 'context');

  const failed = harness(current, { ok: false });
  await failed.dispatch(event);
  assert.equal(failed.sent[0].action, 'allow');

  const cleared = harness(current, { ok: true, result: { structuredContent: { session_id: session } } });
  await cleared.dispatch({ type: 'tool-call', callId: 'call-1', tool: 'clear_progress',
    args: { session_id: session, session_context: { session_id: 'host' } } });
  assert.equal(cleared.getKv().taskProgressContextsByHostSession.host, undefined);
  await cleared.dispatch(event);
  assert.equal(cleared.sent.at(-1).action, 'allow');

  const lifecycle = harness({ taskProgressActiveHostSessionId: 'host',
    taskProgressSessionsByHostSession: { host: session } });
  await lifecycle.dispatch({ type: 'event', name: 'did-task-state-changed',
    data: { sessionId: 'host', state: { activity: { recordStatus: 'failed' } } } });
  assert.equal(lifecycle.broadcasts[0].type, 'refresh-mapped-session');
  assert.equal(lifecycle.broadcasts[0].session_id, session);
  assert.equal(lifecycle.calls.length, 0);
  const firstReceipt = lifecycle.broadcasts[0];
  await lifecycle.deliverPanel({ ...firstReceipt, type: 'panel-refresh-result',
    status: 'read_ok', state_revision: 7 });
  await lifecycle.deliverPanel({ ...firstReceipt, type: 'panel-refresh-result',
    status: 'read_ok', state_revision: 7 });
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-task-state-changed'].refresh_ok, 1);
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-task-state-changed'].last_refresh.state_revision, 7);
  await lifecycle.dispatch({ type: 'event', name: 'did-message-created',
    data: { sessionId: 'host', messageId: 'saved-assistant-1', role: 'assistant', content: 'Ignored' } });
  assert.equal(lifecycle.broadcasts[1].type, 'refresh-mapped-session');
  await lifecycle.dispatch({ type: 'event', name: 'did-message-created',
    data: { sessionId: 'host', messageId: 'saved-user-1', role: 'user', content: 'Ignored' } });
  assert.equal(lifecycle.broadcasts.length, 2);
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-message-created'].received, 2);
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-message-created'].refresh_requested, 1);
  await lifecycle.deliverPanel({ ...lifecycle.broadcasts[1], type: 'panel-refresh-result', status: 'read_failed' });
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-message-created'].refresh_failed, 1);
  await lifecycle.dispatch({ type: 'tool-call', callId: 'read-diagnostics', tool: 'get_hook_diagnostics',
    args: { session_context: { session_id: 'host' } } });
  const view = lifecycle.sent.at(-1).result.structuredContent;
  assert.equal(view.events['did-message-created'].received, 2);
  assert.equal(view.pending_refresh_count, 0);
  await lifecycle.dispatch({ type: 'tool-call', callId: 'other-diagnostics', tool: 'get_hook_diagnostics',
    args: { session_context: { session_id: 'another-host' } } });
  assert.equal(Object.keys(lifecycle.sent.at(-1).result.structuredContent.events).length, 0);
  const unrelated = harness({ taskProgressActiveHostSessionId: 'other',
    taskProgressSessionsByHostSession: { host: session } });
  await unrelated.dispatch({ type: 'event', name: 'did-task-state-changed', data: { sessionId: 'host' } });
  await unrelated.dispatch({ type: 'event', name: 'did-message-created',
    data: { sessionId: 'host', messageId: 'saved-assistant-2', role: 'assistant' } });
  assert.equal(unrelated.broadcasts.length, 0);

  const switched = harness({ taskProgressSessionsByHostSession: { host: session } });
  await switched.dispatch({ type: 'event', name: 'did-session-switched', data: { sessionId: 'host' } });
  assert.equal(switched.getKv().taskProgressActiveHostSessionId, 'host');
  assert.equal(switched.broadcasts[0].type, 'bind-session');
  assert.equal(switched.broadcasts[0].session_id, session);
  assert.equal(switched.getKv().taskProgressHookDiagnostics.host.events['did-session-switched'].refresh_requested, 1);
  console.log('Cindy Hook contract tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
