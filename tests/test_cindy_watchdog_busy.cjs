'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid';let reads=0,dispatches=0;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const sandbox={BroadcastChannel:class{postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms===12500?30:ms===100?5:ms);if(ms>=1000)t.unref();return t;},clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},node:{request:async req=>{assert.equal(req.params.name,'cindy_review_collect_session');reads++;return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}};}},agent:{run:async()=>{dispatches++;throw Error('no due work')}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.watch=scheduleReviewWatchdog;globalThis.inFlight=reviewDispatchInFlight;',sandbox);
(async()=>{
 sandbox.inFlight.add(host);sandbox.watch(host,true,1);await new Promise(resolve=>setTimeout(resolve,15));assert.equal(reads,0);
 sandbox.inFlight.delete(host);const deadline=Date.now()+1000;while(!reads && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(reads,1,'a busy watchdog retries and resumes scanning after the in-flight request');assert.equal(dispatches,0);
 kv.taskProgressAutoReview[host].enabled=false;
 console.log('Watchdog busy race: a transient in-flight scan cannot silently stop future checks.');
})().catch(e=>{console.error(e);process.exitCode=1});
