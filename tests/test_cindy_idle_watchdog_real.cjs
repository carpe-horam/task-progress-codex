'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createHost}=require('./browser_host.cjs');
const {createReviewQueueHandler}=require('../plugins/task-progress/cindy/node/review-queue.cjs');
(async()=>{const h=await createHost();try{
 const main={execution_mode:'main_session',self_execution_reason:'隔离巡检测试'},sub={execution_mode:'subagent',tool_identifier:'fixture.spawn',dispatch_text:'生成隔离结果'};
 const init=await h.rawRpc('init_task_network',{task_title:'后台状态变化',tasks:[{id:'work',title:'待复核',assignment:sub},{id:'other',title:'其他工作',assignment:main}]});
 const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
 const run=await h.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',assignment:sub}),attempt=run.structuredContent.attempt_id;
 await h.rawRpc('bind_task_agent',{...binding,task_id:'work',attempt_id:attempt,agent_name:'fixture'});
 let receive,kv={taskProgressSessionsByHostSession:{host:binding.session_id},taskProgressContextsByHostSession:{host:binding}},events=[],calls=[];
 const handler=createReviewQueueHandler(async req=>({result:await h.rawRpc(req.params.name,req.params.arguments)}));
 const sandbox={BroadcastChannel:class{postMessage(event){events.push(event)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms===60000?100:ms===100?5:ms);if(ms>=1000)timer.unref();return timer;},clearTimeout,fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async req=>{const r=await handler({jsonrpc:'2.0',id:1,...req});return r.error?{ok:false,message:r.error.message}:{ok:true,result:r.result}}},agent:{run:async args=>{calls.push(args);return {ok:true,mode:'continue',sessionId:'host'}}}}};
 vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8'),sandbox);
 await receive({type:'event',name:'card-action',callId:'enable',actionId:'auto-review-enable',sessionId:'host',userActionToken:'trusted'});
 let deadline=Date.now()+5000;while(!events.some(e=>e.status==='empty') && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));assert(events.some(e=>e.status==='empty'));assert.equal(calls.length,0);
 // Simulate the supervised worker writing directly to the backend, without
 // any plugin tool call or synthetic host mutation event.
 await h.rawRpc('update_task',{...binding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'后台结果',evidence:['fixture://result']});
 deadline=Date.now()+5000;while(!calls.length && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(calls.length,1);assert.equal(calls[0].event.task_instance_id,binding.task_instance_id);assert.match(calls[0].userMessage,/work/);
 await new Promise(resolve=>setTimeout(resolve,250));assert.equal(calls.length,1,'periodic polling never fabricates or repeats acceptance');
 console.log('Idle watchdog real backend: direct external state changes trigger one independently reviewed follow-up.');
}finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
