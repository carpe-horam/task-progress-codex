const panelBus = new BroadcastChannel('task-progress-panel');
const panelToolCalls = new Map();

async function writePanelState(patch) {
  try {
    const current = await fetch('/kv').then(r => r.ok ? r.json() : ({})).catch(() => ({}));
    await fetch('/kv', { method: 'PUT', body: JSON.stringify({ ...current, ...patch }) });
    return true;
  } catch (_) { return false; }
}

panelBus.onmessage = async function (event) {
  const request = event.data;
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
  if (msg.type === 'event' && msg.name === 'did-session-switched') {
    const hostSessionId = msg.data?.sessionId;
    let current = {};
    try { current = await fetch('/kv').then(r => r.ok ? r.json() : ({})); } catch (_) {}
    const sessionId = current.taskProgressSessionsByHostSession?.[hostSessionId] || null;
    const diagnostic = {
      kind: 'session-switch', mapped: Boolean(sessionId), hasHostSession: typeof hostSessionId === 'string' && Boolean(hostSessionId),
      at: Date.now()
    };
    try {
      await fetch('/kv', { method: 'PUT', body: JSON.stringify({
        ...current,
        taskProgressActiveHostSessionId: typeof hostSessionId === 'string' ? hostSessionId : '',
        taskProgressPanelDiagnostic: diagnostic
      }) });
    } catch (_) { /* The live panel still receives the session switch. */ }
    panelBus.postMessage({ type: 'bind-session', session_id: sessionId, diagnostic });
    return;
  }
  if (msg.type !== 'tool-call') return;
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
    const diagnostic = {
      kind: 'tool-call', tool: msg.tool, hasHostSession: typeof hostSessionId === 'string' && Boolean(hostSessionId),
      mapped: typeof hostSessionId === 'string' && Boolean(hostSessionId) && typeof sessionId === 'string' && Boolean(sessionId),
      hasTaskSession: typeof sessionId === 'string' && Boolean(sessionId), at: Date.now()
    };
    if (typeof sessionId === 'string' && sessionId) {
      try {
        const current = await fetch('/kv').then(r => r.ok ? r.json() : ({})).catch(() => ({}));
        const sessions = { ...(current.taskProgressSessionsByHostSession || {}) };
        if (typeof hostSessionId === 'string' && hostSessionId) {
          sessions[hostSessionId] = sessionId;
          const keys = Object.keys(sessions);
          while (keys.length > 100) delete sessions[keys.shift()];
        }
        await fetch('/kv', { method: 'PUT', body: JSON.stringify({ ...current, taskProgressSessionsByHostSession: sessions, taskProgressPanelSessionId: sessionId, taskProgressPanelDiagnostic: diagnostic }) });
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
