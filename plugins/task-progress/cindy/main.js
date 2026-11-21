const panelBus = new BroadcastChannel('task-progress-panel');
const panelToolCalls = new Map();
const HOOK_NAMES = new Set(['will-compose-context', 'did-task-state-changed', 'did-message-created', 'did-session-switched']);
let kvWriteQueue = Promise.resolve();
const hostInitializationQueues = new Map();
const reviewDispatchInFlight = new Set();
const reviewDispatchDirty = new Set();
const reviewDispatchTimers = new Map();
// A continuation is only a read optimisation; after restart it safely starts
// at page zero again. Never use it as evidence that an obligation disappeared.
const reviewScanCursors = new Map();
const reviewWatchdogTimers = new Map();
const reviewRecoveryRetries = new Map();
const reviewSettingsGeneration = new Map();
const reviewSuspendedHosts = new Set();
const reviewHostEventSequence = new Map();
const reviewHostActivityGeneration = new Map();
const reviewGapHosts = new Set();
// Cindy owns the user-action association and may discard it on host/plugin
// restart. A persisted opt-in is not proof that the live association exists.
const reviewHostAssociations = new Set();
// Cindy permits only one background agent request per plugin at a time.
let reviewHostCallInFlight = false;
let reviewLastHostDispatchAt = 0, reviewLastHostDispatchSession = null;
const REVIEW_CROSS_SESSION_GAP_MS = 10500;
const REVIEW_WAITER_TTL_MS = 60000;
const reviewHostWaiters = new Map();
const REVIEW_WATCHDOG_MS = 60000;
const REVIEW_TRIGGER_TOOLS = new Set(['update_task','review_task','review_task_closure','decompose_task','review_stage_inventory','handoff_request','update_step','rebuild_task_network','record_task_execution','start_task_execution','cancel_request','activate_request','record_coverage','revise_task_plan','delete_task']);
const MUTATING_TASK_TOOLS = new Set(['decompose_task','delete_task','update_task','update_step','record_task_execution','start_task_execution','review_task_split','bind_task_agent','review_task','reassign_task','snapshot_task_network','revise_task_title','revise_task_plan','rebuild_task_network','route_request','activate_request','update_request','cancel_request','update_task_context','clear_progress','handoff_request','review_task_closure','review_stage_inventory','record_coverage','select_work_context']);
// Retain a newly created binding in memory if persisting it fails. This keeps
// subsequent calls from silently creating another session in this process.
const unsavedHostBindings = new Map();
let hookReceiptSequence = 0;

// Focus events deliberately omit companion sessions on Cindy 0.1.143. The
// task-view peek reports the raw foreground session without opening a tab.
// Never infer focus from tool calls or the last eligible switch event.
let focusPeekRunning=false;
async function syncPanelFocus() {
  if(focusPeekRunning || typeof cindy.taskView!=='function')return;
  focusPeekRunning=true;
  try {
    const result=await cindy.taskView({peek:true});
    // A failed lookup must not leave another conversation's task visible.
    // Wait for the next successful peek instead of guessing from old events.
    const host=result.ok && typeof result.sessionId==='string' ? result.sessionId : '';
    let changed=false;
    const state=await updatePluginKv(current=>{
      if((current.taskProgressActiveHostSessionId||'')===host)return null;
      changed=true;return {...current,taskProgressActiveHostSessionId:host,taskProgressPanelDiagnostic:{kind:'session-switch',hasHostSession:Boolean(host),mapped:Boolean(host && current.taskProgressSessionsByHostSession?.[host]),at:Date.now()}};
    });
    if(changed)panelBus.postMessage({type:'bind-session',session_id:host?state.taskProgressSessionsByHostSession?.[host]||null:null,diagnostic:state.taskProgressPanelDiagnostic});
  }catch(_) {
    try {
      const state=await updatePluginKv(current=>({...current,taskProgressActiveHostSessionId:'',taskProgressPanelDiagnostic:{kind:'session-switch',hasHostSession:false,mapped:false,at:Date.now()}}));
      panelBus.postMessage({type:'bind-session',session_id:null,diagnostic:state.taskProgressPanelDiagnostic});
    }catch(_) { /* KV failure leaves no trustworthy replacement. */ }
  }
  finally {focusPeekRunning=false;}
}
if(typeof setInterval==='function') {
  const focusTimer=setInterval(()=>{void syncPanelFocus();},1000);
  if(focusTimer && typeof focusTimer.unref==='function')focusTimer.unref();
  void syncPanelFocus();
}

// Cindy bounds each Node request to 256 KiB. Keep the public export tool intact,
// but transport its potentially large workbook/report through bounded chunks.
async function requestTaskTool(name, args, callId) {
  const options = { timeoutMs: 120000, ...(callId ? { callId } : {}) };
  if (name !== 'save_export_files') {
    return cindy.node.request({ ...options, method: 'tools/call', params: { name, arguments: args } });
  }
  const payload = JSON.stringify(args);
  if (payload.length > 60 * 1024 * 1024) throw new Error('导出内容超过传输大小限制。');
  const encoder = new TextEncoder();
  const paramsFor = (operation, data) => ({ name: `cindy_export_${operation}`, arguments: data });
  const paramsBytes = (operation, data) => encoder.encode(JSON.stringify(paramsFor(operation, data))).byteLength;
  const send = async (operation, data) => {
    if (paramsBytes(operation, data) > 240 * 1024) throw new Error('导出分块超过传输大小限制。');
    const response = await cindy.node.request({ ...options, method: 'tools/call',
      params: paramsFor(operation, data) });
    if (!response?.ok) throw new Error(response?.message || '导出传输失败。');
    return response;
  };
  const begun = await send('begin', { length: payload.length });
  const transfer_id = begun.result?.structuredContent?.transfer_id;
  if (typeof transfer_id !== 'string' || !transfer_id) throw new Error('导出传输未返回有效标识。');
  try {
    // Measure the entire serialized params, including escaping and Unicode.
    // Leave 16 KiB headroom below the native limit and shrink if needed.
    for (let offset = 0; offset < payload.length;) {
      let length = Math.min(65536, payload.length - offset);
      let data = { transfer_id, offset, chunk: payload.slice(offset, offset + length) };
      while (paramsBytes('append', data) > 240 * 1024 && length > 1) {
        length = Math.floor(length / 2);
        data = { transfer_id, offset, chunk: payload.slice(offset, offset + length) };
      }
      await send('append', data);
      offset += length;
    }
    return await send('finish', { transfer_id });
  } catch (error) {
    try { await send('abort', { transfer_id }); } catch (_) { /* The worker also expires abandoned transfers. */ }
    throw error;
  }
}

function updatePluginKv(change) {
  const operation = kvWriteQueue.then(async () => {
    const response = await fetch('/kv');
    if (!response.ok) throw new Error('Task Progress plugin state unavailable');
    const current = await response.json();
    const next = change(current && typeof current === 'object' && !Array.isArray(current) ? current : {});
    if (next === null) return current;
    const saved = await fetch('/kv', { method: 'PUT', body: JSON.stringify(next) });
    if (!saved.ok) throw new Error('Task Progress plugin state update failed');
    return next;
  });
  kvWriteQueue = operation.catch(() => {});
  return operation;
}

async function recordHookReceipt(msg, { refresh = false, outcome = 'observed' } = {}) {
  const hostSessionId = msg.data?.sessionId;
  if (!HOOK_NAMES.has(msg.name) || typeof hostSessionId !== 'string' || !hostSessionId) return null;
  const receiptId = `${Date.now().toString(36)}-${++hookReceiptSequence}`;
  let target = null;
  await updatePluginKv(current => {
    const all = { ...(current.taskProgressHookDiagnostics || {}) };
    const session = { ...(all[hostSessionId] || {}) };
    const events = { ...(session.events || {}) };
    const previous = events[msg.name] || {};
    const mapped = current.taskProgressSessionsByHostSession?.[hostSessionId];
    const active = msg.name === 'did-session-switched' || current.taskProgressActiveHostSessionId === hostSessionId;
    const requested = refresh && active && typeof mapped === 'string' && Boolean(mapped);
    events[msg.name] = {
      received: (previous.received || 0) + 1,
      refresh_requested: (previous.refresh_requested || 0) + (requested ? 1 : 0),
      refresh_ok: previous.refresh_ok || 0,
      refresh_failed: previous.refresh_failed || 0,
      refresh_skipped: previous.refresh_skipped || 0,
      ...(previous.last_refresh ? { last_refresh: previous.last_refresh } : {}),
      last: { at: Date.now(), receipt_id: receiptId, outcome,
        ...(Number.isSafeInteger(msg.seq) ? { seq: msg.seq } : {}),
        ...(Number.isSafeInteger(msg.dropped) && msg.dropped > 0 ? { dropped: msg.dropped } : {}),
        ...(msg.name === 'did-message-created' ? { role: msg.data?.role === 'assistant' ? 'assistant' : 'user_or_other' } : {}),
        refresh_requested: requested }
    };
    const pending = Array.isArray(session.pending) ? session.pending.slice(-15) : [];
    if (requested) pending.push({ receipt_id: receiptId, name: msg.name, session_id: mapped });
    all[hostSessionId] = { events, pending };
    const keys = Object.keys(all);
    while (keys.length > 16) delete all[keys.shift()];
    target = requested ? { receipt_id: receiptId, trigger: msg.name, host_session_id: hostSessionId, session_id: mapped } : null;
    return { ...current, taskProgressHookDiagnostics: all,
      ...(msg.name === 'did-session-switched' ? { taskProgressActiveHostSessionId: hostSessionId } : {}) };
  });
  return target;
}

