'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../plugins/task-progress/cindy/panel.js'), 'utf8');
const timers = new Map();
const posted = [];
let nextTimer = 0;
let bus;
let reads = 0;
let resultFromWorker;
let bridge;
const bridgeSource = fs.readFileSync(path.join(__dirname, '../plugins/task-progress/cindy/main.js'), 'utf8');
vm.runInNewContext(bridgeSource, {
  BroadcastChannel: class {
    constructor() { bridge = this; }
    postMessage(message) { queueMicrotask(() => bus.onmessage({ data: message })); }
  },
  cindy: { onHostMessage() {}, node: {request: async () => ({ok:true,result:resultFromWorker})} },
  Date, setTimeout, clearTimeout,
});
const session = 'task-session-1';
const result = { structuredContent: { session_id: session, state_revision: 9, progress: { task_title: 'Fixture' } } };
resultFromWorker = result;
const frame = { style: {}, contentWindow: { postMessage() {} }, setAttribute() {} };
const status = { textContent: '', hidden: false };
const context = vm.createContext({
  document: { getElementById: id => id === 'progress-app' ? frame : status,
    documentElement: {dataset:{mobile:'true'}}, head: {} },
  window: { setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => timers.delete(id), setInterval: () => ++nextTimer,
    clearInterval() {}, addEventListener() {} },
  BroadcastChannel: class {
    constructor() { bus = this; }
    postMessage(message) {
      posted.push(message);
      if (message.type === 'panel-tool-request') {
        reads++;
        queueMicrotask(() => bridge.onmessage({data:message}));
      }
    }
  },
  fetch: async url => url === '/kv'
    ? { ok: true, json: async () => ({ taskProgressActiveHostSessionId: 'host',
      taskProgressSessionsByHostSession: { host: session } }) }
    : { ok: true, status: 200 },
  localStorage: { setItem() {}, removeItem() {}, getItem: () => null },
  matchMedia: () => ({ matches: true, addEventListener() {} }),
  getComputedStyle: () => ({ colorScheme: 'light' }),
  MutationObserver: class { observe() {} },
  console, AbortController,
});
vm.runInContext(source, context);
vm.runInContext(`sessionId = '${session}'; appInitialized = true`, context);

async function flush() { await new Promise(resolve => setImmediate(resolve)); }
function receipt(name, id) {
  return { type: 'refresh-mapped-session', trigger: name, receipt_id: id,
    host_session_id: 'host', session_id: session };
}

