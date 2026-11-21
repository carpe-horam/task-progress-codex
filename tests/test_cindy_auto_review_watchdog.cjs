'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const host='host',sid='sid',iid='iid',calls=[],events=[];
let clock=1000,kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const due=Array.from({length:26},(_,i)=>({key:(i+1).toString(16).padStart(64,'0'),kind:'submission_review',task_id:'task_'+i,attempt_id:'attempt_'+i,actionable:true}));
class FakeDate extends Date { static now(){return clock} }
const timer=(fn,ms)=>setTimeout(()=>{if(ms===60000)clock+=60001;fn();},ms===60000?20:5);
const sandbox={BroadcastChannel:class{postMessage(value){events.push(value)}},Date:FakeDate,Promise,Map,Set,Object,JSON,Error,setTimeout:timer,clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},agent:{run:async args=>{calls.push(args);return {ok:true,mode:'continue',sessionId:host,disposition:'queued'}}},node:{request:async req=>{
  const a=req.params.arguments;
  if(req.params.name==='cindy_review_collect_session'){const fresh=due.filter(item=>!a.settled.includes(item.key));return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:fresh.slice(0,25),remaining:Math.max(0,fresh.length-25)}}};}
  if(req.params.name==='cindy_review_reconcile')return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,epoch:0,state_revision:1,observation_complete:true,observed_keys:a.observe_keys.filter(key=>due.some(item=>item.key===key))}}};
  throw Error('unexpected RPC');
 }}}};
vm.runInNewContext(fs.readFileSync(path.resolve('plugins/task-progress/cindy/main.js'),'utf8')+'\nglobalThis.start=scheduleDueReviews;',sandbox);
(async()=>{
 sandbox.start(host);await new Promise(resolve=>setTimeout(resolve,350));
 assert.equal(calls.length,2,'one stalled batch does not starve distinct work, two stalled batches cap further dispatch');
 assert.match(calls[0].userMessage,/任务 task_0，/);assert.doesNotMatch(calls[0].userMessage,/任务 task_25，/);
 assert.match(calls[1].userMessage,/任务 task_25，/);assert.doesNotMatch(calls[1].userMessage,/任务 task_0，/);
 assert.equal(Object.values(kv.taskProgressAutoReview[host].ledger).filter(x=>x.state==='review_pending').length,26);
 assert(events.some(x=>x.status==='review_pending'));assert(events.some(x=>x.status==='dispatched_with_pending'));
 console.log('Watchdog: two bounded batches, no duplicate dispatch and unresolved-work feedback passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
