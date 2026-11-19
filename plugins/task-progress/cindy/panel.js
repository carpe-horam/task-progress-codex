const appFrame = document.getElementById('progress-app');
const panelStatus = document.getElementById('panel-status');
const panelBus = new BroadcastChannel('task-progress-panel');
let sessionId = null;
let disposed = false;
let lastTheme = null;
let appInitialized = false;
let latestUpdate = null;
let sessionSyncTimer = null;
let sessionSyncBusy = false;
let sessionLoadFailed = false;
let taskStateRefreshTimer = null;
let pendingRefreshReceipts = [];
let toolRequestSequence = 0;
const pendingToolCalls = new Map();
setPanelStatus('正在连接任务界面…');
window.setTimeout(() => {
  if (!appInitialized && !disposed) setPanelStatus('任务界面连接超时，请关闭后重新打开面板。');
}, 10000);

function postToApp(message) {
  if (!disposed && appFrame.contentWindow) appFrame.contentWindow.postMessage(message, '*');
}

function acknowledgeRefresh(receipts, status, result) {
  const structured = result?.structuredContent || result?.result?.structuredContent || {};
  for (const receipt of receipts) {
    if (!receipt?.receipt_id || !receipt?.host_session_id || !receipt?.trigger) continue;
    panelBus.postMessage({ type: 'panel-refresh-result', receipt_id: receipt.receipt_id,
      host_session_id: receipt.host_session_id, trigger: receipt.trigger,
      session_id: receipt.session_id, status,
      ...(Number.isSafeInteger(structured.state_revision) ? { state_revision: structured.state_revision } : {}) });
  }
}

function setPanelStatus(text) {
  panelStatus.textContent = text || '';
  panelStatus.hidden = !text;
  // The embedded app starts with an empty-state placeholder. Do not show it
  // while a known session is still loading: it incorrectly says there is no task.
  appFrame.style.visibility = (/^(正在连接任务界面|任务界面已连接，正在同步会话|正在读取当前会话|已找到当前任务，正在读取进度|任务数据已读取，正在显示)/.test(text || '') || (sessionId && sessionLoadFailed)) ? 'hidden' : '';
}

function reply(id, result, error) {
  if (id === undefined || id === null) return;
  const message = { jsonrpc: '2.0', id };
  if (error) message.error = { code: -32000, message: String(error.message || error) };
  else message.result = result;
  postToApp(message);
}

