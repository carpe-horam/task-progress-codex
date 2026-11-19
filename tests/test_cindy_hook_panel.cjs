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
const session = 'task-session-1';
const result = { structuredContent: { session_id: session, state_revision: 9, progress: { task_title: 'Fixture' } } };
const frame = { style: {}, contentWindow: { postMessage() {} }, setAttribute() {} };
const status = { textContent: '', hidden: false };
const context = vm.createContext({
  document: { getElementById: id => id === 'progress-app' ? frame : status,
    documentElement: {}, head: {} },
  window: { setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => timers.delete(id), setInterval: () => ++nextTimer,
    clearInterval() {}, addEventListener() {} },
  BroadcastChannel: class {
    constructor() { bus = this; }
    postMessage(message) {
      posted.push(message);
      if (message.type === 'panel-tool-request') {
        reads++;
        queueMicrotask(() => bus.onmessage({ data: { type: 'panel-tool-result',
          requestId: message.requestId, result } }));
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
  console,
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
  console.log('Cindy Hook panel receipt tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
