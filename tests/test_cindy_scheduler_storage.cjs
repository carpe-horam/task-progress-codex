'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid',events=[];let reads=0,queueReads=0,dispatches=0,permanent=false;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms===12500?20:ms===100?5:ms);if(ms>=1000)t.unref();return t;},clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);else if(++reads<=3||permanent)throw Error('KV failure');return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},node:{request:async req=>{assert.equal(req.params.name,'cindy_review_collect_session');queueReads++;return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}};}},agent:{run:async()=>{dispatches++;throw Error('no due work')}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.schedule=scheduleDueReviews;',sandbox);
(async()=>{
 sandbox.schedule(host,1);const deadline=Date.now()+1000;while(!queueReads && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(queueReads,1,'a failed scheduler KV read resumes through safe recovery polling');assert(events.some(e=>e.status==='recovery_retry'));assert(events.some(e=>e.status==='recovered'));assert.equal(dispatches,0);
 events.length=0;reads=0;permanent=true;sandbox.schedule(host,1);await new Promise(resolve=>setTimeout(resolve,180));
 assert.equal(reads,7,'persistent scheduler and recovery KV failures stop after the bounded retry budget');assert(events.some(e=>e.status==='recovery_unavailable'));assert.equal(queueReads,1);assert.equal(dispatches,0);
 kv.taskProgressAutoReview[host].enabled=false;
 console.log('Scheduler storage: failed identity reads recover safely without dispatching.');
})().catch(e=>{console.error(e);process.exitCode=1});
