const { spawn } = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const { createExportTransfers } = require('./export-transfer.cjs');
const { createReviewQueueHandler } = require('./review-queue.cjs');
const pending = new Map();
let nextId = 1;
let bridgeFailure;
const bridge = spawn(process.env.TASK_PROGRESS_PYTHON || 'python3', [
  path.join(__dirname, '..', 'core', 'mcp_bridge.py')
], { stdio: ['pipe', 'pipe', 'pipe'] });

function unavailable(id) {
  return { jsonrpc: '2.0', id, error: { code: -32000, message: bridgeFailure } };
}

function failBridge(message) {
  if (bridgeFailure) return;
  bridgeFailure = message;
  // A request without a reply may already have changed state. Do not replay it.
  for (const [id, resolve] of pending) resolve(unavailable(id));
  pending.clear();
  process.stderr.write(`${message}\n`);
}

function failWrite(error) {
  failBridge(`Task Progress service bridge input failed (${error.code || error.message}).`);
}

bridge.once('error', error => {
  failBridge(`Task Progress service bridge could not start (${error.code || error.message}).`);
});
bridge.stdin.on('error', failWrite);
bridge.once('close', (code, signal) => {
  failBridge(`Task Progress service bridge exited (${signal || `code ${code}`}).`);
});

bridge.stderr.on('data', chunk => process.stderr.write(chunk));
readline.createInterface({ input: bridge.stdout }).on('line', line => {
  let response;
  try { response = JSON.parse(line); } catch { return; }
  if (response.id == null) return;
  const waiter = pending.get(response.id);
  if (waiter) { pending.delete(response.id); waiter(response); }
});

function writeBridge(line) {
  if (bridgeFailure) return;
  bridge.stdin.write(line + '\n', error => {
    if (error) failWrite(error);
  });
}

function rpc(message) {
  return new Promise(resolve => {
    const id = nextId++;
    if (bridgeFailure) { resolve(unavailable(id)); return; }
    pending.set(id, resolve);
    writeBridge(JSON.stringify({ ...message, id }));
  });
}
function reply(message) { process.stdout.write(JSON.stringify(message) + '\n'); }
const handleExportTransfer = createExportTransfers(rpc);
const handleReviewQueue = createReviewQueueHandler(rpc);

readline.createInterface({ input: process.stdin }).on('line', async line => {
  let request;
  try { request = JSON.parse(line); }
  catch { reply({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }); return; }
  if (!Object.hasOwn(request, 'id')) { writeBridge(line); return; }
  if (bridgeFailure) { reply(unavailable(request.id)); return; }
  const response = await handleExportTransfer(request) || await handleReviewQueue(request) || await rpc(request);
  reply({ ...response, id: request.id });
});