function hostTheme() {
  const value = getComputedStyle(document.documentElement).colorScheme;
  if (value === 'light' || value === 'dark') return value;
  return matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function syncHostContext() {
  const theme = hostTheme();
  if (theme === lastTheme) return;
  lastTheme = theme;
  postToApp({
    jsonrpc: '2.0',
    method: 'ui/notifications/host-context-changed',
    params: { displayMode: 'inline', availableDisplayModes: ['inline'], theme }
  });
}

function applyPanelDiagnostic(diagnostic) {
  let text = '';
  if (diagnostic?.kind === 'session-switch') {
    text = !diagnostic.hasHostSession ? 'Cindy 未提供当前会话标识。'
      : diagnostic.mapped ? '' : '已收到 Cindy 会话切换，但该会话尚未关联 Task Progress 数据。';
  } else if (diagnostic?.kind === 'tool-call') {
    text = !diagnostic.hasHostSession ? '工具调用已到达面板桥，但没有宿主会话标识。'
      : !diagnostic.hasTaskSession ? '工具调用未返回 Task Progress 会话标识。'
        : diagnostic.mapped ? ''
          : '已读取任务进度，但宿主没有提供会话标识，无法安全绑定到右栏。';
  } else if (diagnostic?.kind === 'panel-error') {
    text = `侧栏同步失败：${diagnostic.message || '未知错误'}`;
  }
  if (text) setPanelStatus(text);
  postToApp({ jsonrpc: '2.0', method: 'ui/notifications/panel-diagnostic', params: { text } });
}

async function callTool(name, args) {
  const requestId = `panel-${Date.now()}-${++toolRequestSequence}`;
  const wake = await fetch('cindy-ghost://task-progress/wake');
  if (!wake.ok) throw new Error(`唤醒 Cindy 工具桥失败（HTTP ${wake.status}）。`);
  return new Promise((resolve, reject) => {
    const send = () => panelBus.postMessage({ type: 'panel-tool-request', requestId, name, arguments: args || {} });
    const retry = window.setInterval(send, 400);
    const timeout = window.setTimeout(() => {
      pendingToolCalls.delete(requestId);
      window.clearInterval(retry);
      reject(new Error('等待 Cindy 工具桥响应超时。'));
    }, 120000);
    pendingToolCalls.set(requestId, { resolve, reject, timeout, retry });
    send();
  });
}

async function syncSessionFromKV(receipt) {
  if (sessionSyncBusy) { acknowledgeRefresh([receipt], 'skipped_sync_busy'); return; }
  sessionSyncBusy = true;
  try {
    const response = await fetch('/kv');
    if (!response.ok) throw new Error(`读取 Cindy 插件状态失败（HTTP ${response.status}）`);
    const settings = await response.json();
    const activeHostSessionId = settings.taskProgressActiveHostSessionId;
    let nextSession = '';
    if (typeof activeHostSessionId === 'string' && activeHostSessionId) {
      nextSession = settings.taskProgressSessionsByHostSession?.[activeHostSessionId] || '';
    } else {
      nextSession = localStorage.getItem('task-progress-panel-session') || settings.taskProgressPanelSessionId || '';
    }
    const diagnostic = settings.taskProgressPanelDiagnostic || {
      kind: 'session-switch', hasHostSession: Boolean(activeHostSessionId), mapped: Boolean(nextSession)
    };
    if (!nextSession) setPanelStatus('当前 Cindy 会话尚未关联 Task Progress 任务。');
    if (nextSession === sessionId) {
      applyPanelDiagnostic(diagnostic);
      if (nextSession && sessionLoadFailed) {
        sessionId = null;
        bindSession(nextSession, diagnostic, receipt);
      } else acknowledgeRefresh([receipt], 'skipped_same_session');
      return;
    }
    bindSession(nextSession, diagnostic, receipt);
  } catch (error) {
    acknowledgeRefresh([receipt], 'read_failed');
    setPanelStatus(`读取任务会话失败：${String(error.message || error)}`);
    applyPanelDiagnostic({ kind: 'panel-error', message: String(error.message || error) });
  } finally {
    sessionSyncBusy = false;
  }
}

function bindSession(value, diagnostic, receipt) {
  const nextSession = typeof value === 'string' ? value : '';
  if (nextSession === sessionId) {
    applyPanelDiagnostic(diagnostic);
    acknowledgeRefresh([receipt], 'skipped_same_session');
    return;
  }
  sessionId = nextSession;
  applyPanelDiagnostic(diagnostic || (sessionId ? null : { kind: 'session-switch', hasHostSession: true, mapped: false }));
  if (sessionId) {
    sessionLoadFailed = false;
    try { localStorage.setItem('task-progress-panel-session', sessionId); } catch (_) {}
  } else {
    try { localStorage.removeItem('task-progress-panel-session'); } catch (_) {}
  }
  postToApp({ jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: { arguments: { session_id: sessionId } } });
  if (sessionId) {
    setPanelStatus('已找到当前任务，正在读取进度…');
    const requestedSession = sessionId;
    void callTool('get_progress_view', { session_id: requestedSession, include_history: false })
      .then(result => {
        if (sessionId !== requestedSession) { acknowledgeRefresh([receipt], 'skipped_session_changed'); return; }
        acknowledgeRefresh([receipt], 'read_ok', result);
        sessionLoadFailed = false;
        const structured = result?.structuredContent || {};
        setPanelStatus(structured.progress ? '任务数据已读取，正在显示…' : '已连接当前会话，但任务数据为空。');
        latestUpdate = { type: 'update', session_id: sessionId, result };
        applyUpdate(latestUpdate);
      })
      .catch(error => {
        if (sessionId !== requestedSession) { acknowledgeRefresh([receipt], 'skipped_session_changed'); return; }
        acknowledgeRefresh([receipt], 'read_failed');
        sessionLoadFailed = true;
        setPanelStatus(`读取任务失败：${String(error.message || error)}`);
        applyPanelDiagnostic({ kind: 'panel-error', message: String(error.message || error) });
        postToApp({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { isError: true, content: [{ type: 'text', text: error.message || '无法读取当前任务进度。' }] } });
      });
  } else acknowledgeRefresh([receipt], 'skipped_unmapped');
}

async function bootstrapSession() {
  await syncSessionFromKV();
  if (sessionSyncTimer === null) sessionSyncTimer = window.setInterval(() => { void syncSessionFromKV(); }, 5000);
}

function applyUpdate(message) {
  const result = message?.result;
  if (!result) return;
  const structured = result.structuredContent || result.result?.structuredContent || {};
  const updatedSession = message.session_id || structured.session_id || structured.progress?.session_id;
  if (updatedSession && updatedSession !== sessionId) return;
  if (typeof updatedSession === 'string' && updatedSession) {
    sessionId = updatedSession;
    applyPanelDiagnostic(null);
    try { localStorage.setItem('task-progress-panel-session', sessionId); } catch (_) {}
    postToApp({ jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: { arguments: { session_id: sessionId } } });
  }
  postToApp({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: result });
}

window.addEventListener('message', async event => {
  if (event.source !== appFrame.contentWindow || !event.data || event.data.jsonrpc !== '2.0') return;
  const message = event.data;
  if (message.method === 'ui/initialize') {
    setPanelStatus('任务界面已连接，正在同步会话…');
    syncHostContext();
    reply(message.id, {
      protocolVersion: '2026-01-26',
      hostCapabilities: {},
      hostContext: { displayMode: 'inline', availableDisplayModes: ['inline'], theme: lastTheme || hostTheme() }
    });
    return;
  }
  if (message.method === 'tools/call') {
    const name = message.params?.name;
    const args = message.params?.arguments || {};
    try { reply(message.id, await callTool(name, args)); }
    catch (error) { reply(message.id, null, error); }
    return;
  }
  if (message.method === 'ui/request-display-mode') {
    reply(message.id, { mode: 'inline' });
    syncHostContext();
    return;
  }
  if (message.method === 'ui/notifications/initialized') {
    appInitialized = true;
    setPanelStatus('正在读取当前会话…');
    await bootstrapSession();
    if (latestUpdate) applyUpdate(latestUpdate);
    return;
  }
  if (message.method === 'task-progress/rendered') {
    const renderedSession = message.params?.session_id;
    if (!renderedSession || renderedSession === sessionId) setPanelStatus('');
    return;
  }
  if (message.method === 'ui/resource-teardown') {
    reply(message.id, {});
    disposed = true;
  }
});

panelBus.onmessage = event => {
  const message = event.data || {};
  if (message.type === 'panel-tool-result' || message.type === 'panel-tool-error') {
    const pending = pendingToolCalls.get(message.requestId);
    if (!pending) return;
    pendingToolCalls.delete(message.requestId);
    window.clearTimeout(pending.timeout);
    window.clearInterval(pending.retry);
    if (message.type === 'panel-tool-error') pending.reject(new Error(message.message || 'Task Progress 服务调用失败。'));
    else pending.resolve(message.result);
  } else if (message.type === 'bind-session') {
    // Inline cards and delayed host events may refer to another conversation.
    // The active host-session mapping is the authority for the global panel.
    void syncSessionFromKV(message);
  } else if (message.type === 'refresh-mapped-session') {
    if (message.session_id !== sessionId || !sessionId) {
      acknowledgeRefresh([message], 'skipped_session_changed');
      return;
    }
    pendingRefreshReceipts.push(message);
    if (taskStateRefreshTimer !== null) return;
    taskStateRefreshTimer = window.setTimeout(() => {
      taskStateRefreshTimer = null;
      const receipts = pendingRefreshReceipts;
      pendingRefreshReceipts = [];
      const requestedSession = sessionId;
      if (!requestedSession || disposed) { acknowledgeRefresh(receipts, 'skipped_session_changed'); return; }
      void callTool('get_progress_view', { session_id: requestedSession, include_history: false })
        .then(result => {
          if (sessionId !== requestedSession || disposed) { acknowledgeRefresh(receipts, 'skipped_session_changed'); return; }
          acknowledgeRefresh(receipts, 'read_ok', result);
          latestUpdate = { type: 'update', session_id: requestedSession, result };
          if (appInitialized) applyUpdate(latestUpdate);
        }).catch(() => { acknowledgeRefresh(receipts, 'read_failed'); });
    }, 250);
  } else if (message.type === 'update' && message.result) {
    if (message.session_id && message.session_id !== sessionId) {
      void syncSessionFromKV();
      return;
    }
    latestUpdate = message;
    applyPanelDiagnostic(message.diagnostic);
    if (appInitialized) applyUpdate(message);
  } else if (message.type === 'panel-diagnostic') {
    applyPanelDiagnostic(message.diagnostic);
  } else if (message.type === 'refresh-error') {
    applyPanelDiagnostic({ kind: 'panel-error', message: message.message || '刷新失败' });
    latestUpdate = { result: { isError: true, content: [{ type: 'text', text: message.message || '刷新失败' }] } };
    if (appInitialized) applyUpdate(latestUpdate);
  }
};

matchMedia('(prefers-color-scheme: light)').addEventListener('change', syncHostContext);
new MutationObserver(syncHostContext).observe(document.head, { childList: true, subtree: true, attributes: true });
syncHostContext();

// The local app resource can load faster than this external script. Assign its
// URL only after every message handler is ready, or the app's one-shot
// ui/initialize request can race past the parent and leave the sidebar empty.
appFrame.setAttribute('src', 'core/panel-app.html?source=panel');
