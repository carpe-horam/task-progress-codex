'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid',events=[];let receive,release,started,first=true,reads=0,dispatches=0;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const pending=new Promise(resolve=>started=resolve);
const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms===12500?20:ms);if(ms>=1000)t.unref();return t;},clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);else {reads++;if(first){first=false;started();await new Promise(resolve=>release=resolve);throw Error('late storage failure');}}return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async()=>{throw Error('disabled session must not query queue')}},agent:{run:async()=>{dispatches++;throw Error('disabled session must not dispatch')}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.watch=scheduleReviewWatchdog;',sandbox);
(async()=>{
 const keepAlive=setTimeout(()=>{throw Error('watchdog race test timed out');},2000);
 sandbox.watch(host,true,1);await pending;
 await receive({type:'event',name:'card-action',callId:'disable',actionId:'auto-review-disable',sessionId:host,userActionToken:'trusted'});
 release();await new Promise(resolve=>setTimeout(resolve,100));
 assert.equal(kv.taskProgressAutoReview[host].enabled,false);assert(events.some(e=>e.status==='disabled'));assert(!events.some(e=>e.status==='recovery_retry'||e.status==='recovery_unavailable'),'late storage failures cannot re-create warnings or retries after opt-out');assert.equal(dispatches,0);assert.equal(reads,2);
 clearTimeout(keepAlive);console.log('Watchdog opt-out race: a late KV failure cannot restart or warn after the user disables follow-up.');
})().catch(e=>{console.error(e);process.exitCode=1});
