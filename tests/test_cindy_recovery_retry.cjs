'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const host='host',sid='sid',iid='iid',key='d'.repeat(64),events=[];let reads=0,dispatches=0,alwaysFail=false,disableOnRead=false;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{[key]:{state:'uncertain',session_id:sid,task_instance_id:iid}}}}};
const sandbox={BroadcastChannel:class{postMessage(value){events.push(value)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>setTimeout(fn,ms===12500?10:ms===100?5:ms),clearTimeout,
 fetch:async(_url,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},agent:{run:async()=>{dispatches++;return {ok:true,mode:'continue',sessionId:host}}},node:{request:async req=>{
  assert.equal(req.params.name,'cindy_review_reconcile');reads++;
  if(disableOnRead)kv.taskProgressAutoReview[host].enabled=false;
  if(alwaysFail||reads<3)return {ok:false,message:'temporary failure'};
  return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,observation_complete:true,observed_keys:[key]}}};
 }}}};
vm.runInNewContext(fs.readFileSync(path.resolve('plugins/task-progress/cindy/main.js'),'utf8')+'\nglobalThis.start=scheduleDueReviews;',sandbox);
(async()=>{
 sandbox.start(host);await new Promise(resolve=>setTimeout(resolve,200));
 assert.equal(reads,3,'temporary read failures trigger bounded read-only retries');
 assert.equal(dispatches,0,'uncertain delivery is never automatically resent');
 assert.equal(kv.taskProgressAutoReview[host].ledger[key].state,'uncertain');
 assert(events.some(x=>x.status==='recovery_retry'));assert(events.some(x=>x.status==='recovery_required'));
 reads=0;events.length=0;alwaysFail=true;sandbox.start(host);await new Promise(resolve=>setTimeout(resolve,200));
 assert.equal(reads,5,'persistent read failure stops after a bounded retry budget');
 assert.equal(dispatches,0);assert(events.some(x=>x.status==='recovery_unavailable'));
 reads=0;events.length=0;disableOnRead=true;kv.taskProgressAutoReview[host].enabled=true;sandbox.start(host);await new Promise(resolve=>setTimeout(resolve,200));
 assert.equal(reads,1,'opt-out during recovery prevents subsequent polling');assert.equal(dispatches,0);assert.equal(events.length,0);
 console.log('Recovery retry: transient reads retry, unresolved dispatch remains protected, and no model turn is resent.');
})().catch(e=>{console.error(e);process.exitCode=1});