async function recordPanelRefresh(result) {
  if (!result || typeof result.host_session_id !== 'string' || typeof result.receipt_id !== 'string') return;
  await updatePluginKv(current => {
    const all = { ...(current.taskProgressHookDiagnostics || {}) };
    const session = all[result.host_session_id];
    if (!session || !Array.isArray(session.pending)) return null;
    const pending = session.pending.find(item => item.receipt_id === result.receipt_id &&
      item.session_id === result.session_id && item.name === result.trigger);
    if (!pending) return null; // Duplicate or stale acknowledgement.
    const events = { ...session.events };
    const entry = { ...(events[pending.name] || {}) };
    const key = result.status === 'read_ok' ? 'refresh_ok'
      : result.status === 'read_failed' ? 'refresh_failed' : 'refresh_skipped';
    entry[key] = (entry[key] || 0) + 1;
    entry.last_refresh = { at: Date.now(), status: result.status,
      ...(Number.isSafeInteger(result.state_revision) ? { state_revision: result.state_revision } : {}) };
    events[pending.name] = entry;
    all[result.host_session_id] = { events, pending: session.pending.filter(item => item !== pending) };
    return { ...current, taskProgressHookDiagnostics: all };
  });
}

function hookGuidance(source, expectedSessionId, expectedInstanceId) {
  if (!source || source.session_id !== expectedSessionId ||
      source.task_instance_id !== expectedInstanceId || source.view !== 'overview') return null;
  const progress = source.progress;
  if (!progress || progress.task_instance_id !== expectedInstanceId || progress.read_only ||
      progress.schema_version !== 2 || !Number.isInteger(progress.epoch)) return null;
  const notes = [];
  if (progress.planning_status === 'required') notes.push('当前任务尚需完成 Task Progress 规划；先读取概览并完善任务网络。');
  if (progress.pending_review_count > 0) notes.push('有待主会话验收的提交；读取对应节点与证据后独立验收。');
  if (progress.synthesis_ready_count > 0) notes.push('有 '+progress.synthesis_ready_count+' 个工作包已具备综合验收条件（'+(progress.synthesis_ready_ids || []).slice(0,3).join('、')+'）；主会话先核对各子结果和证据，再记录综合验收；未解决项不得标为通过。');
  if (progress.stage_inventory_due_count > 0) notes.push('有阶段盘点待处理；读取当前范围与指纹后记录决定。');
  if (progress.closure_review_due) notes.push('根任务收尾仍需复核；读取当前闭环绑定后处理。');
  if (!notes.length) return null;
  return `Task Progress 只读提醒（实例 ${expectedInstanceId}，epoch ${progress.epoch}，状态版本 ${source.state_revision}）：${notes.join(' ')}这份附加资料不代表用户原话，也不自动修改任务状态。`;
}

async function composeTaskProgressContext(msg) {
  const hostSessionId = msg.data?.sessionId;
  if (typeof hostSessionId !== 'string' || !hostSessionId || typeof msg.hookId !== 'string' || !msg.hookId) return;
  let action = 'allow', blocks, expired = false;
  const readGuidance = async () => {
    const current = await fetch('/kv').then(r => r.ok ? r.json() : null);
    if (expired) return null;
    const context = current?.taskProgressContextsByHostSession?.[hostSessionId];
    if (context && typeof context.session_id === 'string' && typeof context.task_instance_id === 'string' &&
        current?.taskProgressSessionsByHostSession?.[hostSessionId] === context.session_id) {
      const response = await cindy.node.request({
        method: 'tools/call',
        params: { name: 'get_progress', arguments: {
          session_id: context.session_id, task_instance_id: context.task_instance_id, view: 'overview'
        } }, timeoutMs: 1200
      });
      if (expired) return null;
      if (response?.ok) {
        let expectedInstanceId = context.task_instance_id;
        let source = response.result?.structuredContent || response.result?.result?.structuredContent;
        if (!source && (response.result?.isError || response.result?.result?.isError) && current.taskProgressSessionsByHostSession?.[hostSessionId] === context.session_id) {
          // The previous foreground instance may have been archived from a
          // different client. Resolve the current instance only within the
          // same verified Cindy-session binding.
          const latest = await cindy.node.request({method:'tools/call', params:{name:'get_progress', arguments:{session_id:context.session_id, view:'overview'}}, timeoutMs:1200});
          if (expired) return null;
          const candidate = latest?.result?.structuredContent || latest?.result?.result?.structuredContent;
          if (latest?.ok && candidate?.session_id === context.session_id && candidate?.progress?.task_instance_id === candidate?.task_instance_id) {
            source = candidate;
            expectedInstanceId = candidate.task_instance_id;
            await updatePluginKv(value => {
              if (expired) return null;
              const previous = value.taskProgressContextsByHostSession?.[hostSessionId];
              if (value.taskProgressSessionsByHostSession?.[hostSessionId] !== context.session_id || previous?.session_id !== context.session_id || previous?.task_instance_id !== context.task_instance_id) return null;
              return {...value, taskProgressContextsByHostSession:{...value.taskProgressContextsByHostSession, [hostSessionId]:{session_id:context.session_id, task_instance_id:candidate.task_instance_id}}};
            });
          }
        }
        const note = hookGuidance(source, context.session_id, expectedInstanceId);
        if (note) return [{ id: 'task-progress-current-state', text: note }];
      }
    }
    return null;
  };
  let timer;
  try {
    // Cindy's composition hook has a host deadline. Limit the whole chain,
    // including KV and a possible stale-instance recovery, not just each RPC.
    blocks = await Promise.race([readGuidance(), new Promise(resolve => { timer=setTimeout(()=>{expired=true;resolve(null);}, 2200); })]);
    if (blocks) action = 'context';
  } catch (_) { /* Unknown ownership or unavailable state gives no reminder. */ }
  finally { clearTimeout(timer); }
  await cindy.send({ type: 'event-verdict', hookId: msg.hookId, action, ...(blocks ? { blocks } : {}) });
  try { await recordHookReceipt(msg, { outcome: action }); } catch (_) { /* Verdict already sent. */ }
}

async function observeRefreshEvent(msg, refresh = true) {
  try {
    const target = await recordHookReceipt(msg, { refresh });
    if (target) panelBus.postMessage({ type: 'refresh-mapped-session', ...target });
  } catch (_) { /* Observation failures never change task state or block a turn. */ }
}

async function writePanelState(patch) {
  try {
    await updatePluginKv(current => ({ ...current, ...patch }));
    return true;
  } catch (_) { return false; }
}

