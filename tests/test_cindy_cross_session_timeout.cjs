'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const hosts=['a','b'],sessions=['sa','sb'],instances=['ia','ib'],keys=['a'.repeat(64),'b'.repeat(64)],calls=[];
let clockAdvance=0,releaseFirst,firstStarted;const started=new Promise(resolve=>firstStarted=resolve),pending=new Promise(resolve=>releaseFirst=resolve);let hostBusy=false;
class TestDate extends Date {static now(){return Date.now()+clockAdvance;}}
let kv={taskProgressSessionsByHostSession:Object.fromEntries(hosts.map((h,i)=>[h,sessions[i]])),taskProgressContextsByHostSession:Object.fromEntries(hosts.map((h,i)=>[h,{session_id:sessions[i],task_instance_id:instances[i]}])),taskProgressAutoReview:Object.fromEntries(hosts.map((h,i)=>[h,{enabled:true,session_id:sessions[i],ledger:{}}]))};
const sandbox={BroadcastChannel:class{postMessage(){}},Date:TestDate,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>setTimeout(fn,ms===15000?20:ms),clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},node:{request:async req=>{const a=req.params.arguments,i=sessions.indexOf(a.session_id),base={session_id:sessions[i],task_instance_id:instances[i],epoch:0,state_revision:1};if(req.params.name==='cindy_review_collect_session')return {ok:true,result:{structuredContent:{...base,foreground_task_instance_id:instances[i],items:[{key:keys[i],kind:'submission_review',task_id:'work',attempt_id:'attempt'}]}}};if(req.params.name==='cindy_review_reconcile')return {ok:true,result:{structuredContent:{...base,observed_keys:[keys[i]]}}};throw Error('unexpected request');}},
 agent:{run:async args=>{calls.push(args);if(hostBusy)return {ok:false,errorCode:'RATE_LIMITED'};if(args.sessionId===hosts[0]){hostBusy=true;firstStarted();await pending;hostBusy=false;}return {ok:true,mode:'continue',sessionId:args.sessionId};}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.dispatch=dispatchDueReviews;',sandbox);
(async()=>{
 const first=sandbox.dispatch(hosts[0]);await started;assert.equal(await sandbox.dispatch(hosts[1]),'host_busy');assert.equal(await first,'uncertain');assert.equal(kv.taskProgressAutoReview[hosts[0]].ledger[keys[0]].state,'uncertain');
 clockAdvance=11000;assert.equal(await sandbox.dispatch(hosts[1]),'rate_limited','the host rejects a second call while the timed-out call is still running');assert.equal(kv.taskProgressAutoReview[hosts[1]].reservation,null);assert.equal(Object.keys(kv.taskProgressAutoReview[hosts[1]].ledger).length,0);
 releaseFirst();await new Promise(resolve=>setTimeout(resolve,10));clockAdvance=23001;assert.equal(await sandbox.dispatch(hosts[1]),'dispatched');assert.equal(await sandbox.dispatch(hosts[0]),'recovery_required');assert.equal(calls.filter(x=>x.sessionId===hosts[0]).length,1,'late success never causes the uncertain call to be sent twice');
 console.log('Cross-session timeout: uncertain delivery stays protected while another session safely retries after host contention.');
})().catch(e=>{console.error(e);process.exitCode=1});
