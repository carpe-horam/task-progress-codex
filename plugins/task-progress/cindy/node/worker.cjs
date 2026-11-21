const { spawn } = require('node:child_process');
const path = require('node:path');
const readline = require('node:readline');
const { createExportTransfers } = require('./export-transfer.cjs');
const { createReviewQueueHandler } = require('./review-queue.cjs');
const pending = new Map();
let nextId = 1;
const bridge = spawn(process.env.TASK_PROGRESS_PYTHON || 'python3', [
  path.join(__dirname, '..', 'core', 'mcp_bridge.py')
], { stdio: ['pipe', 'pipe', 'pipe'] });

bridge.stderr.on('data', chunk => process.stderr.write(chunk));
readline.createInterface({ input: bridge.stdout }).on('line', line => {
  let response;
  try { response = JSON.parse(line); } catch { return; }
  if (response.id == null) return;
  const waiter = pending.get(response.id);
  if (waiter) { pending.delete(response.id); waiter(response); }
});
bridge.on('exit', code => {
  for (const [id, resolve] of pending) {
    resolve({ jsonrpc: '2.0', id, error: { code: -32000, message: 'Task Progress service bridge exited.' } });
  }
  pending.clear();
  process.stderr.write(`Task Progress bridge exited (${code}).\n`);
});

function rpc(message) {
  return new Promise(resolve => {
    const id = nextId++;
    pending.set(id, resolve);
    bridge.stdin.write(JSON.stringify({ ...message, id }) + '\n');
  });
}
function reply(message) { process.stdout.write(JSON.stringify(message) + '\n'); }
const handleExportTransfer = createExportTransfers(rpc);
const handleReviewQueue = createReviewQueueHandler(rpc);

readline.createInterface({ input: process.stdin }).on('line', async line => {
  let request;
  try { request = JSON.parse(line); }
  catch { reply({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }); return; }
  if (!Object.hasOwn(request, 'id')) { bridge.stdin.write(line + '\n'); return; }
  const response = await handleExportTransfer(request) || await handleReviewQueue(request) || await rpc(request);
  reply({ ...response, id: request.id });
});
