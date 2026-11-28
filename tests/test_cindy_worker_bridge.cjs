'use strict';
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const test = require('node:test');

const source = path.resolve(__dirname, '../plugins/task-progress/cindy/node');

function startWorker(t, pythonFixture) {
  const root = mkdtempSync(path.join(os.tmpdir(), 'task-progress-worker-test-'));
  cpSync(source, path.join(root, 'node'), { recursive: true });
  mkdirSync(path.join(root, 'core'));
  const log = path.join(root, 'writes.log');
  if (pythonFixture) writeFileSync(path.join(root, 'core', 'mcp_bridge.py'), pythonFixture);
  const worker = spawn(process.execPath, [path.join(root, 'node', 'worker.cjs')], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
      ...process.env,
      TASK_PROGRESS_PYTHON: pythonFixture
        ? process.env.TASK_PROGRESS_PYTHON || 'python3'
        : path.join(root, 'missing-python'),
      BRIDGE_TEST_LOG: log,
      BRIDGE_TEST_ROOT: root,
    },
  });
  const done = once(worker, 'close');
  const lines = readline.createInterface({ input: worker.stdout })[Symbol.asyncIterator]();
  worker.stderr.resume();
  t.after(async () => {
    worker.kill();
    await done;
    rmSync(root, { recursive: true, force: true });
  });
  return {
    send(message) { worker.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`); },
    async receive() {
      const result = await lines.next();
      assert.equal(result.done, false, 'the worker closed before replying');
      return JSON.parse(result.value);
    },
    readWrites() { return readFileSync(log, 'utf8').trim().split('\n'); },
  };
}

function assertUnavailable(response, id, reason) {
  assert.equal(response.jsonrpc, '2.0');
  assert.equal(response.id, id);
  assert.equal(response.error?.code, -32000);
  assert.match(response.error.message, reason);
}

test('resident worker forwards host archive RPC and exact target identity', {timeout:5000}, async t => {
  const worker = startWorker(t, `
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' in request:
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],
                          'result':{'method':request['method'],'params':request.get('params')}}), flush=True)
`);
  const params = {session_id:'00000000-0000-4000-8000-000000000001',host_session_id:'host-test',source:'cindy'};
  worker.send({id:'host-archive-wire',method:'task-progress/archive-host-session',params});
  const response = await worker.receive();
  assert.equal(response.id,'host-archive-wire');
  assert.equal(response.result.method,'task-progress/archive-host-session');
  assert.deepEqual(response.result.params,params);
});

test('Python spawn failure returns a JSON-RPC error for each call', { timeout: 5000 }, async t => {
  const worker = startWorker(t);
  worker.send({ method: 'notifications/initialized' });
  worker.send({ id: 'initial', method: 'tools/list' });
  assertUnavailable(await worker.receive(), 'initial', /could not start.*ENOENT/);

  worker.send({ id: 'later', method: 'tools/list' });
  assertUnavailable(await worker.receive(), 'later', /could not start.*ENOENT/);
});

test('a closed Python input pipe fails pending and future calls before process exit', { timeout: 5000 }, async t => {
  const worker = startWorker(t, `
import json
import os
import sys
import time

json.loads(sys.stdin.readline())
ready = json.loads(sys.stdin.readline())
os.close(0)
print(json.dumps({'jsonrpc': '2.0', 'id': ready['id'], 'result': 'input closed'}), flush=True)
deadline = time.monotonic() + 10
while time.monotonic() < deadline and os.path.exists(os.environ['BRIDGE_TEST_ROOT']):
    time.sleep(0.01)
`);
  worker.send({ id: 'pending write', method: 'write_one' });
  worker.send({ id: 'ready', method: 'close_input' });
  assert.deepEqual(await worker.receive(), { jsonrpc: '2.0', id: 'ready', result: 'input closed' });

  worker.send({ id: 'failed write', method: 'write_two' });
  const responses = new Map();
  for (let i = 0; i < 2; i++) {
    const response = await worker.receive();
    responses.set(response.id, response);
  }
  const reason = /input failed.*(?:EPIPE|ECONNRESET|ERR_STREAM_DESTROYED)/;
  assertUnavailable(responses.get('pending write'), 'pending write', reason);
  assertUnavailable(responses.get('failed write'), 'failed write', reason);
  worker.send({ id: 'later', method: 'tools/list' });
  assertUnavailable(await worker.receive(), 'later', reason);
});

test('Python exit preserves replies and fails unresolved calls without replaying writes', { timeout: 5000 }, async t => {
  const worker = startWorker(t, `
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'shutdown_unexpectedly':
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': 'ack'}), flush=True)
        sys.exit(19)
    with open(os.environ['BRIDGE_TEST_LOG'], 'a') as log:
        log.write(method + '\\n')
`);
  worker.send({ id: 'first write', method: 'write_one' });
  worker.send({ id: 'second write', method: 'write_two' });
  worker.send({ id: 'ack at exit', method: 'shutdown_unexpectedly' });

  const responses = new Map();
  for (let i = 0; i < 3; i++) {
    const response = await worker.receive();
    responses.set(response.id, response);
  }
  assert.equal(responses.get('ack at exit').result, 'ack');
  assertUnavailable(responses.get('first write'), 'first write', /exited.*code 19/);
  assertUnavailable(responses.get('second write'), 'second write', /exited.*code 19/);

  worker.send({ method: 'notifications/initialized' });
  worker.send({ id: 'later write', method: 'write_after_exit' });
  assertUnavailable(await worker.receive(), 'later write', /exited.*code 19/);
  assert.deepEqual(worker.readWrites(), ['write_one', 'write_two']);
});
