'use strict';
const {createHash}=require('node:crypto');
const PAGE_SIZE=100, MAX_PAGES=20, MAX_BATCH=25, MAX_RECONCILE_WINDOWS=5, MAX_INSTANCES_PER_WINDOW=20;

function keyFor(sessionId, instanceId, epoch, item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null;
  const identity=item.kind==='submission_review' ? item.attempt_id : item.source_fingerprint;
  if (item.actionable!==true || typeof item.task_id!=='string' || !item.task_id || item.task_id.length>256 || typeof identity!=='string' || !identity || identity.length>256 || !['submission_review','synthesis'].includes(item.kind)) return null;
  return createHash('sha256').update(JSON.stringify(['review-queue-v1',sessionId,instanceId,epoch,item.kind,item.task_id,identity])).digest('hex');
}

// Discover only identities from a revision-pinned foreground context.  The
// context can contain private text; none of that is returned to the caller.
async function collectReviewInstances(rpc,{session_id,task_instance_id,include_settled=false}) {
  if(![session_id,task_instance_id].every(x=>typeof x==='string' && x && x.length<=256))throw new Error('验收实例身份无效。');
  if(typeof include_settled!=='boolean')throw new Error('验收实例筛选无效。');
  const read=async args=>{
    const response=await rpc({jsonrpc:'2.0',method:'tools/call',params:{name:'get_progress',arguments:args}});
    const result=response?.result?.structuredContent;
    if(response?.error || response?.result?.isError || result?.session_id!==session_id || !Number.isSafeInteger(result?.state_revision))throw new Error('验收实例读取失败。');
    return result;
  };
  const foreground=await read({session_id,view:'overview'});
  if(foreground.task_instance_id!==task_instance_id || foreground.progress?.task_instance_id!==task_instance_id || foreground.progress?.schema_version!==2 || foreground.progress?.read_only || !foreground.protocol_capabilities?.includes('review_queue_v1') || !Number.isSafeInteger(foreground.epoch))throw new Error('主任务实例或后台能力无法核验。');
  const instances=new Set([task_instance_id]);let offset=0,total=null,truncated=false;
  for(let page=0;page<MAX_PAGES;page++) {
    const result=await read({session_id,task_instance_id,view:'context',offset,limit:PAGE_SIZE,expected_state_revision:foreground.state_revision});
    if(result.task_instance_id!==task_instance_id || result.epoch!==foreground.epoch || result.state_revision!==foreground.state_revision || result.view!=='context')throw new Error('任务实例列表已变化。');
    const work=result.progress?.session_work,rows=work?.requests,p=work?.pagination;
    if(!Array.isArray(rows) || rows.length>PAGE_SIZE || !p || !Number.isSafeInteger(p.total) || p.total<0 || typeof p.has_more!=='boolean' || (total!==null && total!==p.total))throw new Error('任务实例分页无效。');
    total=p.total;
    for(const item of rows) {
      if(item?.mode!=='parallel' || (!include_settled && !['active','pending_review'].includes(item.status) && item.closure_review_due!==true))continue;
      if(typeof item.task_instance_id!=='string' || !item.task_instance_id || item.task_instance_id.length>256)throw new Error('并行实例身份无效。');
      instances.add(item.task_instance_id);
    }
    if(!p.has_more){if(offset+rows.length!==p.total || p.next_offset!=null)throw new Error('任务实例分页不完整。');break;}
    if(!Number.isSafeInteger(p.next_offset) || p.next_offset!==offset+rows.length || p.next_offset<=offset || p.next_offset>=p.total)throw new Error('任务实例分页未前进。');
    offset=p.next_offset;if(page===MAX_PAGES-1)truncated=true;
  }
  return {session_id,task_instance_id,epoch:foreground.epoch,state_revision:foreground.state_revision,instances:[...instances],truncated};
}