// A reservation is durably written before invoking the host. A missing or
// ambiguous host response is intentionally not retried: the original request
// might already be queued. Explicit enablement is required per Cindy session.
async function dispatchDueReviews(hostSessionId) {
  if (typeof hostSessionId !== 'string' || !hostSessionId || reviewDispatchInFlight.has(hostSessionId)) return 'skipped';
  reviewDispatchInFlight.add(hostSessionId);
  let reservationId=null,keepHostWaiter=false;
  const settingsGeneration=reviewSettingsGeneration.get(hostSessionId)||0;
  try {
    await kvWriteQueue;
    const response=await fetch('/kv');if(!response.ok)throw new Error('验收设置暂不可读取。');
    const state=await response.json(), config=state?.taskProgressAutoReview?.[hostSessionId];
    if(config?.enabled!==true)return 'disabled';
    if(reviewSuspendedHosts.has(hostSessionId) || config.host_suspended===true)return 'host_suspended';
    if(config.reservation || Object.values(config.ledger||{}).some(item=>item?.state==='uncertain'))return 'recovery_required';
    const outstanding=Object.values(config.ledger||{});
    if(outstanding.some(item=>item?.state==='dispatched'))return 'awaiting_completion';
    const hasPendingReview=outstanding.some(item=>item?.state==='review_pending');
    const stalledBatches=new Set(outstanding.filter(item=>item?.state==='review_pending').map(item=>item.batch_id||'legacy'));
    if(stalledBatches.size>=2)return 'review_pending';
    if(config.last_error==='RETRY_EXHAUSTED')return 'retry_exhausted';
    if(Number.isFinite(config.retry_after) && config.retry_after>Date.now())return 'retry_wait';
    const sessionId=state.taskProgressSessionsByHostSession?.[hostSessionId];
    const context=state.taskProgressContextsByHostSession?.[hostSessionId];
    if(!sessionId || context?.session_id!==sessionId || !context.task_instance_id || config.session_id!==sessionId) return 'binding_changed';
    if(typeof cindy.agent?.run!=='function') {
      await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},latest=all[hostSessionId];
        if(latest?.enabled!==true || latest.session_id!==sessionId)return null;
        all[hostSessionId]={...latest,last_error:'HOST_UNSUPPORTED'};
        return {...value,taskProgressAutoReview:all};
      });
      return 'host_unsupported';
    }
    const ledger=config.ledger && typeof config.ledger==='object' ? config.ledger : {};
    const settled=Object.keys(ledger);
    if(settled.length>=5000)throw new Error('验收派发记录已达容量上限，需要检查历史。');
    let cursor=reviewScanCursors.get(hostSessionId);
    if(cursor && (cursor.session_id!==sessionId || cursor.foreground_task_instance_id!==context.task_instance_id)){reviewScanCursors.delete(hostSessionId);cursor=null;}
    const collected=await requestTaskTool('cindy_review_collect_session',{session_id:sessionId,task_instance_id:context.task_instance_id,settled,...(cursor?{scan_instance_id:cursor.task_instance_id,start_offset:cursor.offset,expected_state_revision:cursor.revision}:{})});
    if(cursor && (!collected.ok || collected.result?.isError)){reviewScanCursors.delete(hostSessionId);return 'queue_changed';}
    if(!collected.ok || collected.result?.isError)throw new Error('验收待办读取失败。');
    const batch=collected.result?.structuredContent;
    if(batch?.session_id!==sessionId || batch?.foreground_task_instance_id!==context.task_instance_id || typeof batch?.task_instance_id!=='string' || !batch.task_instance_id || !Array.isArray(batch.items))throw new Error('验收待办身份不匹配。');
    const targetId=batch.task_instance_id;
    if(cursor && (batch.state_revision!==cursor.revision || (targetId===cursor.task_instance_id && ((cursor.epoch!==null && batch.epoch!==cursor.epoch) || batch.scan_from!==cursor.offset)))){reviewScanCursors.delete(hostSessionId);return 'queue_changed';}
    const priorUnavailable=Boolean(cursor?.unavailable);
    const batchCursor=cursor && targetId===cursor.task_instance_id?cursor:null;
    if(cursor && !batchCursor){reviewScanCursors.delete(hostSessionId);cursor=null;}
    if(!batch.items.length){
      if(batch.truncated && typeof batch.next_instance_id==='string' && batch.next_instance_id && batch.next_scan_offset===0) {
        reviewScanCursors.set(hostSessionId,{session_id:sessionId,foreground_task_instance_id:context.task_instance_id,task_instance_id:batch.next_instance_id,offset:0,revision:batch.state_revision,epoch:null,unavailable:Boolean(priorUnavailable || batch.unavailable_count)});
        return 'scan_more';
      }
      if(batch.truncated && Number.isSafeInteger(batch.next_scan_offset) && batch.next_scan_offset>(cursor?.offset||0)) {
        reviewScanCursors.set(hostSessionId,{session_id:sessionId,foreground_task_instance_id:context.task_instance_id,task_instance_id:targetId,offset:batch.next_scan_offset,revision:batch.state_revision,epoch:batch.epoch,unavailable:Boolean(priorUnavailable || batch.unavailable_count)});
        return 'scan_more';
      }
      const incomplete=Boolean(priorUnavailable || batch.unavailable_count || batch.truncated);
      reviewScanCursors.delete(hostSessionId);
      if(incomplete || config.last_error || config.retry_count || config.retry_after)await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},latest=all[hostSessionId];
        if(latest?.enabled!==true || latest.session_id!==sessionId || latest.reservation || Object.values(latest.ledger||{}).some(item=>item?.state==='uncertain'))return null;
        all[hostSessionId]={...latest,last_error:incomplete?'QUEUE_INCOMPLETE':hasPendingReview?'REVIEW_PENDING':null,retry_count:0,retry_after:null};
        return {...value,taskProgressAutoReview:all};
      });
      return incomplete ? 'incomplete' : hasPendingReview ? 'review_pending' : 'empty';
    }
    const keys=batch.items.map(item=>item.key);
    if(keys.length>25 || keys.some(key=>typeof key!=='string'||!/^[a-f0-9]{64}$/.test(key)) || new Set(keys).size!==keys.length)throw new Error('验收待办标识无效。');
    await updatePluginKv(value=>{
      const all={...(value.taskProgressAutoReview||{})}, latest=all[hostSessionId];
      if(latest?.enabled!==true || latest.session_id!==sessionId || value.taskProgressSessionsByHostSession?.[hostSessionId]!==sessionId || value.taskProgressContextsByHostSession?.[hostSessionId]?.task_instance_id!==context.task_instance_id || latest.reservation) return null;
      const currentLedger={...(latest.ledger||{})};
      if(keys.some(key=>Object.hasOwn(currentLedger,key)))return null;
      reservationId=String((latest.sequence||0)+1);
      all[hostSessionId]={...latest,sequence:Number(reservationId),reservation:{id:reservationId,keys,session_id:sessionId,task_instance_id:targetId,at:Date.now()},last_error:null};
      return {...value,taskProgressAutoReview:all};
    });
    if(!reservationId)return 'superseded';
    // Do not send if the user disabled the feature or the binding changed
    // while the queue was being read and reserved.
    const latestResponse=await fetch('/kv');
    if(!latestResponse.ok)throw new Error('验收设置暂不可读取。');
    const latest=await latestResponse.json(), active=latest?.taskProgressAutoReview?.[hostSessionId];
    if(reviewSuspendedHosts.has(hostSessionId) || active?.host_suspended===true || active?.enabled!==true || active.session_id!==sessionId || active.reservation?.id!==reservationId ||
       latest.taskProgressSessionsByHostSession?.[hostSessionId]!==sessionId ||
       latest.taskProgressContextsByHostSession?.[hostSessionId]?.task_instance_id!==context.task_instance_id) {
      const suspended=reviewSuspendedHosts.has(hostSessionId) || active?.host_suspended===true;
      await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},current=all[hostSessionId];
        if(current?.reservation?.id!==reservationId || current.reservation.session_id!==sessionId || current.reservation.task_instance_id!==targetId)return null;
        all[hostSessionId]={...current,reservation:null,last_error:suspended?'HOST_SUSPENDED':'BINDING_CHANGED'};
        return {...value,taskProgressAutoReview:all};
      });
      return suspended?'host_suspended':'binding_changed';
    }
    // The queue can change between reading and reserving it. Re-read every
    // selected key against one pinned revision before creating a host turn.
    // Positive evidence for every selected key is sufficient here, even if
    // unrelated records are unavailable; recovery still requires a complete
    // observation before it can conclude that any key is absent.
    let confirmed=null;
    try {
      const check=await requestTaskTool('cindy_review_reconcile',{session_id:sessionId,task_instance_id:targetId,observe_keys:keys,...(batchCursor?{start_offset:batchCursor.offset,expected_state_revision:batchCursor.revision}:{})});
      if(check?.ok && !check.result?.isError)confirmed=check.result?.structuredContent;
    }catch(_){ /* No host request has been sent, so this is safe to retry. */ }
    const present=new Set(Array.isArray(confirmed?.observed_keys)?confirmed.observed_keys:[]);
    let fresh=confirmed?.session_id===sessionId && confirmed?.task_instance_id===targetId &&
      Array.isArray(confirmed?.observed_keys) && confirmed?.state_revision===batch.state_revision &&
      confirmed?.epoch===batch.epoch && present.size===keys.length && keys.every(key=>present.has(key));
    if(fresh && targetId!==context.task_instance_id) {
      try {
        const check=await requestTaskTool('cindy_review_instances',{session_id:sessionId,task_instance_id:context.task_instance_id});
        const identity=check?.result?.structuredContent;
        fresh=Boolean(check?.ok && !check.result?.isError && identity?.session_id===sessionId && identity?.task_instance_id===context.task_instance_id && identity?.state_revision===batch.state_revision && Array.isArray(identity.instances) && identity.instances.includes(targetId));
      } catch (_) {fresh=false;}
    }
    if(!fresh) {
      let retry=false;
      await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},current=all[hostSessionId];
        if(current?.reservation?.id!==reservationId || current.reservation.session_id!==sessionId || current.reservation.task_instance_id!==targetId)return null;
        const count=(current.retry_count||0)+1;retry=count<=3;
        all[hostSessionId]={...current,reservation:null,retry_count:count,retry_after:retry?Date.now()+1000:null,last_error:retry?'QUEUE_CHANGED':'RETRY_EXHAUSTED'};
        return {...value,taskProgressAutoReview:all};
      });
      return retry?'queue_changed':'retry_exhausted';
    }
    // A stop or setting change may also have happened during the second read.
    const beforeSendResponse=await fetch('/kv');if(!beforeSendResponse.ok)throw new Error('验收设置暂不可读取。');
    const beforeSend=await beforeSendResponse.json(),beforeConfig=beforeSend?.taskProgressAutoReview?.[hostSessionId];
    if(reviewSuspendedHosts.has(hostSessionId) || beforeConfig?.host_suspended===true || beforeConfig?.enabled!==true || beforeConfig.reservation?.id!==reservationId || beforeSend.taskProgressSessionsByHostSession?.[hostSessionId]!==sessionId || beforeSend.taskProgressContextsByHostSession?.[hostSessionId]?.task_instance_id!==context.task_instance_id) {
      const suspended=reviewSuspendedHosts.has(hostSessionId) || beforeConfig?.host_suspended===true;
      await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},current=all[hostSessionId];
        if(current?.reservation?.id!==reservationId || current.reservation.session_id!==sessionId || current.reservation.task_instance_id!==targetId)return null;
        all[hostSessionId]={...current,reservation:null,last_error:suspended?'HOST_SUSPENDED':'BINDING_CHANGED'};
        return {...value,taskProgressAutoReview:all};
      });
      return suspended?'host_suspended':'binding_changed';
    }
    const now=Date.now();
    for(const [host,at] of reviewHostWaiters)if(now-at>REVIEW_WAITER_TTL_MS)reviewHostWaiters.delete(host);
    const firstWaiting=reviewHostWaiters.keys().next().value;
    if(reviewHostCallInFlight || (firstWaiting && firstWaiting!==hostSessionId) || (reviewLastHostDispatchSession && reviewLastHostDispatchSession!==hostSessionId && now-reviewLastHostDispatchAt<REVIEW_CROSS_SESSION_GAP_MS)) {
      let deferred=false;
      await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},current=all[hostSessionId];
        if(current?.reservation?.id!==reservationId || current.reservation.session_id!==sessionId || current.reservation.task_instance_id!==targetId)return null;
        deferred=current.enabled===true && current.host_suspended!==true;all[hostSessionId]={...current,reservation:null};return {...value,taskProgressAutoReview:all};
      });
      if(!deferred || reviewSuspendedHosts.has(hostSessionId) || settingsGeneration!==(reviewSettingsGeneration.get(hostSessionId)||0))return 'superseded';
      reviewHostWaiters.set(hostSessionId,Date.now());keepHostWaiter=true;
      return 'host_busy';
    }
    reviewHostWaiters.delete(hostSessionId);
    const instructions=batch.items.map(item=>item.kind==='submission_review'
      ? `提交复核：任务 ${item.task_id}，执行尝试 ${item.attempt_id}`
      : `综合验收：工作包 ${item.task_id}，结果指纹 ${item.source_fingerprint}`).join('\n');
    let outcome,hostTimer;
    reviewHostCallInFlight=true;
    try {
      const hostRequest=cindy.agent.run({mode:'continue',trigger:'background',sessionId:hostSessionId,
        promptTemplate:'请处理 Task Progress 验收待办：{{user_message}}',
        userMessage:`请先核对当前任务会话 ${sessionId}、主任务实例 ${context.task_instance_id} 和待验收实例 ${targetId}；若会话或主任务已切换，或目标实例不再属于该会话，则停止。逐项重新读取目标实例验收队列，只有任务 ID 与执行尝试或结果指纹仍完全一致时才继续；不再匹配的事项跳过。读取结果和证据并独立复核；只在真实满足条件时记录验收，未解决或证据不足时保持未解决。\n`+instructions,
        event:{session_id:sessionId,task_instance_id:targetId,foreground_task_instance_id:context.task_instance_id,state_revision:batch.state_revision,reservation_id:reservationId}});
      outcome=await Promise.race([hostRequest,new Promise(resolve=>{hostTimer=setTimeout(()=>resolve({ok:false,errorCode:'DELIVERY_UNKNOWN'}),15000);})]);
    } catch(error) {outcome={ok:false,errorCode:'DELIVERY_UNKNOWN',message:String(error.message||error)};}
    finally {
      clearTimeout(hostTimer);
      // A successful or ambiguous call may have consumed the host slot. A
      // known pre-run denial did not. Preserve a small cross-session gap so
      // several waiting sessions do not repeatedly exhaust rate-limit retries.
      if(outcome?.ok===true || !['RATE_LIMITED','PERMISSION_DENIED'].includes(outcome?.errorCode)) {
        reviewLastHostDispatchAt=Date.now();reviewLastHostDispatchSession=hostSessionId;
      }
      reviewHostCallInFlight=false;
    }
    let dispatchState='uncertain';
    await updatePluginKv(value=>{
      const all={...(value.taskProgressAutoReview||{})},latest=all[hostSessionId];
      if(latest?.reservation?.id!==reservationId || latest.reservation.session_id!==sessionId || latest.reservation.task_instance_id!==targetId || JSON.stringify(latest.reservation.keys)!==JSON.stringify(keys))return null;
      const matched=outcome?.ok===true && outcome.sessionId===hostSessionId && outcome.mode==='continue';
      if(matched)reviewHostAssociations.add(hostSessionId);
      // These two errors are returned by Cindy before its runner is invoked;
      // unlike a transport error, they cannot have queued a model turn.
      if(outcome?.ok===false && ['RATE_LIMITED','PERMISSION_DENIED'].includes(outcome.errorCode)) {
        if(latest.enabled!==true){dispatchState='disabled';all[hostSessionId]={...latest,reservation:null};return {...value,taskProgressAutoReview:all};}
        const denied=outcome.errorCode==='PERMISSION_DENIED',retries=(latest.retry_count||0)+1;
        if(denied)reviewHostAssociations.delete(hostSessionId);
        dispatchState=denied?'consent_required':retries>3?'retry_exhausted':'rate_limited';
        all[hostSessionId]={...latest,reservation:null,enabled:denied?false:latest.enabled,retry_count:retries,
          retry_after:!denied && retries<=3?Date.now()+12000:null,
          last_error:denied?'CONSENT_REQUIRED':retries>3?'RETRY_EXHAUSTED':'RATE_LIMITED'};
        return {...value,taskProgressAutoReview:all};
      }
      const ledger={...(latest.ledger||{})};
      for(const key of keys)ledger[key]={state:matched?'dispatched':'uncertain',at:Date.now(),session_id:sessionId,task_instance_id:targetId,batch_id:reservationId};
      const olderPending=Object.values(ledger).some(item=>item?.state==='review_pending');
      dispatchState=matched?(olderPending?'dispatched_with_pending':'dispatched'):'uncertain';
      all[hostSessionId]={...latest,ledger,reservation:null,retry_count:0,retry_after:null,last_error:matched?(olderPending?'REVIEW_PENDING':null):(outcome?.errorCode||'DELIVERY_UNKNOWN')};
      return {...value,taskProgressAutoReview:all};
    });
    return dispatchState;
  } catch(error) {
    // A reserved batch remains reserved if KV itself failed, so a later
    // observation cannot silently dispatch the same batch a second time.
    let readRetry=false;
    if(!reservationId)try {
      await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},latest=all[hostSessionId];
        if(latest?.enabled!==true)return null;
        const retries=(latest.retry_count||0)+1;readRetry=retries<=3;
        all[hostSessionId]={...latest,retry_count:retries,retry_after:readRetry?Date.now()+12000:null,
          last_error:readRetry?'QUEUE_UNAVAILABLE':'RETRY_EXHAUSTED'};
        return {...value,taskProgressAutoReview:all};
      });
    }catch(_) { readRetry=false; /* KV failure cannot authorize dispatch. */ }
    return readRetry?'read_retry':'unavailable';
  } finally { reviewDispatchInFlight.delete(hostSessionId);if(!keepHostWaiter)reviewHostWaiters.delete(hostSessionId); }
}

