'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const host='host',sid='sid',iid='iid',sent=[],events=[];let receive,agentCalls=0,holdRecovery=false,recoveryStarted,releaseRecovery;
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout,clearTimeout,
 fetch:async(_url,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(fn){receive=fn},send:async msg=>sent.push(msg),agent:{run:async()=>{agentCalls++;throw Error('unexpected model call')}},node:{request:async req=>{if(holdRecovery && req.params.name==='cindy_review_reconcile'){holdRecovery=false;recoveryStarted();await new Promise(resolve=>releaseRecovery=resolve);return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,observed_keys:['a'.repeat(64)],observation_complete:true}}};}return {ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}};}}}};
vm.runInNewContext(fs.readFileSync(path.resolve('plugins/task-progress/cindy/main.js'),'utf8'),sandbox);
(async()=>{
 await receive({type:'event',name:'did-session-switched',data:{sessionId:host}});
 assert(events.some(e=>e.type==='auto-review-state'&&e.host_session_id===host&&e.status==='association_unverified'));
 await receive({type:'tool-call',callId:'before',tool:'get_auto_review_status',args:{session_context:{session_id:host}}});
 assert.equal(sent.find(x=>x.callId==='before').result.structuredContent.host_association_verified,false);
 await receive({type:'event',name:'card-action',callId:'card',actionId:'auto-review-enable',sessionId:host,userActionToken:'trusted-click'});
 assert(events.some(e=>e.status==='association_verified'));await new Promise(resolve=>setTimeout(resolve,250));
 await receive({type:'tool-call',callId:'after',tool:'get_auto_review_status',args:{session_context:{session_id:host}}});
 assert.equal(sent.find(x=>x.callId==='after').result.structuredContent.host_association_verified,true);
 assert.equal(agentCalls,0,'reconfirming association with no due work must not create a model turn');
 kv.taskProgressAutoReview[host].enabled=false;kv.taskProgressAutoReview[host].last_error='CONSENT_REQUIRED';events.length=0;
 await receive({type:'event',name:'did-session-switched',data:{sessionId:host}});
 assert(events.some(e=>e.status==='consent_required'),'a denied authorization remains visible after the session is reopened');
 await receive({type:'event',name:'card-action',callId:'disable',actionId:'auto-review-disable',sessionId:host,userActionToken:'trusted-click'});
 assert.equal(kv.taskProgressAutoReview[host].last_error,null);assert(events.some(e=>e.status==='disabled'),'explicit opt-out clears the warning');
 events.length=0;await receive({type:'event',name:'did-session-switched',data:{sessionId:host}});assert(!events.some(e=>e.status==='consent_required'),'explicit opt-out does not re-create a consent warning');
 kv.taskProgressAutoReview[host].enabled=true;kv.taskProgressAutoReview[host].ledger={['a'.repeat(64)]:{state:'uncertain',session_id:sid,task_instance_id:iid}};
 events.length=0;const recoveryReady=new Promise(resolve=>recoveryStarted=resolve);holdRecovery=true;
 await receive({type:'event',name:'did-session-switched',data:{sessionId:host}});await recoveryReady;
 await receive({type:'event',name:'card-action',callId:'disable-race',actionId:'auto-review-disable',sessionId:host,userActionToken:'trusted-click'});
 releaseRecovery();await new Promise(resolve=>setTimeout(resolve,50));assert(events.some(e=>e.status==='disabled'));assert(!events.some(e=>e.status==='recovery_required'),'late recovery cannot restore a warning after opt-out');assert.equal(kv.taskProgressAutoReview[host].ledger['a'.repeat(64)].state,'uncertain','opt-out preserves uncertain delivery evidence');
 kv.taskProgressAutoReview[host].enabled=true;kv.taskProgressAutoReview[host].host_suspended=false;events.length=0;
 const secondRecoveryReady=new Promise(resolve=>recoveryStarted=resolve);holdRecovery=true;await receive({type:'event',name:'did-session-switched',data:{sessionId:host}});await secondRecoveryReady;
 await receive({type:'event',name:'did-task-state-changed',seq:10,data:{sessionId:host,state:{activity:{gracefulStopState:'requested'}}}});
 releaseRecovery();await new Promise(resolve=>setTimeout(resolve,50));assert.equal(kv.taskProgressAutoReview[host].host_suspended,true);assert(events.some(e=>e.status==='host_suspended'));assert(!events.some(e=>e.status==='recovery_required'),'late recovery cannot override a newer stop warning');
 console.log('Restart: persisted opt-in does not imply live host association; user action restores it without an unnecessary turn.');
})().catch(error=>{console.error(error);process.exitCode=1});
