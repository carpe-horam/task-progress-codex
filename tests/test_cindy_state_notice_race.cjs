'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const host='host',sid='sid',iid='iid',events=[];let receive,started,release,hold=true;
const pending=new Promise(resolve=>started=resolve);
let kv={taskProgressSessionsByHostSession:{[host]:sid},taskProgressContextsByHostSession:{[host]:{session_id:sid,task_instance_id:iid}},taskProgressAutoReview:{[host]:{enabled:true,session_id:sid,ledger:{}}}};
const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms);if(ms>=1000)t.unref();return t;},clearTimeout,
 fetch:async(_u,opt)=>{if(opt?.method==='PUT'){if(hold){hold=false;started();await new Promise(resolve=>release=resolve);}kv=JSON.parse(opt.body);}return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(fn){receive=fn},send:async()=>{},node:{request:async()=>({ok:true,result:{structuredContent:{session_id:sid,task_instance_id:iid,foreground_task_instance_id:iid,epoch:0,state_revision:1,items:[]}}})},agent:{run:async()=>{throw Error('no pending work')}}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8'),sandbox);
(async()=>{
 const stop=receive({type:'event',name:'did-task-state-changed',seq:1,data:{sessionId:host,state:{activity:{gracefulStopState:'requested'}}}});await pending;
 const active=receive({type:'event',name:'did-task-state-changed',seq:2,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 release();await Promise.all([stop,active]);await new Promise(resolve=>setTimeout(resolve,180));
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,false);assert(!events.some(e=>e.status==='host_suspended'),'a delayed stop notice cannot override a newer active event');
 events.length=0;hold=true;const gapPending=new Promise(resolve=>started=resolve);
 const gap=receive({type:'event',name:'did-message-created',seq:3,dropped:1,data:{sessionId:host,role:'user'}});await gapPending;
 const later=receive({type:'event',name:'did-task-state-changed',seq:4,data:{sessionId:host,state:{status:'active',activity:{phase:'running',currentTurnActive:true,gracefulStopState:'none'}}}});
 release();await Promise.all([gap,later]);await new Promise(resolve=>setTimeout(resolve,180));
 assert.equal(kv.taskProgressAutoReview[host].host_suspended,false);assert(!events.some(e=>e.status==='event_gap'),'a delayed gap notice cannot override newer confirmed activity');
 console.log('State notice race: delayed stop feedback cannot override newer confirmed activity.');
})().catch(e=>{console.error(e);process.exitCode=1});