// Recovery never infers that a model turn was delivered or that review passed.
// It only releases an uncertain reservation once the exact obligations no
// longer exist in a complete, revision-pinned server queue.
async function reconcileUncertainReviews(hostSessionId, includeDispatched=false) {
  if(typeof hostSessionId!=='string' || !hostSessionId || reviewDispatchInFlight.has(hostSessionId))return 'busy';
  try {
    await kvWriteQueue;const response=await fetch('/kv');if(!response.ok)return 'recovery_unavailable';
    const state=await response.json(),config=state?.taskProgressAutoReview?.[hostSessionId];
    const sessionId=state?.taskProgressSessionsByHostSession?.[hostSessionId],context=state?.taskProgressContextsByHostSession?.[hostSessionId];
    if(!sessionId || config?.session_id!==sessionId || context?.session_id!==sessionId || !context.task_instance_id)return 'binding_changed';
    const reservation=config.reservation;
    if(reservation && (reservation.session_id!==sessionId || typeof reservation.task_instance_id!=='string' || !reservation.task_instance_id || !Array.isArray(reservation.keys)))return 'recovery_required';
    const ledger=config.ledger||{},allUncertain=Object.keys(ledger).filter(key=>ledger[key]?.state==='uncertain');
    const allDispatched=includeDispatched?[...Object.keys(ledger).filter(key=>ledger[key]?.state==='dispatched'),...Object.keys(ledger).filter(key=>ledger[key]?.state==='review_pending')]:[];
    if([...allUncertain,...allDispatched].some(key=>ledger[key].session_id!==sessionId || typeof ledger[key].task_instance_id!=='string' || !ledger[key].task_instance_id))return 'recovery_required';
    const targetId=reservation?.task_instance_id || ledger[allUncertain[0]]?.task_instance_id || ledger[allDispatched[0]]?.task_instance_id;
    if(!targetId)return 'clear';
    if(targetId!==context.task_instance_id) {
      const discovered=await requestTaskTool('cindy_review_instances',{session_id:sessionId,task_instance_id:context.task_instance_id,include_settled:true});
      const instances=discovered?.result?.structuredContent;
      if(!discovered?.ok || discovered.result?.isError || instances?.session_id!==sessionId || instances?.task_instance_id!==context.task_instance_id || !Array.isArray(instances.instances) || !instances.instances.includes(targetId))return 'recovery_required';
    }
    const uncertainKeys=allUncertain.filter(key=>ledger[key].task_instance_id===targetId);
    const dispatchedKeys=allDispatched.filter(key=>ledger[key].task_instance_id===targetId);
    if(reservation?.keys.some(key=>ledger[key] && (ledger[key].session_id!==sessionId || ledger[key].task_instance_id!==targetId)))return 'recovery_required';
    const critical=[...new Set([...(reservation?.keys||[]),...uncertainKeys])];
    if(critical.length>25)return 'recovery_required';
    const keys=[...new Set([...critical,...dispatchedKeys])].slice(0,25);
    if(!keys.length)return 'clear';
    if(keys.length>25 || keys.some(key=>typeof key!=='string' || !/^[a-f0-9]{64}$/.test(key)))return 'recovery_required';
    const result=await requestTaskTool('cindy_review_reconcile',{session_id:sessionId,task_instance_id:targetId,observe_keys:keys});
    const facts=result?.result?.structuredContent;
    if(!result?.ok || result.result?.isError)return 'recovery_unavailable';
    if(facts?.session_id!==sessionId || facts?.task_instance_id!==targetId || !Array.isArray(facts.observed_keys) || facts.observed_keys.some(key=>!keys.includes(key)))return 'recovery_required';
    const present=new Set(facts.observed_keys);
    // All-positive observations are enough to mark a dispatched obligation
    // still pending, even when the rest of a very large queue was not read.
    // Absence, including partial absence, still requires a complete scan.
    if(facts.observation_complete!==true && keys.some(key=>!present.has(key)))return 'recovery_required';
    const removed=keys.filter(key=>!present.has(key));
    const overdue=dispatchedKeys.filter(key=>keys.includes(key) && present.has(key) && (ledger[key].state==='review_pending' || (Number.isFinite(ledger[key].at) && Date.now()-ledger[key].at>=REVIEW_WATCHDOG_MS)));
    if(!removed.length && !overdue.length){
      if(uncertainKeys.length || reservation)return 'recovery_required';
      if(config.last_error==='REVIEW_PENDING' && !Object.values(ledger).some(item=>item?.state==='review_pending'))await updatePluginKv(value=>{
        const all={...(value.taskProgressAutoReview||{})},latest=all[hostSessionId];
        if(latest?.session_id!==sessionId || latest.last_error!=='REVIEW_PENDING' || value.taskProgressSessionsByHostSession?.[hostSessionId]!==sessionId || value.taskProgressContextsByHostSession?.[hostSessionId]?.task_instance_id!==context.task_instance_id || latest.reservation || Object.values(latest.ledger||{}).some(item=>item?.state==='review_pending' || item?.state==='uncertain'))return null;
        all[hostSessionId]={...latest,last_error:null};return {...value,taskProgressAutoReview:all};
      });
      return 'awaiting_completion';
    }
    let status='recovery_required';
    await updatePluginKv(value=>{
      const all={...(value.taskProgressAutoReview||{})},latest=all[hostSessionId];
      if(latest?.session_id!==sessionId || value.taskProgressSessionsByHostSession?.[hostSessionId]!==sessionId || value.taskProgressContextsByHostSession?.[hostSessionId]?.task_instance_id!==context.task_instance_id || JSON.stringify(latest.reservation||null)!==JSON.stringify(reservation||null))return null;
      const nextLedger={...(latest.ledger||{})};
      for(const key of keys)if(ledger[key] && JSON.stringify(nextLedger[key])!==JSON.stringify(ledger[key]))return null;
      for(const key of removed)nextLedger[key]={state:'no_longer_due',at:Date.now(),session_id:sessionId,task_instance_id:targetId};
      for(const key of overdue)nextLedger[key]={...nextLedger[key],state:'review_pending'};
      const remaining=reservation?.keys?.filter(key=>present.has(key))||[];
      const nextReservation=remaining.length?{...reservation,keys:remaining}:null;
      const stillUncertain=Object.values(nextLedger).some(item=>item?.state==='uncertain');
      const stillDispatched=Object.values(nextLedger).some(item=>item?.state==='dispatched');
      const stillPending=Object.values(nextLedger).some(item=>item?.state==='review_pending');
      status=nextReservation || stillUncertain ? 'recovery_required' : stillDispatched ? 'awaiting_completion' : stillPending ? 'review_pending' : 'cleared';
      all[hostSessionId]={...latest,ledger:nextLedger,reservation:nextReservation,last_error:['cleared','awaiting_completion'].includes(status)?null:status==='review_pending'?'REVIEW_PENDING':latest.last_error};
      return {...value,taskProgressAutoReview:all};
    });
    return status;
  } catch (_) {return 'recovery_unavailable';}
}

