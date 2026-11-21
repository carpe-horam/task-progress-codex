'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid',key='a'.repeat(64),source=fs.readFileSync('plugins/task-progress/cindy/main.js','utf8');
async function exercise(tool,isError=false){let receive,dispatches=0;
 let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
 const sandbox={BroadcastChannel:class{postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms===100?5:ms);if(ms>=1000)t.unref();return t;},clearTimeout,
  fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
  cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async req=>{
   if(req.params.name==='cindy_review_collect_session')return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[{key,kind:'submission_review',task_id:'work',attempt_id:'attempt'}]}}};
   if(req.params.name==='cindy_review_reconcile')return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,epoch:0,state_revision:1,observed_keys:[key]}}};
   return {ok:true,result:isError?{isError:true,content:[{type:'text',text:'rejected'}]}:{structuredContent:{session_id:sid,task_instance_id:iid}}};}},
  agent:{run:async()=>{dispatches++;return {ok:true,mode:'continue',sessionId:host}}}}};
 vm.runInNewContext(source,sandbox);await receive({type:'tool-call',callId:'mutation',tool,args:{session_id:sid,task_instance_id:iid,session_context:{session_id:host}}});
 const deadline=Date.now()+500;while(!dispatches && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));return dispatches;}
(async()=>{
 for(const tool of ['record_task_execution','start_task_execution','cancel_request','activate_request','record_coverage','revise_task_plan','delete_task'])assert.equal(await exercise(tool),1,`${tool} schedules a review check`);
 assert.equal(await exercise('record_task_execution',true),0,'a rejected mutation never schedules a follow-up');
 console.log('Mutation triggers: execution receipts and task changes schedule checks; rejected mutations do not.');
})().catch(e=>{console.error(e);process.exitCode=1});
