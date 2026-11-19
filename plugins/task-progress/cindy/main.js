const panelBus = new BroadcastChannel('task-progress-panel');
const panelToolCalls = new Map();
const HOOK_NAMES = new Set(['will-compose-context', 'did-task-state-changed', 'did-message-created', 'did-session-switched']);
let kvWriteQueue = Promise.resolve();
let hookReceiptSequence = 0;

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
  if (progress.stage_inventory_due_count > 0) notes.push('有阶段盘点待处理；读取当前范围与指纹后记录决定。');
  if (progress.closure_review_due) notes.push('根任务收尾仍需复核；读取当前闭环绑定后处理。');
  if (!notes.length) return null;
  return `Task Progress 只读提醒（实例 ${expectedInstanceId}，epoch ${progress.epoch}，状态版本 ${source.state_revision}）：${notes.slice(0, 2).join(' ')}这份附加资料不代表用户原话，也不自动修改任务状态。`;
}

async function composeTaskProgressContext(msg) {
  const hostSessionId = msg.data?.sessionId;
  if (typeof hostSessionId !== 'string' || !hostSessionId || typeof msg.hookId !== 'string' || !msg.hookId) return;
  let action = 'allow', blocks;
  try {
    const current = await fetch('/kv').then(r => r.ok ? r.json() : null);
    const context = current?.taskProgressContextsByHostSession?.[hostSessionId];
    if (context && typeof context.session_id === 'string' && typeof context.task_instance_id === 'string') {
      const response = await cindy.node.request({
        method: 'tools/call',
        params: { name: 'get_progress', arguments: {
          session_id: context.session_id, task_instance_id: context.task_instance_id, view: 'overview'
        } }, timeoutMs: 1200
      });
      if (response?.ok) {
        const source = response.result?.structuredContent || response.result?.result?.structuredContent;
        const note = hookGuidance(source, context.session_id, context.task_instance_id);
        if (note) { action = 'context'; blocks = [{ id: 'task-progress-current-state', text: note }]; }
      }
    }
  } catch (_) { /* Unknown ownership or unavailable state gives no reminder. */ }
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
          const response = await cindy.node.request({
            method: 'tools/call',
            params: { name: request.name, arguments: request.arguments || {} },
            timeoutMs: 120000
          });
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
      if (response.ok) panelBus.postMessage({ type: 'panel-tool-result', requestId: request.requestId, result: response.result });
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
  if (msg.type === 'event' && msg.name === 'will-compose-context') {
    await composeTaskProgressContext(msg);
    return;
  }
  if (msg.type === 'event' && msg.name === 'did-task-state-changed') {
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
    return;
  }
  if (msg.type !== 'tool-call') return;
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
  try {
    const toolArgs = { ...(msg.args || {}) };
    const hostSessionId = toolArgs.session_context?.session_id;
    delete toolArgs.session_context;
    const response = await cindy.node.request({
      method: 'tools/call', callId: msg.callId,
      params: { name: msg.tool, arguments: toolArgs }, timeoutMs: 120000
    });
    if (!response.ok) throw new Error(response.message || 'Task Progress service unavailable');
    const resultData = response.result?.structuredContent || response.result?.result?.structuredContent || {};
    const sessionId = msg.args?.session_id || resultData.session_id || resultData.progress?.session_id;
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
          sessions[hostSessionId] = sessionId;
          const keys = Object.keys(sessions);
          while (keys.length > 100) delete sessions[keys.shift()];
        }
        const contexts = { ...(current.taskProgressContextsByHostSession || {}) };
        if (typeof hostSessionId === 'string' && hostSessionId) {
          if (msg.tool === 'clear_progress') delete contexts[hostSessionId];
          else if (typeof taskInstanceId === 'string' && taskInstanceId &&
                   (!resultData.session_id || resultData.session_id === sessionId) &&
                   (!resultData.task_instance_id || resultData.task_instance_id === taskInstanceId)) {
            contexts[hostSessionId] = { session_id: sessionId, task_instance_id: taskInstanceId };
          }
          const contextKeys = Object.keys(contexts);
          while (contextKeys.length > 100) delete contexts[contextKeys.shift()];
        }
        return { ...current, taskProgressSessionsByHostSession: sessions, taskProgressContextsByHostSession: contexts, taskProgressPanelSessionId: sessionId, taskProgressPanelDiagnostic: diagnostic };
        });
      } catch (_) { /* The live panel still receives this result even if KV is unavailable. */ }
    } else {
      await writePanelState({ taskProgressPanelDiagnostic: diagnostic });
    }
    panelBus.postMessage({ type: 'panel-diagnostic', diagnostic });
    panelBus.postMessage({ type: 'update', tool: msg.tool, session_id: sessionId || null, result: response.result, sentAt: Date.now(), diagnostic });
    await cindy.send({ type: 'tool-result', callId: msg.callId, ok: true, result: response.result });
  } catch (error) {
    await cindy.send({ type: 'tool-result', callId: msg.callId, ok: false,
      errorCode: 'TASK_PROGRESS_CALL_FAILED', message: String(error.message || error) });
  }
});