function scheduleReviewWatchdog(hostSessionId, restart=false, delayMs=REVIEW_WATCHDOG_MS) {
  if(reviewWatchdogTimers.has(hostSessionId)) {
    if(!restart)return;
    clearTimeout(reviewWatchdogTimers.get(hostSessionId));reviewWatchdogTimers.delete(hostSessionId);
  }
  const timer=setTimeout(async()=>{
    reviewWatchdogTimers.delete(hostSessionId);
    const settingsGeneration=reviewSettingsGeneration.get(hostSessionId)||0;
    const stillCurrent=()=>settingsGeneration===(reviewSettingsGeneration.get(hostSessionId)||0);
    const retryRead=()=>{
      if(!stillCurrent())return;
      const count=(reviewRecoveryRetries.get(hostSessionId)||0)+1;reviewRecoveryRetries.set(hostSessionId,count);
      panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:count<=3?'recovery_retry':'recovery_unavailable'});
      if(count<=3)scheduleReviewWatchdog(hostSessionId,false,12500);
    };
    try {
      const response=await fetch('/kv');
      if(!response.ok){retryRead();return;}
      if((await response.json())?.taskProgressAutoReview?.[hostSessionId]?.enabled!==true || !stillCurrent())return;
    } catch (_) {retryRead();return;}
    const status=await reconcileUncertainReviews(hostSessionId,true);
    // Opt-out may race with the read; do not schedule additional background
    // polling or show stale automatic-follow-up notices after it completes.
    try {
      const response=await fetch('/kv');
      if(!response.ok){retryRead();return;}
      if((await response.json())?.taskProgressAutoReview?.[hostSessionId]?.enabled!==true || !stillCurrent())return;
    } catch (_) {retryRead();return;}
    if(status==='recovery_unavailable') {
      retryRead();
    } else if(status==='busy'){scheduleReviewWatchdog(hostSessionId,false,12500);}
    else if(status==='awaiting_completion'){reviewRecoveryRetries.delete(hostSessionId);scheduleReviewWatchdog(hostSessionId);}
    else if(['review_pending','recovery_required','cleared','clear'].includes(status)) {
      reviewRecoveryRetries.delete(hostSessionId);
      panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:['clear','cleared'].includes(status)?'recovered':status});
      if(['review_pending','clear','cleared'].includes(status))scheduleDueReviews(hostSessionId);
    }
  },delayMs);
  if(timer && typeof timer.unref==='function')timer.unref();
  reviewWatchdogTimers.set(hostSessionId,timer);
}

function scheduleDueReviews(hostSessionId, delayMs=100) {
  if(typeof hostSessionId!=='string' || !hostSessionId)return;
  if(reviewDispatchInFlight.has(hostSessionId)){reviewDispatchDirty.add(hostSessionId);return;}
  if(reviewDispatchTimers.has(hostSessionId))return;
  const timer=setTimeout(async()=>{
    reviewDispatchTimers.delete(hostSessionId);
    const settingsGeneration=reviewSettingsGeneration.get(hostSessionId)||0;
    const retrySettingsRead=()=>{
      if(settingsGeneration!==(reviewSettingsGeneration.get(hostSessionId)||0))return;
      panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:'recovery_retry'});
      scheduleReviewWatchdog(hostSessionId,true,12500);
    };
    let result=await dispatchDueReviews(hostSessionId);
    if(result==='recovery_required' || result==='awaiting_completion') {
      const recovered=await reconcileUncertainReviews(hostSessionId,true);
      if(['clear','cleared'].includes(recovered)){result='recovered';reviewDispatchDirty.add(hostSessionId);}
      else {if(recovered==='review_pending' && result==='awaiting_completion')reviewDispatchDirty.add(hostSessionId);result=recovered;}
    }
    // A user opt-out can arrive while a queue or host request is in flight.
    // Keep its durable receipt, but do not display notices or queue retries.
    {
      try {
        const response=await fetch('/kv');if(!response.ok){if(result!=='disabled')retrySettingsRead();return;}
        const config=(await response.json())?.taskProgressAutoReview?.[hostSessionId];
        if(settingsGeneration!==(reviewSettingsGeneration.get(hostSessionId)||0))return;
        if(result==='consent_required') {
          if(config?.enabled===true || config?.last_error!=='CONSENT_REQUIRED')return;
        } else if(config?.enabled!==true)return;
        if(result!=='consent_required' && (reviewSuspendedHosts.has(hostSessionId) || config.host_suspended===true)) {
          result=reviewGapHosts.has(hostSessionId) || config.suspension_reason==='EVENT_GAP' ? 'event_gap' : 'host_suspended';
          reviewDispatchDirty.delete(hostSessionId);
        }
      }
      catch(_){if(result!=='disabled')retrySettingsRead();return;}
    }
    if(!['disabled','skipped','superseded','scan_more','host_busy'].includes(result))
      panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:result});
    const dirty=reviewDispatchDirty.delete(hostSessionId);
    if(result==='dispatched' || result==='dispatched_with_pending')scheduleReviewWatchdog(hostSessionId,true);
    else if(result==='awaiting_completion')scheduleReviewWatchdog(hostSessionId);
    else if(['recovery_unavailable','unavailable'].includes(result))scheduleReviewWatchdog(hostSessionId,true,12500);
    else if(['empty','incomplete','review_pending'].includes(result))scheduleReviewWatchdog(hostSessionId);
    if(result==='rate_limited' || result==='read_retry' || result==='host_busy') scheduleDueReviews(hostSessionId,12500);
    else if(result==='queue_changed')scheduleDueReviews(hostSessionId,1100);
    else if(result==='scan_more')scheduleDueReviews(hostSessionId,100);
    else if(dirty && !['retry_exhausted','recovery_required','awaiting_completion'].includes(result)) scheduleDueReviews(hostSessionId);
  },delayMs);
  reviewDispatchTimers.set(hostSessionId,timer);
}

