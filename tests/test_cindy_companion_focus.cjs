'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let focused='desktop',available=true,events=[],kv={taskProgressActiveHostSessionId:'desktop',taskProgressSessionsByHostSession:{desktop:'task-a',bot:'task-b'}};
const sandbox={BroadcastChannel:class{postMessage(e){events.push(e)}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout,clearTimeout,
 fetch:async(_url,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},
 cindy:{onHostMessage(){},send:async()=>{},taskView:async request=>{assert.equal(request.peek,true);return available?(focused?{ok:true,sessionId:focused}:{ok:false,errorCode:'HOST_NOT_READY'}):{ok:false,errorCode:'PERMISSION_DENIED'};}}};
vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.sync=syncPanelFocus;',sandbox);
(async()=>{
 await sandbox.sync();assert.equal(events.length,0,'unchanged focus does not force redundant rendering');
 focused='bot';await sandbox.sync();assert.equal(kv.taskProgressActiveHostSessionId,'bot');assert.equal(events.at(-1).session_id,'task-b','companion uses its own mapping without an event');
 focused='unmapped-bot';await sandbox.sync();assert.equal(kv.taskProgressActiveHostSessionId,'unmapped-bot');assert.equal(events.at(-1).session_id,null,'an unmapped companion never displays the previous task');
 focused='desktop';await sandbox.sync();assert.equal(events.at(-1).session_id,'task-a','returning restores the original session');
 focused='';await sandbox.sync();assert.equal(kv.taskProgressActiveHostSessionId,'');assert.equal(events.at(-1).session_id,null,'no foreground clears the binding');
 focused='desktop';await sandbox.sync();assert.equal(kv.taskProgressActiveHostSessionId,'desktop');
 available=false;focused='bot';await sandbox.sync();assert.equal(kv.taskProgressActiveHostSessionId,'','an unavailable focus service hides the previously bound task');assert.equal(events.at(-1).session_id,null);
 console.log('Companion focus: mapped, unmapped, return and unavailable foreground are isolated.');
})().catch(e=>{console.error(e);process.exitCode=1});