// Return state facts only. The caller owns authorization, durable reservation,
// dispatch and error handling; this function never starts a model or writes KV.
async function collectReviewQueue(rpc, {session_id,task_instance_id,settled=[],observe_keys=[],start_offset=0,expected_state_revision}) {
  if (![session_id,task_instance_id].every(x=>typeof x==='string' && x && x.length<=256) || !Array.isArray(settled) || settled.length>5000 || settled.some(x=>typeof x!=='string' || !/^[a-f0-9]{64}$/.test(x)) || !Array.isArray(observe_keys) || observe_keys.length>MAX_BATCH || observe_keys.some(x=>typeof x!=='string'||!/^[a-f0-9]{64}$/.test(x)) || new Set(observe_keys).size!==observe_keys.length) throw new Error('验收队列身份无效。');
  const call=async args=>{
    const response=await rpc({jsonrpc:'2.0',method:'tools/call',params:{name:'get_progress',arguments:args}});
    if(response?.error || response?.result?.isError) throw new Error('验收队列读取失败，状态未确认。');
    const s=response?.result?.structuredContent;
    if(s?.session_id!==session_id || s?.task_instance_id!==task_instance_id || !Number.isSafeInteger(s?.state_revision)) throw new Error('验收队列身份无法核验。');
    return s;
  };
  const overview=await call({session_id,task_instance_id,view:'overview'});
  if(!overview.protocol_capabilities?.includes('review_queue_v1')) throw new Error('当前后台不支持验收队列。');
  if(!Number.isSafeInteger(overview.epoch) || overview.progress?.task_instance_id!==task_instance_id || overview.progress?.schema_version!==2 || overview.progress?.read_only) throw new Error('验收队列轮次或任务无法核验。');
  if(!Number.isSafeInteger(start_offset) || start_offset<0 || (start_offset>0 && (!Number.isSafeInteger(expected_state_revision) || expected_state_revision!==overview.state_revision))) throw new Error('验收队列续读版本无效或已变化。');
  const seen=new Set(settled), watched=new Set(observe_keys), observed=new Set(), candidates=[], unavailable=[];let offset=start_offset,truncated=false,expectedTotal=null;
  for(let page=0;page<MAX_PAGES;page++) {
    const s=await call({session_id,task_instance_id,view:'review_queue',offset,limit:PAGE_SIZE,expected_state_revision:overview.state_revision});
    if(s.state_revision!==overview.state_revision || s.epoch!==overview.epoch || s.view!=='review_queue') throw new Error('验收队列已变化，请重新读取。');
    const records=s.progress?.records, pagination=s.progress?.pagination;
    if(!Array.isArray(records) || records.length>PAGE_SIZE || !pagination || !Number.isSafeInteger(pagination.total) || pagination.total<0 || typeof pagination.has_more!=='boolean') throw new Error('验收队列分页无效。');
    if(s.progress?.task_instance_id!==task_instance_id || (expectedTotal!==null && pagination.total!==expectedTotal)) throw new Error('验收队列身份或分页总数已变化。');
    expectedTotal=pagination.total;
    for(const item of records) {
      const key=keyFor(session_id,task_instance_id,overview.epoch,item);
      if(!key){unavailable.push({kind:typeof item?.kind==='string'?item.kind.slice(0,32):'',task_id:typeof item?.task_id==='string'?item.task_id.slice(0,256):''});continue;}
      if(watched.has(key))observed.add(key);
      if(!seen.has(key)){seen.add(key);candidates.push({kind:item.kind,task_id:item.task_id,actionable:true,
        ...(item.kind==='submission_review'?{attempt_id:item.attempt_id}:{source_fingerprint:item.source_fingerprint}),key});}
    }
    if(!pagination.has_more) {
      if(offset+records.length!==pagination.total || pagination.next_offset!=null) throw new Error('验收队列分页不完整。');
      break;
    }
    if(!Number.isSafeInteger(pagination.next_offset) || pagination.next_offset!==offset+records.length || pagination.next_offset<=offset || pagination.next_offset>=pagination.total) throw new Error('验收队列分页未前进或不完整。');
    offset=pagination.next_offset;
    if(page===MAX_PAGES-1)truncated=true;
  }
  return {session_id,task_instance_id,epoch:overview.epoch,state_revision:overview.state_revision,
    items:candidates.slice(0,MAX_BATCH),remaining:Math.max(0,candidates.length-MAX_BATCH),
    truncated,scan_from:start_offset,next_scan_offset:truncated?offset:null,unavailable_count:unavailable.length,unavailable:unavailable.slice(0,MAX_BATCH),
    ...(observe_keys.length ? {observed_keys:[...observed],observation_complete:start_offset===0 && !truncated && !unavailable.length} : {})};
}

