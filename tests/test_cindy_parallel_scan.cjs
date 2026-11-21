'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createHost}=require('./browser_host.cjs');
const {createReviewQueueHandler}=require('../plugins/task-progress/cindy/node/review-queue.cjs');
(async()=>{const h=await createHost();try{
 const main={execution_mode:'main_session',self_execution_reason:'隔离扫描测试'},sub={execution_mode:'subagent',tool_identifier:'fixture.spawn',dispatch_text:'生成隔离测试结果'};
 const init=await h.rawRpc('init_task_network',{task_title:'多实例任务',tasks:[{id:'a',title:'工作一',assignment:main},{id:'b',title:'工作二',assignment:main}]});
 const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
 for(let i=0;i<21;i++)await h.rawRpc('route_request',{...binding,request_id:'p'+i,request_text:'并行 '+i,task_title:'并行 '+i,mode:'parallel',reason:'实例扫描边界',acceptance_criteria:['结果可验收']});
 const ctx=await h.rawRpc('get_progress',{...binding,view:'context',limit:100});
 const peer=ctx.structuredContent.progress.session_work.requests.find(x=>x.request_id==='p20').task_instance_id,peerBinding={session_id:binding.session_id,task_instance_id:peer};
 await h.rawRpc('decompose_task',{...peerBinding,task_id:'root',tasks:[{id:'work',title:'并行结果',assignment:sub}]});
 const run=await h.rawRpc('update_task',{...peerBinding,task_id:'work',execution_status:'running',assignment:sub}),attempt=run.structuredContent.attempt_id;
 await h.rawRpc('bind_task_agent',{...peerBinding,task_id:'work',attempt_id:attempt,agent_name:'fixture'});
 await h.rawRpc('update_task',{...peerBinding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'结果',evidence:['fixture://result']});
 const handler=createReviewQueueHandler(async req=>({result:await h.rawRpc(req.params.name,req.params.arguments)})),calls=[];
 function harness(){let kv={taskProgressSessionsByHostSession:{host:binding.session_id},taskProgressContextsByHostSession:{host:binding},taskProgressAutoReview:{host:{enabled:true,session_id:binding.session_id,ledger:{}}}};
  const sandbox={BroadcastChannel:class{postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms);if(ms>=1000)timer.unref();return timer;},clearTimeout,fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},cindy:{onHostMessage(){},send:async()=>{},node:{request:async req=>{const r=await handler({jsonrpc:'2.0',id:1,...req});return r.error?{ok:false,message:r.error.message}:{ok:true,result:r.result}}},agent:{run:async args=>{calls.push(args);return {ok:true,mode:'continue',sessionId:'host'}}}}};
  vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.dispatch=dispatchDueReviews;globalThis.schedule=scheduleDueReviews;globalThis.stop=observeReviewHostActivity;',sandbox);return sandbox;}
 const active=harness();assert.equal(await active.dispatch('host'),'scan_more');assert.equal(await active.dispatch('host'),'dispatched');assert.equal(calls.length,1);assert.equal(calls[0].event.task_instance_id,peer);assert.equal(calls[0].event.foreground_task_instance_id,binding.task_instance_id);
 const stopped=harness();assert.equal(await stopped.dispatch('host'),'scan_more');stopped.stop({data:{sessionId:'host',state:{activity:{gracefulStopState:'requested'}}}});await new Promise(resolve=>setTimeout(resolve,20));assert.equal(await stopped.dispatch('host'),'host_suspended');assert.equal(calls.length,1,'a stop between instance windows prevents dispatch');
 const scheduled=harness();scheduled.schedule('host',1);const deadline=Date.now()+5000;while(calls.length<2 && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,25));assert.equal(calls.length,2,'scheduler continues to the next instance window without another event');assert.equal(calls[1].event.task_instance_id,peer);
 const changed=harness();assert.equal(await changed.dispatch('host'),'scan_more');await h.rawRpc('update_task',{...binding,task_id:'a',execution_status:'running',assignment:main});assert.equal(await changed.dispatch('host'),'queue_changed');assert.equal(calls.length,2,'a changed version invalidates the instance continuation before dispatch');
 console.log('Parallel scan: real backend continues across 20 instances and respects stop before dispatch.');
}finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
