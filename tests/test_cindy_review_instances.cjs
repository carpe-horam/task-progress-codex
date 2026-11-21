'use strict';
const assert=require('node:assert/strict');
const {collectReviewInstances,collectSessionReviewQueue,createReviewQueueHandler}=require('../plugins/task-progress/cindy/node/review-queue.cjs');
const sid='session',iid='foreground',base={session_id:sid,task_instance_id:iid,epoch:2,state_revision:8};
function mock(rows,{changed=false,malformed=false}={}) {return async request=>{
 const a=request.params.arguments;
 if(a.view==='overview')return {result:{structuredContent:{...base,protocol_capabilities:['review_queue_v1'],progress:{task_instance_id:iid,schema_version:2}}}};
 const offset=a.offset||0,records=rows.slice(offset,offset+a.limit),next=offset+records.length;
 return {result:{structuredContent:{...base,...(changed&&offset?{state_revision:9}:{}),view:'context',progress:{session_work:{requests:records,pagination:{total:rows.length,has_more:next<rows.length,next_offset:malformed&&offset===0?1:next<rows.length?next:null}}}}}};
};}
(async()=>{
 const rows=Array.from({length:205},(_,i)=>({mode:'parallel',status:'active',task_instance_id:'peer-'+i,summary:'private text'}));
 rows.push({mode:'queue',status:'queued',task_instance_id:'queued'},{mode:'parallel',status:'completed',task_instance_id:'finished'});
 const args={session_id:sid,task_instance_id:iid},result=await collectReviewInstances(mock(rows),args);
 assert.equal(result.instances.length,206);assert.equal(result.instances[0],iid);assert(result.instances.includes('peer-204'));assert(!result.instances.includes('queued'));assert(!result.instances.includes('finished'));assert(!JSON.stringify(result).includes('private text'));
 const recovery=await collectReviewInstances(mock(rows),{...args,include_settled:true});assert(recovery.instances.includes('finished'),'recovery can verify a finished parallel identity');assert(!recovery.instances.includes('queued'));
 await assert.rejects(()=>collectReviewInstances(mock(rows,{changed:true}),args),/变化/);
 await assert.rejects(()=>collectReviewInstances(mock(rows,{malformed:true}),args),/分页/);
 const invalid=rows.slice();invalid[102]={mode:'parallel',status:'active',task_instance_id:null};await assert.rejects(()=>collectReviewInstances(mock(invalid),args),/身份/);
 const many=Array.from({length:2001},(_,i)=>({mode:'parallel',status:'active',task_instance_id:'p'+i}));
 const limited=await collectReviewInstances(mock(many),args);assert.equal(limited.truncated,true);assert.equal(limited.instances.length,2001);
 const handler=createReviewQueueHandler(mock(rows));const response=await handler({jsonrpc:'2.0',id:4,method:'tools/call',params:{name:'cindy_review_instances',arguments:args}});assert.equal(response.id,4);assert.equal(response.result.structuredContent.instances.length,206);
 const changedBatch=async request=>{if(request.params.arguments.view!=='review_queue')return mock(rows.slice(0,1))(request);const id=request.params.arguments.task_instance_id;return {result:{structuredContent:{...base,task_instance_id:id,state_revision:9,view:'review_queue',progress:{task_instance_id:id,records:[],pagination:{total:0,has_more:false,next_offset:null}}}}};};
 await assert.rejects(()=>collectSessionReviewQueue(changedBatch,args),/变化/,'a changed revision cannot mix discovery with a later batch');
 const peers=Array.from({length:25},(_,i)=>({mode:'parallel',status:'active',task_instance_id:'peer-'+i}));let queueReads=0;
 const wide=async request=>{const a=request.params.arguments,id=a.task_instance_id||iid,identity={...base,task_instance_id:id};if(a.view==='overview')return {result:{structuredContent:{...identity,protocol_capabilities:['review_queue_v1'],progress:{task_instance_id:id,schema_version:2}}}};if(a.view==='context')return {result:{structuredContent:{...identity,view:'context',progress:{session_work:{requests:peers,pagination:{total:peers.length,has_more:false,next_offset:null}}}}}};queueReads++;const records=id==='peer-24'?[{kind:'submission_review',task_id:'last',attempt_id:'attempt',actionable:true}]:[];return {result:{structuredContent:{...identity,view:'review_queue',progress:{task_instance_id:id,records,pagination:{total:records.length,has_more:false,next_offset:null}}}}};};
 const first=await collectSessionReviewQueue(wide,args);assert.equal(first.next_instance_id,'peer-19');assert.equal(first.next_scan_offset,0);assert.equal(first.truncated,true);assert.equal(queueReads,20,'each scan window has a bounded number of instances');
 const next=await collectSessionReviewQueue(wide,{...args,scan_instance_id:first.next_instance_id,start_offset:0,expected_state_revision:first.state_revision});assert.equal(next.task_instance_id,'peer-24');assert.equal(next.items[0].attempt_id,'attempt');assert.equal(queueReads,26);
 await assert.rejects(()=>collectSessionReviewQueue(wide,{...args,scan_instance_id:first.next_instance_id,start_offset:0,expected_state_revision:7}),/变化/,'instance window continuation is revision-bound');
 console.log('Review instance discovery: pinned pages, foreground identity, privacy, invalid records and bounded scans passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