// Select work from exactly one verified instance at a time. No absence or
// completion is inferred if any earlier scan was incomplete.
async function collectSessionReviewQueue(rpc,{session_id,task_instance_id,settled=[],scan_instance_id,start_offset=0,expected_state_revision}) {
  const discovered=await collectReviewInstances(rpc,{session_id,task_instance_id});
  if(scan_instance_id && (!discovered.instances.includes(scan_instance_id) || !Number.isSafeInteger(expected_state_revision) || expected_state_revision!==discovered.state_revision || !Number.isSafeInteger(start_offset) || start_offset<0))throw new Error('验收实例续读版本无效或已变化。');
  if(!scan_instance_id && (start_offset!==0 || expected_state_revision!==undefined))throw new Error('验收实例续读参数无效。');
  let unavailable=0;
  const start=scan_instance_id?discovered.instances.indexOf(scan_instance_id):0;
  const targets=discovered.instances.slice(start,start+MAX_INSTANCES_PER_WINDOW);
  for(const target of targets) {
    const resume=target===scan_instance_id?{start_offset,expected_state_revision}:{};
    const batch=await collectReviewQueue(rpc,{session_id,task_instance_id:target,settled,...resume});
    if(batch.state_revision!==discovered.state_revision)throw new Error('验收实例列表已变化。');
    unavailable+=batch.unavailable_count;
    if(batch.items.length)return {...batch,foreground_task_instance_id:task_instance_id,instances_truncated:discovered.truncated};
    if(batch.truncated)return {...batch,foreground_task_instance_id:task_instance_id,instances_truncated:discovered.truncated};
  }
  const nextInstance=discovered.instances[start+targets.length];
  if(nextInstance)return {session_id,task_instance_id:nextInstance,foreground_task_instance_id:task_instance_id,epoch:null,state_revision:discovered.state_revision,items:[],remaining:0,truncated:true,unavailable_count:unavailable,instances_truncated:discovered.truncated,next_instance_id:nextInstance,next_scan_offset:0};
  return {session_id,task_instance_id,foreground_task_instance_id:task_instance_id,epoch:discovered.epoch,state_revision:discovered.state_revision,scan_from:scan_instance_id===task_instance_id?start_offset:0,items:[],remaining:0,truncated:discovered.truncated,unavailable_count:unavailable,instances_truncated:discovered.truncated};
}

function createReviewQueueHandler(rpc) {
  return async request=>{
    if(request.method!=='tools/call' || !['cindy_review_collect','cindy_review_reconcile','cindy_review_instances','cindy_review_collect_session'].includes(request.params?.name))return null;
    try {
      if(request.params.name==='cindy_review_instances') {
        const result=await collectReviewInstances(rpc,request.params.arguments||{});
        return {jsonrpc:'2.0',id:request.id,result:{content:[],structuredContent:result}};
      }
      if(request.params.name==='cindy_review_collect_session') {
        const result=await collectSessionReviewQueue(rpc,request.params.arguments||{});
        return {jsonrpc:'2.0',id:request.id,result:{content:[],structuredContent:result}};
      }
      let result=await collectReviewQueue(rpc,request.params.arguments||{});
      if(request.params.name==='cindy_review_reconcile'){
        if(!request.params.arguments?.observe_keys?.length)throw new Error('缺少需要核对的派发标识。');
        // Only a scan beginning at zero can establish absence. Continue under
        // the same revision, and never treat a failed or partial scan as proof.
        const initial=result, observed=new Set(result.observed_keys), missingIdentity=()=>result.unavailable_count>0;
        let incomplete=missingIdentity(), windows=1;
        while(initial.scan_from===0 && result.truncated && windows<MAX_RECONCILE_WINDOWS) {
          result=await collectReviewQueue(rpc,{...request.params.arguments,start_offset:result.next_scan_offset,expected_state_revision:initial.state_revision});
          if(result.epoch!==initial.epoch)throw new Error('验收队列轮次已变化。');
          for(const key of result.observed_keys)observed.add(key);
          incomplete ||= missingIdentity();windows++;
        }
        const complete=initial.scan_from===0 && !result.truncated && !incomplete;
        return {jsonrpc:'2.0',id:request.id,result:{content:[],structuredContent:{session_id:initial.session_id,task_instance_id:initial.task_instance_id,epoch:initial.epoch,state_revision:initial.state_revision,observed_keys:[...observed],observation_complete:complete}}};
      }
      return {jsonrpc:'2.0',id:request.id,result:{content:[],structuredContent:result}};
    }
    catch(error){return {jsonrpc:'2.0',id:request.id,error:{code:-32602,message:String(error.message||error)}};}
  };
}
module.exports={keyFor,collectReviewQueue,collectReviewInstances,collectSessionReviewQueue,createReviewQueueHandler};
