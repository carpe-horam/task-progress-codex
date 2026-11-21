'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const readline = require('node:readline');
const { spawn } = require('node:child_process');
const { createExportTransfers } = require('../plugins/task-progress/cindy/node/export-transfer.cjs');

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cindy-large-export-'));
  const source = path.resolve(__dirname, '../plugins/task-progress');
  fs.mkdirSync(path.join(tmp, 'node')); fs.mkdirSync(path.join(tmp, 'core'));
  for (const name of ['worker.cjs', 'export-transfer.cjs', 'review-queue.cjs']) fs.copyFileSync(path.join(source, 'cindy/node', name), path.join(tmp, 'node', name));
  // Optional negative control changes only the isolated copy, recreating the
  // original missing-envelope bug even while another agent fixes the source.
  if (process.env.CINDY_EXPORT_TEST_LEGACY_JSONRPC === '1') {
    const copy=path.join(tmp,'node/export-transfer.cjs');
    const current=fs.readFileSync(copy,'utf8');
    const legacy=current.replace(/rpc\(\{\s*jsonrpc:\s*['"]2\.0['"],\s*method:/, "rpc({ method:");
    assert.notEqual(legacy,current,'negative control must remove the finish JSON-RPC version');
    fs.writeFileSync(copy,legacy);
  }
  // Exercise the actual JSON-RPC validator and writer with isolated HOME.
  // Calling save_export_files directly misses malformed internal finish frames.
  // This bridge never starts or attaches to the user's shared daemon.
  fs.writeFileSync(path.join(tmp, 'core/mcp_bridge.py'), `import sys, json\nsys.path.insert(0, ${JSON.stringify(source)})\nimport server\nfor line in sys.stdin:\n    request=json.loads(line)\n    reply=server.handle(request)\n    if reply is not None:\n        response=json.loads(reply)\n        if response.get('error'):\n            print('isolated JSON-RPC response: '+json.dumps(response,ensure_ascii=False),file=sys.stderr,flush=True)\n        print(reply,flush=True)\n`);
  const worker = spawn(process.execPath, [path.join(tmp, 'node/worker.cjs')], {env:{...process.env, HOME:tmp},stdio:['pipe','pipe','pipe']});
  let sequence=0, bridgeCalls=0, maxParams=0, panel;
  const rpcTimeoutMs=Number(process.env.CINDY_EXPORT_TEST_RPC_TIMEOUT_MS||10000);
  assert(Number.isFinite(rpcTimeoutMs)&&rpcTimeoutMs>=100);
  const pending=new Map(), broadcasts=[];
  function rejectPending(error) { for (const waiter of pending.values()) { clearTimeout(waiter.timer); waiter.reject(error); } pending.clear(); }
  readline.createInterface({input:worker.stdout}).on('line', line=>{
    try {
      const response=JSON.parse(line),waiter=pending.get(response.id);
      assert(waiter,`worker must preserve original string request id ${response.id}`);
      pending.delete(response.id); clearTimeout(waiter.timer); waiter.resolve(response);
    } catch(error) { rejectPending(error); }
  });
  worker.on('exit',code=>rejectPending(new Error(`isolated worker exited (${code})`)));
  worker.on('error',rejectPending);
  worker.stderr.on('data', b=>process.stderr.write(b));
  const request = async value => {
    const bytes=Buffer.byteLength(JSON.stringify(value.params)); maxParams=Math.max(maxParams,bytes);
    assert(bytes <= 240 * 1024, `native host request safety budget exceeded: ${bytes}`);
    bridgeCalls++;
    if (process.env.CINDY_EXPORT_RPC_DELAY_MS) await new Promise(resolve=>setTimeout(resolve,Number(process.env.CINDY_EXPORT_RPC_DELAY_MS)));
    const id=`cindy-${++sequence}`;
    const response=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`isolated RPC ${value.params?.name||value.method} timed out after ${rpcTimeoutMs}ms; internal JSON-RPC envelope must be valid`));},rpcTimeoutMs);
      pending.set(id,{resolve,reject,timer});
      worker.stdin.write(JSON.stringify({jsonrpc:'2.0',...value,id})+'\n',error=>{if(error){clearTimeout(timer);pending.delete(id);reject(error);}});
    });
    return response.error ? {ok:false,message:response.error.message} : {ok:true,result:response.result};
  };
  const created=await request({method:'tools/call',params:{name:'init_task_network',arguments:{task_title:'隔离验收队列',atomic_reason:'仅验证内部路由'}}});
  assert(created.ok,JSON.stringify(created));
  const {session_id,task_instance_id}=created.result.structuredContent;
  const queue=await request({method:'tools/call',params:{name:'cindy_review_collect',arguments:{session_id,task_instance_id}}});
  assert(queue.ok,JSON.stringify(queue));assert.deepEqual(queue.result.structuredContent.items,[]);
  const context={BroadcastChannel:class{constructor(){panel=this;}postMessage(v){broadcasts.push(v);}},cindy:{onHostMessage(){},node:{request},send:async()=>{}},fetch:async()=>({ok:true,json:async()=>({})}),Date,String,JSON,Error,TextEncoder};
  vm.runInNewContext(fs.readFileSync(path.join(source,'cindy/main.js'),'utf8'),context);
  const html='<!doctype html><meta charset="utf-8"><title>128项导出</title>'+('任务记录🙂\n'.repeat(65000));
  const workbook=Buffer.from('PK'+'workbook-content'.repeat(16000));
  const files=[{name:'Task-Progress-native-large.xlsx',mime_type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',encoding:'base64',content:workbook.toString('base64')},{name:'Task-Progress-native-large.html',mime_type:'text/html',encoding:'utf-8',content:html}];
  try {
    assert(Buffer.byteLength(JSON.stringify({files}))>262144);
    await panel.onmessage({data:{type:'panel-tool-request',requestId:'large-export-1',name:'save_export_files',arguments:{files}}});
    const receipt=broadcasts.at(-1); assert.equal(receipt.type,'panel-tool-result',JSON.stringify(receipt));
    const out=path.join(tmp,'Downloads/Task Progress Exports');
    assert.equal(fs.readFileSync(path.join(out,files[1].name),'utf8'),html);
    assert.deepEqual(fs.readFileSync(path.join(out,files[0].name)),workbook);
    const count=bridgeCalls;
    await panel.onmessage({data:{type:'panel-tool-request',requestId:'large-export-1',name:'save_export_files',arguments:{files}}});
    assert.equal(bridgeCalls,count,'panel replay must not duplicate exports');
    assert.equal(fs.readdirSync(out).length,2);
    await panel.onmessage({data:{type:'panel-tool-request',requestId:'bad-export',name:'save_export_files',arguments:{files:files.map(f=>({...f,name:'../'+f.name}))}}});
    assert.equal(broadcasts.at(-1).type,'panel-tool-result','MCP application errors retain the tool-result envelope');
    assert.equal(broadcasts.at(-1).result?.isError,true,'real shared validator rejects paths');
    assert.match(broadcasts.at(-1).result.content.map(item=>item.text||'').join('\n'),/不能包含路径/);
    assert.equal(fs.readdirSync(out).length,2);
    const worstHtml='<!doctype html>'+ '中'.repeat(140000) + ('控制"\\\n\r\t\u0000🙂'.repeat(12000));
    const worstFiles=files.map(f=>({...f,name:f.name.replace('native-large','native-escaping'),...(f.encoding==='utf-8'?{content:worstHtml}:{})}));
    await panel.onmessage({data:{type:'panel-tool-request',requestId:'worst-escaping-export',name:'save_export_files',arguments:{files:worstFiles}}});
    assert.equal(broadcasts.at(-1).type,'panel-tool-result',JSON.stringify(broadcasts.at(-1)));
    assert.equal(fs.readFileSync(path.join(out,worstFiles[1].name),'utf8'),worstHtml,'dense multibyte text and escapes preserve bytes');
    assert.deepEqual(fs.readFileSync(path.join(out,worstFiles[0].name)),workbook);
    assert(maxParams > 190000,'worst case must exercise dense 3-byte Unicode chunks');
    // Reordering, truncation, replay and abandonment never reach the writer.
    let clock=100, written=0;
    const handle=createExportTransfers(async()=>{written++;return{result:{}};},()=>clock);
    const send=(name,args)=>handle({id:name,method:'tools/call',params:{name:'cindy_export_'+name,arguments:args}});
    let id=(await send('begin',{length:10})).result.structuredContent.transfer_id;
    assert((await send('append',{transfer_id:id,offset:1,chunk:'x'})).error);
    assert((await send('finish',{transfer_id:id})).error);
    id=(await send('begin',{length:10})).result.structuredContent.transfer_id;
    assert((await send('finish',{transfer_id:id})).error);
    id=(await send('begin',{length:2})).result.structuredContent.transfer_id;
    clock+=300001; assert((await send('append',{transfer_id:id,offset:0,chunk:'{}'})).error);
    assert.equal(written,0);
    console.log(JSON.stringify({pass:true,payloadBytes:Buffer.byteLength(JSON.stringify({files})),maxNativeRequestBytes:maxParams,bridgeCalls:count,delayPerRequestMs:Number(process.env.CINDY_EXPORT_RPC_DELAY_MS||0),rpcTimeoutMs,checks:['finish passes the real server.handle JSON-RPC validator','real writer preserves both files byte-for-byte','each serialized native request below 240 KiB including dense Chinese, escaping and emoji','request ids preserved across chunk and bridge requests','replayed panel request writes once','filename traversal rejected through the real MCP error envelope','invalid order, incomplete transfer and expiry rejected']},null,2));
  } finally { rejectPending(new Error('isolated test finished')); worker.stdin.end(); worker.kill(); fs.rmSync(tmp,{recursive:true,force:true}); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
