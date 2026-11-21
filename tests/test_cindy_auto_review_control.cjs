'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.resolve('plugins/task-progress/cindy/main.js'),'utf8');
const host='host',sid='sid',iid='iid',sent=[],calls=[];let key='b'.repeat(64);
let receive,bus,ready=false,kv={taskProgressActiveHostSessionId:host,taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}}};
const sandbox={BroadcastChannel:class{constructor(){bus=this}postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout,clearTimeout,
 fetch:async(_url,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(fn){receive=fn},send:async msg=>sent.push(msg),agent:{run:async args=>{calls.push(args);return {ok:true,sessionId:host,mode:'continue',disposition:'queued'}}},node:{request:async req=>{
  if(req.params.name==='cindy_review_collect_session'){const settled=req.params.arguments.settled||[];return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:3,items:!ready||settled.includes(key)?[]:[{key,kind:'submission_review',task_id:'work',attempt_id:'attempt',actionable:true}]}}};}
  if(req.params.name==='cindy_review_reconcile'){const observed=ready&&req.params.arguments.observe_keys.includes(key)?[key]:[];return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,epoch:0,state_revision:3,observation_complete:true,observed_keys:observed}}};}
  return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,acknowledged:true}}};
 }}}};
vm.runInNewContext(source,sandbox);
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 await receive({type:'tool-call',callId:'config',tool:'configure_auto_review',args:{session_context:{session_id:host}}});
 assert(sent.some(x=>x.type==='card-update'&&x.html.includes('auto-review-enable')));
 assert.equal(kv.taskProgressAutoReview,undefined,'showing a card is not consent');
 await receive({type:'event',name:'card-action',callId:'config',actionId:'auto-review-enable',sessionId:host});
 assert.equal(kv.taskProgressAutoReview,undefined,'missing click token cannot enable');
 await receive({type:'event',name:'card-action',callId:'config',actionId:'auto-review-enable',sessionId:host,userActionToken:'trusted-host-token'});
 assert.equal(kv.taskProgressAutoReview[host].enabled,true);await delay(400);
 assert.equal(calls.length,0,'enabling without a due review does not send a turn');
 await receive({type:'event',name:'did-message-created',seq:50,dropped:2,data:{sessionId:host,role:'user'}});
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'dropped events suspend dispatch');
 let receiveAfterRestart;
 const gapRestarted={...sandbox,BroadcastChannel:class{postMessage(){}},cindy:{...sandbox.cindy,onHostMessage(fn){receiveAfterRestart=fn}}};
 vm.runInNewContext(source+'\nglobalThis.dispatchAfterGapRestart=dispatchDueReviews;',gapRestarted);
 await receiveAfterRestart({type:'event',name:'did-task-state-changed',data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 assert.equal(await gapRestarted.dispatchAfterGapRestart(host),'host_suspended','unsequenced activity after restart cannot clear a recorded gap');
 await receiveAfterRestart({type:'event',name:'did-task-state-changed',seq:49,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 assert.equal(await gapRestarted.dispatchAfterGapRestart(host),'host_suspended','an older numbered replay after restart cannot clear a recorded gap');
 await receiveAfterRestart({type:'event',name:'did-task-state-changed',seq:50,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 assert.equal(await gapRestarted.dispatchAfterGapRestart(host),'host_suspended','the gap event itself cannot be replayed as a resume');
 await receive({type:'event',name:'did-task-state-changed',seq:51,dropped:1,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'the gap-bearing event alone cannot resume');
 await receive({type:'event',name:'did-task-state-changed',seq:49,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'older replay cannot undo a newer event gap');
 await receive({type:'event',name:'did-task-state-changed',data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});await delay(100);
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'unsequenced activity cannot clear an event gap');
 ready=true;
 await Promise.all([
   receive({type:'event',name:'did-task-state-changed',seq:52,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}}),
   receive({type:'event',name:'did-task-state-changed',seq:53,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'requested'}}}})
 ]);await delay(200);
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'newer stop wins over an earlier asynchronous resume');assert.equal(calls.length,0);
 ready=false;
 await receive({type:'event',name:'did-task-state-changed',seq:54,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});await delay(200);
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,false,'a later complete active event resumes');
 ready=true;
 await receive({type:'event',name:'did-task-state-changed',data:{sessionId:host,state:{activity:{phase:'running',currentTurnActive:true,recordStatus:'active',gracefulStopState:'requested'}}}});
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'stop is persisted before later mutations');
 const restarted={...sandbox,BroadcastChannel:class{postMessage(){}},cindy:{...sandbox.cindy,onHostMessage(){}}};
 vm.runInNewContext(source+'\nglobalThis.dispatchAfterRestart=dispatchDueReviews;',restarted);
 assert.equal(await restarted.dispatchAfterRestart(host),'host_suspended','plugin restart cannot erase a recorded stop');
 await receive({type:'tool-call',callId:'mutation',tool:'update_task',args:{session_id:sid,task_instance_id:iid,session_context:{session_id:host}}});await delay(400);
 assert.equal(calls.length,0,'a requested stop suppresses automatic follow-up');
 await receive({type:'tool-call',callId:'suspended-status',tool:'get_auto_review_status',args:{session_context:{session_id:host}}});
 assert.equal(sent.find(x=>x.callId==='suspended-status'&&x.type==='tool-result').result.structuredContent.host_suspended,true);
 await receive({type:'event',name:'did-task-state-changed',data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});await delay(400);
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,false,'only an active host turn clears the persisted stop');
 assert.equal(calls.length,1);assert.equal(calls[0].sessionId,host);assert.equal(kv.taskProgressAutoReview[host].ledger[key].state,'dispatched');
 await receive({type:'tool-call',callId:'status',tool:'get_auto_review_status',args:{session_context:{session_id:host}}});
 const status=sent.find(x=>x.callId==='status'&&x.type==='tool-result').result.structuredContent;
 assert.equal(status.dispatched,1);assert.equal(status.uncertain,0);assert.equal(status.enabled,true);
 await receive({type:'tool-call',callId:'mutation-again',tool:'update_task',args:{session_id:sid,task_instance_id:iid,session_context:{session_id:host}}});await delay(400);
 assert.equal(calls.length,1,'repeated trigger does not dispatch the same review twice');
 key='c'.repeat(64);
 const panelRequest={type:'panel-tool-request',requestId:'panel-mutation',name:'review_task',arguments:{session_id:sid,task_instance_id:iid}};
 await Promise.all([bus.onmessage({data:panelRequest}),bus.onmessage({data:panelRequest})]);await delay(400);
 assert.equal(calls.length,2,'panel mutation triggers one check despite duplicate delivery');
 assert.equal(kv.taskProgressAutoReview[host].ledger[key].state,'dispatched');
 await receive({type:'event',name:'card-action',callId:'config',actionId:'auto-review-disable',sessionId:host,userActionToken:'another-token'});
 assert.equal(kv.taskProgressAutoReview[host].enabled,false);
 await receive({type:'tool-call',callId:'mutation2',tool:'update_task',args:{session_id:sid,task_instance_id:iid,session_context:{session_id:host}}});await delay(300);
 assert.equal(calls.length,2,'disabled session never dispatches');
 console.log('Auto-review control: explicit click, session binding, one dispatch, mutation deduplication, status and opt-out passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
