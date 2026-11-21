'use strict';
const { randomUUID } = require('node:crypto');
const LIMIT = 60 * 1024 * 1024;
const TTL = 5 * 60 * 1000;

// Private transport only: final writes still use the shared export validator.
function createExportTransfers(rpc, now = Date.now) {
  const transfers = new Map();
  function expire() {
    for (const [id, item] of transfers) if (now() - item.updated > TTL) transfers.delete(id);
  }
  return async function handle(request) {
    if (request.method !== 'tools/call' || !request.params?.name?.startsWith('cindy_export_')) return null;
    expire();
    const args = request.params.arguments || {};
    const operation = request.params.name.slice('cindy_export_'.length);
    try {
      if (operation === 'begin') {
        if (!Number.isSafeInteger(args.length) || args.length <= 0 || args.length > LIMIT) throw new Error('导出传输大小无效。');
        if (transfers.size >= 2) throw new Error('已有导出正在传输，请稍后重试。');
        const id = randomUUID();
        transfers.set(id, { length: args.length, received: 0, bytes: 0, chunks: [], updated: now() });
        return { jsonrpc: '2.0', id: request.id, result: { content: [], structuredContent: { transfer_id: id } } };
      }
      const transfer = transfers.get(args.transfer_id);
      if (!transfer) throw new Error('导出传输已失效，请重新导出。');
      transfer.updated = now();
      if (operation === 'abort') transfers.delete(args.transfer_id);
      else if (operation === 'append') {
        if (args.offset !== transfer.received || typeof args.chunk !== 'string' || !args.chunk.length || args.chunk.length > 65536) throw new Error('导出分块顺序或大小无效。');
        transfer.received += args.chunk.length;
        transfer.bytes += Buffer.byteLength(args.chunk, 'utf8');
        if (transfer.received > transfer.length || transfer.bytes > LIMIT) throw new Error('导出内容超过传输大小限制。');
        transfer.chunks.push(args.chunk);
      } else if (operation === 'finish') {
        transfers.delete(args.transfer_id); // A repeated finish must never write a second pair.
        if (transfer.received !== transfer.length) throw new Error('导出内容不完整。');
        const payload = JSON.parse(transfer.chunks.join(''));
        if (!payload || typeof payload !== 'object' || Array.isArray(payload) || Object.keys(payload).some(key => key !== 'files')) throw new Error('导出参数无效。');
        return { ...await rpc({ jsonrpc: '2.0', method: 'tools/call', params: { name: 'save_export_files', arguments: payload } }), id: request.id };
      } else throw new Error('未知导出操作。');
      return { jsonrpc: '2.0', id: request.id, result: { content: [], structuredContent: { received: transfer.received } } };
    } catch (error) {
      if (args.transfer_id) transfers.delete(args.transfer_id);
      return { jsonrpc: '2.0', id: request.id, error: { code: -32602, message: String(error.message || error) } };
    }
  };
}
module.exports = { createExportTransfers };
