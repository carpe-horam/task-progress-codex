'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createHost}=require('./browser_host.cjs');
const {createReviewQueueHandler}=require('../plugins/task-progress/cindy/node/review-queue.cjs');
(async()=>{const h=await createHost();try{
 const main={execution_mode:'main_session',self_execution_reason:'隔离恢复测试'},sub={execution_mode:'subagent',tool_identifier:'fixture.spawn',dispatch_text:'提交隔离结果'};
 const init=await h.rawRpc('init_task_network',{task_title:'恢复待验收',tasks:[{id:'work',title:'待复核',assignment:sub},{id:'other',title:'其他工作',assignment:main}]});
 const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
 const run=await h.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',assignment:sub}),attempt=run.structuredContent.attempt_id;
 await h.rawRpc('bind_task_agent',{...binding,task_id:'work',attempt_id:attempt,agent_name:'fixture'});
 await h.rawRpc('update_task',{...binding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'结果',evidence:['fixture://result']});
 const handler=createReviewQueueHandler(async req=>({result:await h.rawRpc(req.params.name,req.params.arguments)}));
 function harness(enabled){let receive,kv={taskProgressSessionsByHostSession:{host:binding.session_id},taskProgressContextsByHostSession:{host:binding},taskProgressAutoReview:{host:{enabled,session_id:binding.session_id,ledger:{}}}};const dispatches=[];
  const sandbox={BroadcastChannel:class{postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms);if(ms>=1000)t.unref();return t;},clearTimeout,fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async req=>{const r=await handler({jsonrpc:'2.0',id:1,...req});return r.error?{ok:false,message:r.error.message}:{ok:true,result:r.result}}},agent:{run:async args=>{dispatches.push(args);return {ok:true,mode:'continue',sessionId:'host'}}}}};
  vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8'),sandbox);return {receive,dispatches};}
 const resumed=harness(true);await resumed.receive({type:'event',name:'did-session-switched',data:{sessionId:'host'}});
 const deadline=Date.now()+5000;while(!resumed.dispatches.length && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,25));
 assert.equal(resumed.dispatches.length,1,'opening an enabled session scans existing work without a new mutation');assert.equal(resumed.dispatches[0].event.task_instance_id,binding.task_instance_id);
 await resumed.receive({type:'event',name:'did-session-switched',data:{sessionId:'host'}});await new Promise(resolve=>setTimeout(resolve,250));assert.equal(resumed.dispatches.length,1,'reopening cannot duplicate an outstanding follow-up');
 const active=harness(true),activeEvent={type:'event',name:'did-task-state-changed',seq:1,data:{sessionId:'host',state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}};
 await active.receive(activeEvent);let activeDeadline=Date.now()+5000;while(!active.dispatches.length && Date.now()<activeDeadline)await new Promise(resolve=>setTimeout(resolve,25));assert.equal(active.dispatches.length,1,'a confirmed active host event restarts scanning even without a session switch');
 await active.receive({...activeEvent,seq:2});await new Promise(resolve=>setTimeout(resolve,250));assert.equal(active.dispatches.length,1,'repeated active events cannot duplicate an outstanding follow-up');
 const disabled=harness(false);await disabled.receive({type:'event',name:'did-session-switched',data:{sessionId:'host'}});await new Promise(resolve=>setTimeout(resolve,250));assert.equal(disabled.dispatches.length,0,'disabled sessions never auto-dispatch');
 await disabled.receive(activeEvent);await new Promise(resolve=>setTimeout(resolve,250));assert.equal(disabled.dispatches.length,0,'active host events do not opt a disabled session in');
 console.log('Restart recovery: enabled sessions scan existing pending work once; disabled sessions remain silent.');
}finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
