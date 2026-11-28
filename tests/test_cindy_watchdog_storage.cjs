'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid';
function harness({failures}){let reads=0,queueReads=0,dispatches=0,events=[],reviewPhase=false;
 let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
 const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms===12500?15:ms===100?5:ms);if(ms>=1000)t.unref();return t;},clearTimeout,
  fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);else if(reviewPhase&&++reads<=failures)throw Error('temporary storage failure');return {ok:true,json:async()=>structuredClone(kv)}},
  cindy:{onHostMessage(){},send:async()=>{},node:{request:async req=>{assert.equal(req.params.name,'cindy_review_collect_session');queueReads++;return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}};}},agent:{run:async()=>{dispatches++;throw Error('no due work')}}}};
 vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.watch=scheduleReviewWatchdog;globalThis.testStartup=()=>hostArchiveWorkQueue;',sandbox);
 return {start:async()=>{await sandbox.testStartup();reviewPhase=true;sandbox.watch(host,true,1);},disable:()=>{kv.taskProgressAutoReview[host].enabled=false},get:()=>({reads,queueReads,dispatches,events})};}
(async()=>{
 const transient=harness({failures:2});await transient.start();await new Promise(resolve=>setTimeout(resolve,160));
 assert.equal(transient.get().queueReads,1,'polling resumes after temporary KV failures');assert.equal(transient.get().dispatches,0);assert(transient.get().events.some(e=>e.status==='recovery_retry'));
 const persistent=harness({failures:100});await persistent.start();await new Promise(resolve=>setTimeout(resolve,160));
 assert.equal(persistent.get().reads,4,'persistent KV failure stops after three retries');assert.equal(persistent.get().queueReads,0);assert(persistent.get().events.some(e=>e.status==='recovery_unavailable'));
 const disabled=harness({failures:1});await disabled.start();await new Promise(resolve=>setTimeout(resolve,5));disabled.disable();await new Promise(resolve=>setTimeout(resolve,100));
 assert.equal(disabled.get().queueReads,0,'a disabled session is not polled after storage recovers');assert.equal(disabled.get().dispatches,0);
 console.log('Watchdog storage: transient failures retry, persistent failures stop visibly, and opt-out prevents later scanning.');
})().catch(e=>{console.error(e);process.exitCode=1});