(async () => {
  bus.onmessage({ data: receipt('did-message-created', 'm1') });
  bus.onmessage({ data: receipt('did-task-state-changed', 's1') });
  const refreshTimer = [...timers.values()].find(item => item.delay === 250);
  assert.ok(refreshTimer);
  refreshTimer.fn();
  await flush();
  assert.equal(reads, 1, 'two nearby events share one Task Progress read');
  const acknowledgements = posted.filter(item => item.type === 'panel-refresh-result');
  assert.deepEqual(acknowledgements.map(item => item.receipt_id), ['m1', 's1']);
  assert.ok(acknowledgements.every(item => item.status === 'read_ok' && item.state_revision === 9));

  bus.onmessage({ data: { type: 'bind-session', trigger: 'did-session-switched',
    receipt_id: 'switch1', host_session_id: 'host', session_id: session } });
  await flush();
  assert.equal(posted.at(-1).status, 'skipped_same_session');
  for (const failure of [{isError:true,structuredContent:result.structuredContent},
      {result:{isError:true,structuredContent:result.structuredContent}}]) {
    resultFromWorker = failure;
    bus.onmessage({data:receipt('did-message-created','failed-' + reads)});
    const timer = [...timers.values()].filter(item => item.delay === 250).at(-1);
    timer.fn();
    await flush();
    assert.equal(posted.at(-1).status,'read_failed');
    assert.equal(posted.at(-1).state_revision,undefined);
  }
  for (const invalid of [null, [], 'invalid', 42, true]) {
    context.fetch = async url => url === '/kv' ? {ok:true,json:async()=>invalid} : {ok:true,status:200};
    const refresh = receipt('did-session-switched', `invalid-${String(invalid)}`);
    await context.syncSessionFromKV(refresh);
    assert.equal(vm.runInContext('sessionId',context),session,'invalid storage must not clear a confirmed binding');
    assert.equal(posted.at(-1).status,'read_failed','invalid storage must be reported as an unavailable read');
  }
  context.fetch = async url => url === '/kv' ? {ok:true,json:async()=>({taskProgressActiveHostSessionId:'host',taskProgressSessionsByHostSession:{host:session}})} : {ok:true,status:200};
  await context.syncSessionFromKV(receipt('did-session-switched','recovered'));
  assert.equal(vm.runInContext('sessionId',context),session);
  assert.equal(posted.at(-1).status,'skipped_same_session');
  context.fetch = async url => url === '/kv' ? {ok:true,json:async()=>({})} : {ok:true,status:200};
  await context.syncSessionFromKV(receipt('did-session-switched','confirmed-empty'));
  assert.equal(vm.runInContext('sessionId',context),'','a confirmed empty state clears the old binding');
  let releaseStale, kvReads = 0;
  const newerSession = 'task-session-2';
  resultFromWorker = {structuredContent:{session_id:newerSession,state_revision:10,progress:{task_title:'New'}}};
  context.fetch = async url => {
    if (url !== '/kv') return {ok:true,status:200};
    if (++kvReads === 1) return {ok:true,json:()=>new Promise(resolve=>{releaseStale=()=>resolve({taskProgressActiveHostSessionId:'host',taskProgressSessionsByHostSession:{host:session}});})};
    return {ok:true,json:async()=>({taskProgressActiveHostSessionId:'host',taskProgressSessionsByHostSession:{host:newerSession}})};
  };
  const oldSync=context.syncSessionFromKV(receipt('did-session-switched','old-read'));
  await flush();
  bus.onmessage({data:{type:'bind-session',trigger:'did-session-switched',receipt_id:'new-read',host_session_id:'host',session_id:newerSession}});
  releaseStale();
  await oldSync;
  await flush();
  await flush();
  assert.equal(vm.runInContext('sessionId',context),newerSession,'a switch during KV read triggers a fresh read instead of applying a stale binding');
  assert.equal(posted.find(item=>item.receipt_id==='old-read')?.status,'skipped_session_changed');
  assert.notEqual(posted.find(item=>item.receipt_id==='new-read')?.status,'skipped_sync_busy');
  for (const phase of ['fetch','body']) {
    let releaseLate, signal, calls = 0;
    const target = `recovered-${phase}`;
    context.fetch = (url, options) => {
      if (url !== '/kv') return Promise.resolve({ok:true,status:200});
      if (++calls > 1) return Promise.resolve({ok:true,json:async()=>({taskProgressActiveHostSessionId:'host',taskProgressSessionsByHostSession:{host:target}})});
      signal=options?.signal;
      const stale = {taskProgressActiveHostSessionId:'host',taskProgressSessionsByHostSession:{host:'stale'}};
      return phase === 'fetch' ? new Promise(resolve=>{releaseLate=()=>resolve({ok:true,json:async()=>stale});})
        : Promise.resolve({ok:true,json:()=>new Promise(resolve=>{releaseLate=()=>resolve(stale);})});
    };
    const blocked=context.syncSessionFromKV(receipt('did-session-switched',`hung-${phase}`));
    await flush();
    bus.onmessage({data:{type:'bind-session',trigger:'did-session-switched',receipt_id:`next-${phase}`,host_session_id:'host',session_id:target}});
    const deadline=[...timers.values()].filter(item=>item.delay===1200).at(-1);
    assert.ok(deadline,`${phase} read has a bounded deadline`);
    deadline.fn();
    await blocked;
    await flush(); await flush();
    assert.equal(vm.runInContext('sessionId',context),target,'queued switch resumes after timeout');
    if (signal) assert.equal(signal.aborted,true,'timed out read is aborted when supported');
    releaseLate();
    await flush();
    assert.equal(vm.runInContext('sessionId',context),target,'late old read cannot replace recovered binding');
  }
  context.fetch = async () => new Promise(()=>{});
  const loneTimeout=context.syncSessionFromKV(receipt('did-session-switched','lone-timeout'));
  await flush();
  [...timers.values()].filter(item=>item.delay===1200).at(-1).fn();
  await loneTimeout;
  assert.equal(posted.find(item=>item.receipt_id==='lone-timeout')?.status,'read_failed');
  assert.equal(vm.runInContext('sessionId',context),'recovered-body','timeout preserves the confirmed binding');
  console.log('Cindy Hook panel receipt tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