function observeReviewHostActivity(msg) {
  const host=msg.data?.sessionId,state=msg.data?.state,activity=state?.activity;
  if(typeof host!=='string' || !host || !state || typeof state!=='object')return;
  const stopped=['archived','deleted'].includes(state.status) || ['archived','deleted'].includes(activity?.recordStatus) ||
    (typeof activity?.gracefulStopState==='string' && activity.gracefulStopState!=='none') ||
    ['needs-interaction','error'].includes(activity?.phase);
  if(stopped) {
    reviewHostWaiters.delete(host);
    const generation=(reviewHostActivityGeneration.get(host)||0)+1;
    reviewHostActivityGeneration.set(host,generation);
    reviewSuspendedHosts.add(host);reviewDispatchDirty.delete(host);
    if(reviewDispatchTimers.has(host)){clearTimeout(reviewDispatchTimers.get(host));reviewDispatchTimers.delete(host);}
    void updatePluginKv(value=>{
      const all={...(value.taskProgressAutoReview||{})},config=all[host];
      if(!config || config.host_suspended===true)return null;
      all[host]={...config,host_suspended:true};return {...value,taskProgressAutoReview:all};
    }).then(value=>{if(reviewHostActivityGeneration.get(host)===generation && value?.taskProgressAutoReview?.[host]?.enabled===true)panelBus.postMessage({type:'auto-review-state',host_session_id:host,status:'host_suspended'});}).catch(()=>{});
  } else if(activity?.phase==='running' && activity.currentTurnActive===true && activity.gracefulStopState==='none' && (activity.recordStatus==='active' || state.status==='active')) {
    if(reviewGapHosts.has(host) && !Number.isSafeInteger(msg.seq))return;
    const generation=(reviewHostActivityGeneration.get(host)||0)+1;
    reviewHostActivityGeneration.set(host,generation);
    // A confirmed new active turn is the only automatic resume signal. Persist
    // the change so a stale stop survives plugin restart until that signal.
    let mayClear=false;
    void updatePluginKv(value=>{
      const all={...(value.taskProgressAutoReview||{})},config=all[host];
      if(config?.suspension_reason==='EVENT_GAP' && !Number.isSafeInteger(msg.seq))return null;
      if(config?.suspension_reason==='EVENT_GAP' && Number.isSafeInteger(config.suspension_reset_after) && (!Number.isSafeInteger(msg.ts) || msg.ts<=config.suspension_reset_after))return null;
      if(config?.suspension_reason==='EVENT_GAP' && Number.isSafeInteger(config.suspension_event_seq) && msg.seq<=config.suspension_event_seq)return null;
      mayClear=true;
      if(!config || config.host_suspended!==true)return null;
      all[host]={...config,host_suspended:false,suspension_reason:null,suspension_event_seq:null,suspension_reset_after:null};return {...value,taskProgressAutoReview:all};
    }).then(value=>{
      if(!mayClear || reviewHostActivityGeneration.get(host)!==generation)return;
      reviewSuspendedHosts.delete(host);reviewGapHosts.delete(host);
      // A live active event also restarts scanning after a plugin restart,
      // even when no suspension was persisted and no switch event arrives.
      if(value?.taskProgressAutoReview?.[host]?.enabled===true)scheduleDueReviews(host);
    }).catch(()=>{});
  }
}

// A dropped host event may have contained a stop or a permission transition.
// Suspend the affected session until a later, complete active-state event.
async function suspendReviewOnEventGap(msg) {
  const host=msg.data?.sessionId;
  if(!['did-task-state-changed','did-message-created','did-session-switched'].includes(msg.name) ||
     !Number.isSafeInteger(msg.dropped) || msg.dropped<=0 || typeof host!=='string' || !host)return false;
  reviewSuspendedHosts.add(host);reviewDispatchDirty.delete(host);
  reviewHostWaiters.delete(host);
  reviewGapHosts.add(host);const generation=(reviewHostActivityGeneration.get(host)||0)+1;reviewHostActivityGeneration.set(host,generation);
  if(reviewDispatchTimers.has(host)){clearTimeout(reviewDispatchTimers.get(host));reviewDispatchTimers.delete(host);}
  try {
    const current=await updatePluginKv(value=>{
      const all={...(value.taskProgressAutoReview||{})},config=all[host];
      if(!config)return null;
      const previous=Number.isSafeInteger(config.suspension_event_seq)?config.suspension_event_seq:null;
      const observed=Number.isSafeInteger(msg.seq) && msg.seq>=0?msg.seq:null;
      const gapSeq=observed===null?previous:previous===null?observed:Math.max(previous,observed);
      all[host]={...config,host_suspended:true,suspension_reason:'EVENT_GAP',suspension_event_seq:gapSeq,suspension_reset_after:null};
      return {...value,taskProgressAutoReview:all};
    });
    if(reviewHostActivityGeneration.get(host)===generation && current?.taskProgressAutoReview?.[host]?.enabled===true)
      panelBus.postMessage({type:'auto-review-state',host_session_id:host,status:'event_gap'});
  } catch (_) { /* In-memory suspension remains until a fresh activity event. */ }
  return true;
}

function staleReviewHostEvent(msg) {
  const host=msg.data?.sessionId;
  if(!['did-task-state-changed','did-message-created','did-session-switched'].includes(msg.name) ||
     typeof host!=='string' || !host || !Number.isSafeInteger(msg.seq) || msg.seq<0)return false;
  const last=reviewHostEventSequence.get(host);
  if(Number.isSafeInteger(last) && msg.seq<=last)return true;
  reviewHostEventSequence.set(host,msg.seq);
  return false;
}

async function schedulePanelReview(sessionId) {
  try {
    const response=await fetch('/kv');if(!response.ok)return;
    const state=await response.json(),host=state?.taskProgressActiveHostSessionId;
    if(host && state.taskProgressSessionsByHostSession?.[host]===sessionId) scheduleDueReviews(host);
  } catch (_) { /* A missing binding cannot authorize background work. */ }
}

panelBus.onmessage = async function (event) {
  const request = event.data;
  if (request?.type === 'panel-refresh-result') {
    try { await recordPanelRefresh(request); } catch (_) { /* Diagnostics are advisory. */ }
    return;
  }
  if (request?.type === 'panel-tool-request') {
    if (typeof request.requestId !== 'string' || !request.requestId || typeof request.name !== 'string' || !request.name) {
      panelBus.postMessage({ type: 'panel-tool-error', requestId: request.requestId, message: '无效的面板工具请求。' });
      return;
    }
    let call = panelToolCalls.get(request.requestId);
    if (!call) {
      call = (async () => {
        try {
          const response = await requestTaskTool(request.name, request.arguments || {});
          if (!response?.ok) throw new Error(response?.message || 'Task Progress 服务调用失败。');
          return { ok: true, result: response.result };
        } catch (error) {
          return { ok: false, message: String(error.message || error) };
        }
      })();
      panelToolCalls.set(request.requestId, call);
    }
    try {
      const response = await call;
      if (response.ok) {
        panelBus.postMessage({ type: 'panel-tool-result', requestId: request.requestId, result: response.result });
        if(REVIEW_TRIGGER_TOOLS.has(request.name) && !response.result?.isError && !response.result?.result?.isError)
          void schedulePanelReview(request.arguments?.session_id);
      }
      else panelBus.postMessage({ type: 'panel-tool-error', requestId: request.requestId, message: response.message });
    } catch (error) {
      panelBus.postMessage({ type: 'panel-tool-error', requestId: request.requestId, message: String(error.message || error) });
    }
    while (panelToolCalls.size > 100) panelToolCalls.delete(panelToolCalls.keys().next().value);
    return;
  }
  if (!request || request.type !== 'refresh') return;
  if (typeof request.session_id !== 'string' || !request.session_id) {
    panelBus.postMessage({ type: 'refresh-error', requestId: request.requestId, message: '面板没有可查询的任务会话。' });
    return;
  }
  try {
    const response = await cindy.node.request({
      method: 'tools/call',
      params: { name: 'get_progress', arguments: { session_id: request.session_id, view: 'overview' } },
      timeoutMs: 30000
    });
    if (!response.ok) throw new Error(response.message || 'Task Progress 查询失败');
    panelBus.postMessage({ type: 'update', requestId: request.requestId, result: response.result, sentAt: Date.now() });
  } catch (error) {
    panelBus.postMessage({ type: 'refresh-error', requestId: request.requestId, message: String(error.message || error) });
  }
};

