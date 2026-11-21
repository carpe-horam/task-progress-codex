'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const host='host',sid='sid',iid='iid',events=[];let release,started,agentCalls=0;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const startedPromise=new Promise(resolve=>started=resolve),gate=new Promise(resolve=>release=resolve);
const sandbox={BroadcastChannel:class{postMessage(value){events.push(value)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout,clearTimeout,
 fetch:async(_url,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},agent:{run:async()=>{agentCalls++;return {ok:true,sessionId:host,mode:'continue'}}},node:{request:async req=>{
  assert.equal(req.params.name,'cindy_review_collect_session');started();await gate;
  return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}};
 }}}};
vm.runInNewContext(fs.readFileSync(path.resolve('plugins/task-progress/cindy/main.js'),'utf8')+'\nglobalThis.start=scheduleDueReviews;globalThis.stop=observeReviewHostActivity;',sandbox);
(async()=>{
 sandbox.start(host);await startedPromise;
 sandbox.stop({data:{sessionId:host,state:{activity:{phase:'running',gracefulStopState:'requested'}}}});
 await new Promise(resolve=>setTimeout(resolve,20));release();await new Promise(resolve=>setTimeout(resolve,200));
 assert.equal(agentCalls,0);assert.equal(kv.taskProgressAutoReview[host].host_suspended,true);
 assert(events.some(e=>e.status==='host_suspended'));
 assert(!events.some(e=>e.status==='empty'),'an older empty read cannot clear a newer stop warning');
 console.log('Stop race: a delayed empty queue cannot hide a newer user-stop warning or dispatch a model turn.');
})().catch(error=>{console.error(error);process.exitCode=1});
