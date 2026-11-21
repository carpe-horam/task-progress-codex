'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid',events=[],messages=[];let receive,dispatches=0;
class TestDate extends Date {static now(){return 10000;}}
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{},host_suspended:true,suspension_reason:'EVENT_GAP',suspension_event_seq:50}}};
const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date:TestDate,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms);if(ms>=1000)t.unref();return t;},clearTimeout,fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},cindy:{onHostMessage(fn){receive=fn},send:async msg=>messages.push(msg),node:{request:async()=>({ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}})},agent:{run:async()=>{dispatches++;throw Error('no due work')}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.dispatch=dispatchDueReviews;',sandbox);
const active=(seq,ts)=>({type:'event',name:'did-task-state-changed',seq,ts,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
(async()=>{
 await receive(active(1,9000));assert.equal(await sandbox.dispatch(host),'host_suspended','a reset sequence cannot automatically clear an old gap');
 await receive({type:'event',name:'card-action',callId:'reenable',actionId:'auto-review-enable',sessionId:host,userActionToken:'trusted'});
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,true,'explicit opt-in alone does not attest a running host');
 assert(messages.some(m=>m.callId==='reenable'&&m.html?.includes('等待会话恢复运行')));assert(events.some(e=>e.status==='event_gap'),'the panel retains the suspension warning after re-enabling');
 await receive(active(2,9999));assert.equal(await sandbox.dispatch(host),'host_suspended','activity from before re-enabling cannot clear the gap');
 await receive(active(3,10001));await new Promise(resolve=>setTimeout(resolve,150));assert.equal(kv.taskProgressAutoReview[host].host_suspended,false,'fresh host activity after explicit re-enabling can resume despite a reset sequence');assert.equal(dispatches,0);
 console.log('Host sequence reset: explicit re-enable plus later host activity resumes safely; old activity stays suspended.');
})().catch(e=>{console.error(e);process.exitCode=1});