cindy.onHostMessage(async function (msg) {
  const staleReviewEvent=msg.type==='event' ? staleReviewHostEvent(msg) : false;
  const hadEventGap=msg.type==='event' ? await suspendReviewOnEventGap(msg) : false;
  if(msg.type==='event' && msg.name==='card-action' && ['auto-review-enable','auto-review-disable'].includes(msg.actionId)) {
    const host=msg.sessionId;
    try {
      if(typeof host!=='string' || !host || typeof msg.userActionToken!=='string' || !msg.userActionToken)throw new Error('用户点击凭据或会话身份不可用。');
      let enabled=false,waitingForHost=false,waitingForGap=false;
      await updatePluginKv(current=>{
        const sid=current.taskProgressSessionsByHostSession?.[host],context=current.taskProgressContextsByHostSession?.[host];
        if(!sid || context?.session_id!==sid || !context.task_instance_id)throw new Error('当前会话尚未绑定任务，请先打开任务进度。');
        const all={...(current.taskProgressAutoReview||{})},prior=all[host]||{};
        if(prior.session_id && prior.session_id!==sid && (prior.reservation || Object.keys(prior.ledger||{}).length))throw new Error('旧绑定存在派发记录，请先核对后再启用。');
        enabled=msg.actionId==='auto-review-enable';
        const retained=prior.session_id===sid?prior:{};
        waitingForHost=enabled && retained.host_suspended===true;
        waitingForGap=waitingForHost && retained.suspension_reason==='EVENT_GAP';
        all[host]={...retained,enabled,session_id:sid,
          ...(enabled && retained.host_suspended===true && retained.suspension_reason==='EVENT_GAP'
            ? {suspension_event_seq:null,suspension_reset_after:Date.now()} : {}),
          ...((enabled && ['RATE_LIMITED','RETRY_EXHAUSTED','CONSENT_REQUIRED'].includes(retained.last_error)) || (!enabled && retained.last_error==='CONSENT_REQUIRED')
            ? {retry_count:0,retry_after:null,last_error:null} : {})};
        return {...current,taskProgressAutoReview:all};
      });
      reviewSettingsGeneration.set(host,(reviewSettingsGeneration.get(host)||0)+1);
      await cindy.send({type:'card-update',callId:msg.callId,v:2,height:112,html:enabled?`<div style="padding:12px"><strong>自动验收跟进已启用</strong><p>${waitingForHost?'等待会话恢复运行后继续检查。':'出现待复核或可综合验收时，会安排独立检查；不会自动标记通过。'}</p><button data-ghost-action="auto-review-disable">关闭自动跟进</button></div>`:'<div style="padding:12px"><strong>自动验收跟进已关闭</strong><p>已排队的验收回合不受此操作影响。</p><button data-ghost-action="auto-review-enable">重新启用</button></div>'});
      reviewRecoveryRetries.delete(host);
      if(enabled){reviewHostAssociations.add(host);panelBus.postMessage({type:'auto-review-state',host_session_id:host,status:'association_verified'});if(waitingForHost)panelBus.postMessage({type:'auto-review-state',host_session_id:host,status:waitingForGap?'event_gap':'host_suspended'});scheduleDueReviews(host);}
      else {reviewHostAssociations.delete(host);reviewHostWaiters.delete(host);panelBus.postMessage({type:'auto-review-state',host_session_id:host,status:'disabled'});if(reviewWatchdogTimers.has(host)){clearTimeout(reviewWatchdogTimers.get(host));reviewWatchdogTimers.delete(host);}}
    } catch(error) {
      await cindy.send({type:'card-update',callId:msg.callId,v:2,height:105,html:'<div style="padding:12px"><strong>未能更新自动跟进</strong><p>请打开当前任务进度后重试。</p><button data-ghost-action="auto-review-enable">重试启用</button></div>'});
    }
    return;
  }
  if (msg.type === 'event' && msg.name === 'will-compose-context') {
    await composeTaskProgressContext(msg);
    return;
  }
  if (msg.type === 'event' && msg.name === 'did-task-state-changed') {
    if(!hadEventGap && !staleReviewEvent)observeReviewHostActivity(msg);
    await observeRefreshEvent(msg);
    return;
  }
  if (msg.type === 'event' && msg.name === 'did-message-created') {
    // A saved assistant message can follow tool updates; its content is not
    // evidence that the Task Progress attempt has finished or passed review.
    await observeRefreshEvent(msg, msg.data?.role === 'assistant' &&
      typeof msg.data.messageId === 'string' && Boolean(msg.data.messageId));
    return;
  }
  if (msg.type === 'event' && msg.name === 'did-session-switched') {
    const hostSessionId = msg.data?.sessionId;
    let target = null;
    try { target = await recordHookReceipt(msg, { refresh: true }); } catch (_) {}
    let current = {};
    try { current = await fetch('/kv').then(r => r.ok ? r.json() : ({})); } catch (_) {}
    const sessionId = current.taskProgressSessionsByHostSession?.[hostSessionId] || null;
    const diagnostic = {
      kind: 'session-switch', mapped: Boolean(sessionId), hasHostSession: typeof hostSessionId === 'string' && Boolean(hostSessionId),
      at: Date.now()
    };
    try {
      await updatePluginKv(value => ({
        ...value,
        taskProgressActiveHostSessionId: typeof hostSessionId === 'string' ? hostSessionId : '',
        taskProgressPanelDiagnostic: diagnostic
      }));
    } catch (_) { /* The live panel still receives the session switch. */ }
    panelBus.postMessage({ type: 'bind-session', session_id: sessionId, diagnostic, ...(target || {}) });
    // A card action can change enablement while the switch is being handled.
    // Render and schedule from the latest state, not the earlier snapshot.
    try { current = await fetch('/kv').then(r => r.ok ? r.json() : ({})); } catch (_) { current={}; }
    if(current.taskProgressAutoReview?.[hostSessionId]?.enabled!==true && current.taskProgressAutoReview?.[hostSessionId]?.last_error==='CONSENT_REQUIRED')
      panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:'consent_required'});
    if(current.taskProgressAutoReview?.[hostSessionId]?.enabled===true && !reviewHostAssociations.has(hostSessionId))
      panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:'association_unverified'});
    const switchSettingsGeneration=reviewSettingsGeneration.get(hostSessionId)||0;
    if(current.taskProgressAutoReview?.[hostSessionId]?.enabled===true)
      void reconcileUncertainReviews(hostSessionId,true).then(async status=>{
        try {const latest=await fetch('/kv');if(!latest.ok)return;const config=(await latest.json())?.taskProgressAutoReview?.[hostSessionId];if(config?.enabled!==true || config.host_suspended===true || reviewSuspendedHosts.has(hostSessionId) || switchSettingsGeneration!==(reviewSettingsGeneration.get(hostSessionId)||0))return;}catch(_){return;}
        if(status==='recovery_required')panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status});
        else if(status==='recovery_unavailable'){panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:'recovery_retry'});scheduleReviewWatchdog(hostSessionId,true,12500);}
        else if(status==='review_pending'){panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status});scheduleDueReviews(hostSessionId);}
        else if(status==='awaiting_completion')scheduleReviewWatchdog(hostSessionId);
        else if(status==='clear' || status==='cleared'){if(status==='cleared')panelBus.postMessage({type:'auto-review-state',host_session_id:hostSessionId,status:'recovered'});scheduleDueReviews(hostSessionId);}
      });
    return;
  }
  if (msg.type !== 'tool-call') return;
  if(msg.tool==='configure_auto_review') {
    const host=msg.args?.session_context?.session_id;
    if(typeof host!=='string' || !host){await cindy.send({type:'tool-result',callId:msg.callId,ok:false,errorCode:'SESSION_CONTEXT_REQUIRED',message:'当前 Cindy 会话身份不可用。'});return;}
    await cindy.send({type:'card-update',callId:msg.callId,v:2,height:145,html:'<div style="padding:12px"><strong>自动验收跟进</strong><p>启用后，待复核或可综合验收时会安排本会话继续检查，可能产生额外模型调用；只依据证据验收，不自动通过。</p><button data-ghost-action="auto-review-enable">启用自动跟进</button><button data-ghost-action="auto-review-disable">关闭</button></div>'});
    await cindy.send({type:'tool-result',callId:msg.callId,ok:true,result:{content:[{type:'text',text:'请使用卡片选择是否启用自动验收跟进。'}],structuredContent:{status:'awaiting_user_action'}}});
    return;
  }
  if(msg.tool==='get_auto_review_status') {
    const host=msg.args?.session_context?.session_id;
    try {
      if(typeof host!=='string' || !host)throw new Error('当前 Cindy 会话身份不可用。');
      const reconciliation=await reconcileUncertainReviews(host,true);
      await kvWriteQueue;const response=await fetch('/kv');if(!response.ok)throw new Error('自动跟进状态不可读取。');
      const state=await response.json(),config=state?.taskProgressAutoReview?.[host]||{},entries=Object.values(config.ledger||{});
      const data={enabled:config.enabled===true,host_association_verified:reviewHostAssociations.has(host),host_suspended:reviewSuspendedHosts.has(host)||config.host_suspended===true,binding_current:config.session_id===state?.taskProgressSessionsByHostSession?.[host],reservation_pending:!!config.reservation,dispatched:entries.filter(x=>x.state==='dispatched').length,review_pending:entries.filter(x=>x.state==='review_pending').length,uncertain:entries.filter(x=>x.state==='uncertain').length,no_longer_due:entries.filter(x=>x.state==='no_longer_due').length,last_error:config.last_error||null,retry_after:config.retry_after||null,reconciliation};
      await cindy.send({type:'tool-result',callId:msg.callId,ok:true,result:{content:[{type:'text',text:JSON.stringify(data)}],structuredContent:data}});
    }catch(error){await cindy.send({type:'tool-result',callId:msg.callId,ok:false,errorCode:'AUTO_REVIEW_STATUS_UNAVAILABLE',message:String(error.message||error)});}
    return;
  }
  if (msg.tool === 'repair_session_binding') {
    try {
      const {host_session_id:host, expected_session_id:expected, session_id:sid, task_instance_id:iid} = msg.args || {};
      if (![host,expected,sid,iid].every(x=>typeof x==='string' && x)) throw new Error('绑定修复参数不完整。');
      if (msg.args?.session_context?.session_id !== host) throw new Error('只能修复当前 Cindy 会话的任务绑定。');
      const read = await requestTaskTool('get_progress', {session_id:sid,view:'overview'}, msg.callId);
      const view = read.result?.structuredContent || read.result?.result?.structuredContent;
      if (!read.ok || view?.session_id !== sid || view?.task_instance_id !== iid || view?.progress?.task_instance_id !== iid) throw new Error('目标任务实例无法核验，未修改绑定。');
      await updatePluginKv(current=>{
        if (current.taskProgressSessionsByHostSession?.[host] !== expected) throw new Error('会话绑定已变化，未执行修复。');
        return {...current,
          taskProgressSessionsByHostSession:{...current.taskProgressSessionsByHostSession,[host]:sid},
          taskProgressContextsByHostSession:{...current.taskProgressContextsByHostSession,[host]:{session_id:sid,task_instance_id:iid}},
          ...(current.taskProgressActiveHostSessionId===host?{taskProgressPanelSessionId:sid}:{})};
      });
      panelBus.postMessage({type:'bind-session',session_id:sid});
      await cindy.send({type:'tool-result',callId:msg.callId,ok:true,result:{content:[{type:'text',text:'会话绑定已更新。'}],structuredContent:{host_session_id:host,session_id:sid,task_instance_id:iid}}});
    } catch(error) { await cindy.send({type:'tool-result',callId:msg.callId,ok:false,errorCode:'TASK_PROGRESS_BINDING_FAILED',message:String(error.message || error)}); }
    return;
  }
  if (msg.tool === 'get_hook_diagnostics') {
    const hostSessionId = msg.args?.session_context?.session_id;
    if (typeof hostSessionId !== 'string' || !hostSessionId) {
      await cindy.send({ type: 'tool-result', callId: msg.callId, ok: false,
        errorCode: 'SESSION_CONTEXT_REQUIRED', message: '当前 Cindy 会话身份不可用，无法查看诊断回执。' });
      return;
    }
    try {
      await kvWriteQueue;
      const current = await fetch('/kv').then(r => r.ok ? r.json() : null);
      if (!current) throw new Error('诊断状态不可用');
      const diagnostics = current.taskProgressHookDiagnostics?.[hostSessionId] || { events: {}, pending: [] };
      const result = { content: [{ type: 'text', text: JSON.stringify(diagnostics) }],
        structuredContent: { schema_version: 1, scope: 'current_cindy_session',
          events: diagnostics.events || {}, pending_refresh_count: diagnostics.pending?.length || 0 } };
      await cindy.send({ type: 'tool-result', callId: msg.callId, ok: true, result });
    } catch (_) {
      await cindy.send({ type: 'tool-result', callId: msg.callId, ok: false,
        errorCode: 'HOOK_DIAGNOSTICS_UNAVAILABLE', message: 'Task Progress Hook 诊断回执暂不可读取。' });
    }
    return;
  }
  const initialize = msg.tool === 'init_task_network' || msg.tool === 'init_progress';
  const initHostSessionId = msg.args?.session_context?.session_id;
  const executeTool = async () => { try {
    const toolArgs = { ...(msg.args || {}) };
    const hostSessionId = toolArgs.session_context?.session_id;
    delete toolArgs.session_context;
    if (MUTATING_TASK_TOOLS.has(msg.tool) && hostSessionId && toolArgs.session_id) {
      await kvWriteQueue;
      const response = await fetch('/kv');
      if (!response.ok) throw new Error('无法核验当前任务绑定，请稍后重试。');
      const state = await response.json();
      const mapped = state?.taskProgressSessionsByHostSession?.[hostSessionId];
      if (mapped && mapped !== toolArgs.session_id) throw new Error('此任务已切换，请先读取当前会话绑定的任务进度，再更新任务；旧记录没有被修改。');
    }
    if (initialize && typeof hostSessionId === 'string' && hostSessionId) {
      // Reuse the task session belonging to this Cindy conversation, so the
      // service can archive the old task instead of allocating an orphan.
      // If the binding cannot be read, do not start a new unbound session.
      await kvWriteQueue;
      const state = await fetch('/kv').then(r => {
        if (!r.ok) throw new Error('无法读取当前会话绑定，请稍后重试。');
        return r.json();
      });
      if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error('当前会话绑定无效，请稍后重试。');
      const stored = state.taskProgressSessionsByHostSession?.[hostSessionId];
      const pending = unsavedHostBindings.get(hostSessionId);
      if (stored && pending && stored !== pending) throw new Error('当前会话绑定存在冲突，请先恢复插件状态。');
      const mapped = stored || pending;
      if (typeof mapped === 'string' && mapped) {
        if (toolArgs.session_id && toolArgs.session_id !== mapped) {
          throw new Error('当前会话已有任务进度，请复用原任务会话，避免遗漏历史任务。');
        }
        toolArgs.session_id = mapped;
      }
    }
    const response = await requestTaskTool(msg.tool, toolArgs, msg.callId);
    if (!response.ok) throw new Error(response.message || 'Task Progress service unavailable');
    if (response.result?.isError || response.result?.result?.isError) {
      if (initialize && hostSessionId) {
        const failure = response.result?.result || response.result;
        const message = (failure.content || []).filter(item => item.type === 'text').map(item => item.text).join('\n') || '任务初始化未完成，请检查任务状态。';
        panelBus.postMessage({ type: 'task-init-error', host_session_id: hostSessionId, message });
      }
      await cindy.send({ type: 'tool-result', callId: msg.callId, ok: true, result: response.result });
      return;
    }
    const resultData = response.result?.structuredContent || response.result?.result?.structuredContent || {};
    const sessionId = toolArgs.session_id || resultData.session_id || resultData.progress?.session_id;
    const taskInstanceId = resultData.task_instance_id || resultData.progress?.task_instance_id || toolArgs.task_instance_id;
    const diagnostic = {
      kind: 'tool-call', tool: msg.tool, hasHostSession: typeof hostSessionId === 'string' && Boolean(hostSessionId),
      mapped: typeof hostSessionId === 'string' && Boolean(hostSessionId) && typeof sessionId === 'string' && Boolean(sessionId),
      hasTaskSession: typeof sessionId === 'string' && Boolean(sessionId), at: Date.now()
    };
    if (typeof sessionId === 'string' && sessionId) {
      try {
        await updatePluginKv(current => {
        const sessions = { ...(current.taskProgressSessionsByHostSession || {}) };
        if (typeof hostSessionId === 'string' && hostSessionId) {
          // A late response for another task session must not steal this
          // conversation's binding or change its foreground task context.
          if (sessions[hostSessionId] && sessions[hostSessionId] !== sessionId) return null;
          sessions[hostSessionId] = sessionId;
        }
        const contexts = { ...(current.taskProgressContextsByHostSession || {}) };
        if (typeof hostSessionId === 'string' && hostSessionId) {
          if (msg.tool === 'clear_progress') delete contexts[hostSessionId];
          else if (typeof taskInstanceId === 'string' && taskInstanceId &&
                   (!resultData.session_id || resultData.session_id === sessionId) &&
                   (!resultData.task_instance_id || resultData.task_instance_id === taskInstanceId)) {
            const previous = contexts[hostSessionId];
            // Explicit reads and writes may target an isolated parallel root.
            // They must not redirect background follow-up away from the
            // foreground root. Only actual foreground transitions may switch.
            if (!previous || previous.session_id !== sessionId || previous.task_instance_id === taskInstanceId ||
                initialize || msg.tool === 'activate_request')
              contexts[hostSessionId] = { session_id: sessionId, task_instance_id: taskInstanceId };
          }
          const contextKeys = Object.keys(contexts);
          while (contextKeys.length > 100) delete contexts[contextKeys.shift()];
        }
        return { ...current, taskProgressSessionsByHostSession: sessions, taskProgressContextsByHostSession: contexts, taskProgressPanelSessionId: sessionId, taskProgressPanelDiagnostic: diagnostic };
        });
        if (initialize && hostSessionId) unsavedHostBindings.delete(hostSessionId);
      } catch (_) {
        if (initialize && hostSessionId) {
          unsavedHostBindings.set(hostSessionId, sessionId);
          const warning = { type: 'text', text: '任务已创建，但会话绑定暂未保存；请保留返回的会话 ID，恢复插件状态后再继续。' };
          if (Array.isArray(response.result?.content)) response.result.content.push(warning);
        }
      }
    } else {
      await writePanelState({ taskProgressPanelDiagnostic: diagnostic });
    }
    panelBus.postMessage({ type: 'panel-diagnostic', diagnostic });
    panelBus.postMessage({ type: 'update', tool: msg.tool, session_id: sessionId || null, result: response.result, sentAt: Date.now(), diagnostic });
    await cindy.send({ type: 'tool-result', callId: msg.callId, ok: true, result: response.result });
    if(REVIEW_TRIGGER_TOOLS.has(msg.tool) && hostSessionId) scheduleDueReviews(hostSessionId);
  } catch (error) {
    if (initialize && typeof initHostSessionId === 'string' && initHostSessionId) {
      panelBus.postMessage({ type: 'task-init-error', host_session_id: initHostSessionId, message: String(error.message || error) });
    }
    await cindy.send({ type: 'tool-result', callId: msg.callId, ok: false,
      errorCode: 'TASK_PROGRESS_CALL_FAILED', message: String(error.message || error) });
  }};
  if (initialize && typeof initHostSessionId === 'string' && initHostSessionId) {
    // Two initializations for the same conversation must not both observe an
    // empty binding and allocate different sessions. Other sessions stay independent.
    const previous = hostInitializationQueues.get(initHostSessionId) || Promise.resolve();
    const operation = previous.then(executeTool, executeTool);
    hostInitializationQueues.set(initHostSessionId, operation);
    try { await operation; } finally {
      if (hostInitializationQueues.get(initHostSessionId) === operation) hostInitializationQueues.delete(initHostSessionId);
    }
  } else await executeTool();
});
