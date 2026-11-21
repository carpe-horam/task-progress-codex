'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const hosts=['host-a','host-b','host-c'],sessions=['session-a','session-b','session-c'],instances=['instance-a','instance-b','instance-c'],keys=['a'.repeat(64),'b'.repeat(64),'c'.repeat(64)],calls=[];
let started,release,receive,clockAdvance=0,denyHost=null;const firstStarted=new Promise(resolve=>started=resolve),gate=new Promise(resolve=>release=resolve);
class TestDate extends Date {static now(){return Date.now()+clockAdvance;}}
let kv={taskProgressSessionsByHostSession:Object.fromEntries(hosts.map((h,i)=>[h,sessions[i]])),taskProgressContextsByHostSession:Object.fromEntries(hosts.map((h,i)=>[h,{session_id:sessions[i],task_instance_id:instances[i]}])),taskProgressAutoReview:Object.fromEntries(hosts.map((h,i)=>[h,{enabled:true,session_id:sessions[i],ledger:{}}]))};
const sandbox={BroadcastChannel:class{postMessage(){}},Date:TestDate,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms===12500?30:ms);if(ms>=1000)t.unref();return t;},clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async req=>{const a=req.params.arguments,index=sessions.indexOf(a.session_id);assert(index>=0);const base={session_id:sessions[index],task_instance_id:instances[index],epoch:0,state_revision:1};
  if(req.params.name==='cindy_review_collect_session')return {ok:true,result:{structuredContent:{...base,foreground_task_instance_id:instances[index],items:[{key:keys[index],kind:'submission_review',task_id:'work',attempt_id:'attempt'}]}}};
  if(req.params.name==='cindy_review_reconcile')return {ok:true,result:{structuredContent:{...base,observed_keys:[keys[index]]}}};throw Error('unexpected request');}},
  agent:{run:async args=>{calls.push(args);if(calls.length===1){started();await gate;}if(args.sessionId===denyHost)return {ok:false,errorCode:'PERMISSION_DENIED'};return {ok:true,mode:'continue',sessionId:args.sessionId}}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.dispatch=dispatchDueReviews;globalThis.schedule=scheduleDueReviews;globalThis.waiters=reviewHostWaiters;globalThis.stop=observeReviewHostActivity;',sandbox);
(async()=>{
 const first=sandbox.dispatch(hosts[0]);await firstStarted;
 assert.equal(await sandbox.dispatch(hosts[1]),'host_busy');assert.equal(calls.length,1);assert.equal(kv.taskProgressAutoReview[hosts[1]].reservation,null);assert.equal(kv.taskProgressAutoReview[hosts[1]].retry_count,undefined,'local contention does not consume the host error retry budget');
 assert.equal(await sandbox.dispatch(hosts[2]),'host_busy');assert.deepEqual([...sandbox.waiters.keys()],hosts.slice(1));sandbox.schedule(hosts[1],1);sandbox.schedule(hosts[2],1);await new Promise(resolve=>setTimeout(resolve,15));assert.equal(calls.length,1);
 release();assert.equal(await first,'dispatched');assert.equal(await sandbox.dispatch(hosts[1]),'host_busy','a different session waits through the host cooldown');assert.equal(kv.taskProgressAutoReview[hosts[1]].retry_count,undefined);
 kv.taskProgressAutoReview[hosts[0]].ledger={};keys[0]='d'.repeat(64);assert.equal(await sandbox.dispatch(hosts[0]),'host_busy','the previous session cannot overtake waiting sessions');clockAdvance=11000;
 let deadline=Date.now()+1000;while(calls.length<2 && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(calls.length,2,'a deferred session is retried automatically after the cooldown');
 clockAdvance=22000;deadline=Date.now()+1000;while(calls.length<3 && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(calls.length,3,'both waiting sessions eventually run');assert.deepEqual(calls.slice(0,3).map(c=>c.sessionId),hosts,'retries preserve FIFO order');
 clockAdvance=33000;kv.taskProgressAutoReview[hosts[0]].ledger={};kv.taskProgressAutoReview[hosts[1]].ledger={};denyHost=hosts[0];assert.equal(await sandbox.dispatch(hosts[0]),'consent_required');assert.equal(await sandbox.dispatch(hosts[1]),'dispatched','a pre-run permission denial does not extend another session’s cooldown');
 sandbox.waiters.set(hosts[2],TestDate.now());sandbox.stop({data:{sessionId:hosts[2],state:{activity:{gracefulStopState:'requested'}}}});assert.equal(sandbox.waiters.has(hosts[2]),false,'stopped sessions leave the waiting queue immediately');
 sandbox.waiters.set(hosts[2],TestDate.now());await receive({type:'event',name:'card-action',callId:'disable',actionId:'auto-review-disable',sessionId:hosts[2],userActionToken:'trusted'});assert.equal(sandbox.waiters.has(hosts[2]),false,'explicitly disabled sessions leave the waiting queue');assert.equal(kv.taskProgressAutoReview[hosts[2]].enabled,false);
 sandbox.waiters.set('expired-session',TestDate.now()-61000);kv.taskProgressAutoReview[hosts[1]].ledger={};assert.equal(await sandbox.dispatch(hosts[1]),'dispatched','an expired waiter cannot block active sessions');
 console.log('Cross-session dispatch: one host call at a time, without losing the deferred session or consuming its retry budget.');
})().catch(e=>{console.error(e);process.exitCode=1});
