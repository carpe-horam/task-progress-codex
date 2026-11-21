'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid',key='d'.repeat(64);let receive,ready=false,dispatches=0;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}}};
const sandbox={BroadcastChannel:class{postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const timer=setTimeout(fn,ms===60000?60:ms===100?5:ms);if(ms>=1000)timer.unref();return timer;},clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async req=>{
  const a=req.params.arguments;if(req.params.name==='cindy_review_collect_session')return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:ready&&!a.settled.includes(key)?[{key,kind:'submission_review',task_id:'work',attempt_id:'attempt'}]:[]}}};
  if(req.params.name==='cindy_review_reconcile')return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,epoch:0,state_revision:1,observation_complete:true,observed_keys:ready?[key]:[]}}};
  throw Error('unexpected request');}},agent:{run:async()=>{dispatches++;return {ok:true,mode:'continue',sessionId:host}}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8'),sandbox);
(async()=>{
 await receive({type:'event',name:'card-action',callId:'enable',actionId:'auto-review-enable',sessionId:host,userActionToken:'trusted'});
 await new Promise(resolve=>setTimeout(resolve,25));assert.equal(dispatches,0);
 ready=true;const deadline=Date.now()+1000;while(!dispatches && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(dispatches,1,'a background-only state change is discovered without a new tool call');
 await new Promise(resolve=>setTimeout(resolve,150));assert.equal(dispatches,1,'periodic reads do not repeat a dispatched item');
 await receive({type:'event',name:'card-action',callId:'disable',actionId:'auto-review-disable',sessionId:host,userActionToken:'trusted'});
 await new Promise(resolve=>setTimeout(resolve,150));assert.equal(dispatches,1,'opt-out stops periodic follow-up');
 console.log('Idle watchdog: background changes are discovered without duplicate dispatch, and opt-out stops polling.');
})().catch(e=>{console.error(e);process.exitCode=1});
