'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createHost}=require('./browser_host.cjs');
const {collectReviewQueue,createReviewQueueHandler}=require('../plugins/task-progress/cindy/node/review-queue.cjs');
(async()=>{const h=await createHost();try{
 const main={execution_mode:'main_session',self_execution_reason:'隔离验收测试'},sub={execution_mode:'subagent',tool_identifier:'fixture.spawn',dispatch_text:'生成隔离测试结果'};
 const init=await h.rawRpc('init_task_network',{task_title:'主任务',tasks:[{id:'work',title:'主任务结果',assignment:sub},{id:'other',title:'后续工作',assignment:main}]});
 const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
 await h.rawRpc('route_request',{...binding,request_id:'parallel',request_text:'并行工作',task_title:'并行工作',mode:'parallel',reason:'隔离实例',acceptance_criteria:['结果可验收']});
 const context=await h.rawRpc('get_progress',{...binding,view:'context'}),peer=context.structuredContent.progress.session_work.requests.find(r=>r.request_id==='parallel').task_instance_id;
 const peerBinding={session_id:binding.session_id,task_instance_id:peer};
 await h.rawRpc('decompose_task',{...peerBinding,task_id:'root',tasks:[{id:'peer-work',title:'并行结果',assignment:sub}]});
 async function submit(b,id){const start=await h.rawRpc('update_task',{...b,task_id:id,execution_status:'running',assignment:sub}),attempt=start.structuredContent.attempt_id;await h.rawRpc('bind_task_agent',{...b,task_id:id,attempt_id:attempt,agent_name:'fixture'});await h.rawRpc('update_task',{...b,task_id:id,attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'结果',evidence:['fixture://result']});return attempt;}
 const mainAttempt=await submit(binding,'work'),peerAttempt=await submit(peerBinding,'peer-work');
 const rpc=async req=>({result:await h.rawRpc(req.params.name,req.params.arguments)}),handler=createReviewQueueHandler(rpc);
 const mainItem=(await collectReviewQueue(rpc,binding)).items[0],peerItem=(await collectReviewQueue(rpc,peerBinding)).items[0];
 let kv={taskProgressSessionsByHostSession:{host:binding.session_id},taskProgressContextsByHostSession:{host:binding},taskProgressAutoReview:{host:{enabled:true,session_id:binding.session_id,ledger:{[mainItem.key]:{state:'no_longer_due',session_id:binding.session_id,task_instance_id:binding.task_instance_id}}}}};
 const dispatches=[];const sandbox={BroadcastChannel:class{postMessage(){}},Date,Promise,Map,Set,Object,JSON,Error,setTimeout,clearTimeout,fetch:async(_u,opt)=>{if(opt?.method==='PUT')kv=JSON.parse(opt.body);return {ok:true,json:async()=>structuredClone(kv)}},cindy:{onHostMessage(){},send:async()=>{},node:{request:async req=>{const result=await handler({jsonrpc:'2.0',id:1,...req});return result.error?{ok:false}:{ok:true,result:result.result}}},agent:{run:async args=>{dispatches.push(args);return {ok:true,mode:'continue',sessionId:'host',disposition:'queued'}}}}};
 vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8')+'\nglobalThis.recover=reconcileUncertainReviews;globalThis.dispatch=dispatchDueReviews;',sandbox);
 assert.equal(await sandbox.dispatch('host'),'dispatched');assert.equal(dispatches.length,1);assert.equal(dispatches[0].event.task_instance_id,peer);assert.equal(dispatches[0].event.foreground_task_instance_id,binding.task_instance_id);assert.equal(kv.taskProgressContextsByHostSession.host.task_instance_id,binding.task_instance_id);assert.equal(kv.taskProgressAutoReview.host.ledger[peerItem.key].task_instance_id,peer);
 kv.taskProgressAutoReview.host.ledger[peerItem.key].state='uncertain';kv.taskProgressAutoReview.host.ledger[mainItem.key].state='dispatched';kv.taskProgressAutoReview.host.ledger[mainItem.key].at=Date.now();
 assert.equal(await sandbox.recover('host',true),'recovery_required','pending peer submission remains uncertain');
 await h.rawRpc('review_task',{...peerBinding,task_id:'peer-work',attempt_id:peerAttempt,decision:'accept',summary:'已核验',evidence:['fixture://peer-review'],reviewer:main});
 await h.rawRpc('update_task',{...peerBinding,task_id:'root',execution_status:'finished',outcome:'resolved',assignment:main,summary:'并行工作已完成',evidence:['fixture://peer-root']});
 assert.equal(await sandbox.recover('host',true),'awaiting_completion');assert.equal(kv.taskProgressAutoReview.host.ledger[peerItem.key].state,'no_longer_due');assert.equal(kv.taskProgressAutoReview.host.ledger[mainItem.key].state,'dispatched');
 const mainQueue=await h.rawRpc('get_progress',{...binding,view:'review_queue'});assert(mainQueue.structuredContent.progress.records.some(x=>x.attempt_id===mainAttempt));assert.equal(dispatches.length,1,'recovery never repeats a host dispatch');
 console.log('Parallel recovery: real backend verifies peer ownership and clears only its reviewed attempt without touching the foreground.');
}finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
