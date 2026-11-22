const appFrame = document.getElementById('progress-app');
const panelStatus = document.getElementById('panel-status');
const panelBus = new BroadcastChannel('task-progress-panel');
let sessionId = null;
let activeHostSessionId = null;
const autoReviewStates = new Map();
const unverifiedReviewAssociations = new Set();
const unconfirmedReviewStops = new Set();
let sessionBinding = 0;
let disposed = false;
let lastTheme = null;
let appInitialized = false;
let latestUpdate = null;
let sessionSyncTimer = null;
let sessionSyncBusy = false;
let sessionSyncQueued = false;
let sessionSyncQueuedReceipt = null;
let sessionLoadFailed = false;
let taskStateRefreshTimer = null;
let themeSyncTimer = null;
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
  // Present connection and loading feedback on one surface until the iframe is ready.
  appFrame.style.visibility = (/^(正在连接任务界面|任务界面已连接，正在同步会话|正在读取当前会话|已找到当前任务，正在读取进度|任务数据已读取，正在显示)/.test(text || '') || (sessionId && sessionLoadFailed)) ? 'hidden' : '';
}

function applyAutoReviewStatus(status) {
  const notices={consent_required:'自动验收跟进需要重新启用。',host_unsupported:'自动验收跟进暂不可用：当前宿主不支持后台派发。',
    uncertain:'自动验收派发结果待核实，已暂停以避免重复。',recovery_required:'自动验收派发记录待核实，已暂停以避免重复。',
    retry_exhausted:'自动验收多次无法核对或安排，请检查状态后重新启用。',rate_limited:'自动验收派发受限，稍后自动重试。',
    incomplete:'自动验收待办信息不完整，请查看任务详情。',unavailable:'自动验收待办暂不可读取，请稍后重试。',read_retry:'自动验收待办暂不可读取，稍后自动重试。',
    recovery_retry:'自动验收状态暂不可读取，将限次重试；不会重复派发。',recovery_unavailable:'自动验收状态读取失败，请检查后台后重试；不会重复派发。',disable_unconfirmed:'自动验收已在当前进程暂停；关闭设置尚未确认保存，请重试关闭。',
    binding_changed:'当前任务已切换，自动验收需重新核对并启用。',association_unverified:'自动验收的会话授权尚未确认，可能需要重新启用。',queue_changed:'验收待办正在变化，将重新核对。',event_gap:'会话事件有缺失，自动验收暂缓；可重新启用并恢复任务。',host_suspended:'当前会话已暂停、出错或等待处理，自动验收暂缓。',review_pending:'自动验收已安排，待办仍存在；可能正在排队或检查中。',dispatched_with_pending:'新的验收已安排；此前待办仍可能在排队或检查中。'};
  if(notices[status])setPanelStatus(notices[status]);
  else if(status==='disabled' || status==='association_verified' || status==='dispatched' ||
          (['empty','recovered'].includes(status) && !panelStatus.textContent.includes('会话授权尚未确认'))) {
    if(Object.values(notices).includes(panelStatus.textContent))setPanelStatus('');
  }
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
  if (disposed) return;
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
  // The ready, unmapped iframe presents the reason alongside its empty state.
  // Loading and bridge errors still belong to the outer status surface.
  if (text) setPanelStatus(appInitialized && !sessionId && diagnostic.kind === 'session-switch' ? '' : text);
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

async function readPanelSessionSettings() {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  let timer;
  try {
    const read = (async () => {
      const response = await fetch('/kv', controller ? {signal:controller.signal} : {});
      if (!response.ok) throw new Error(`读取 Cindy 插件状态失败（HTTP ${response.status}）`);
      return response.json();
    })();
    return await Promise.race([read, new Promise((_, reject) => {
      timer = window.setTimeout(() => {
        reject(new Error('读取 Cindy 插件状态超时'));
        controller?.abort();
      }, 1200);
    })]);
  } finally { window.clearTimeout(timer); }
}

async function syncSessionFromKV(receipt) {
  if (sessionSyncBusy) {
    sessionSyncQueued = true;
    if (receipt) {
      acknowledgeRefresh([sessionSyncQueuedReceipt], 'skipped_sync_busy');
      sessionSyncQueuedReceipt = receipt;
    }
    return;
  }
  sessionSyncBusy = true;
  try {
    const settings = await readPanelSessionSettings();
    if (sessionSyncQueued || disposed) { acknowledgeRefresh([receipt], 'skipped_session_changed'); return; }
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error('Cindy 插件状态格式无效');
    activeHostSessionId = settings.taskProgressActiveHostSessionId;
    if(typeof activeHostSessionId==='string' && activeHostSessionId)
      panelBus.postMessage({type:'panel-review-state-request',host_session_id:activeHostSessionId});
    if(autoReviewStates.has(activeHostSessionId))applyAutoReviewStatus(autoReviewStates.get(activeHostSessionId));
    else if(panelStatus.textContent.startsWith('自动验收'))setPanelStatus('');
    let nextSession = '';
    if (typeof activeHostSessionId === 'string' && activeHostSessionId) {
      nextSession = settings.taskProgressSessionsByHostSession?.[activeHostSessionId] || '';
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
    if (sessionSyncQueued || disposed) { acknowledgeRefresh([receipt], 'skipped_session_changed'); return; }
    acknowledgeRefresh([receipt], 'read_failed');
    setPanelStatus(`读取任务会话失败：${String(error.message || error)}`);
    applyPanelDiagnostic({ kind: 'panel-error', message: String(error.message || error) });
  } finally {
    sessionSyncBusy = false;
    if (sessionSyncQueued) {
      const nextReceipt = sessionSyncQueuedReceipt;
      sessionSyncQueued = false;
      sessionSyncQueuedReceipt = null;
      if (disposed) acknowledgeRefresh([nextReceipt], 'skipped_session_changed');
      else void syncSessionFromKV(nextReceipt);
    }
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
  const binding = ++sessionBinding;
  latestUpdate = null;
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
        if (binding !== sessionBinding || disposed) { acknowledgeRefresh([receipt], 'skipped_session_changed'); return; }
        acknowledgeRefresh([receipt], 'read_ok', result);
        sessionLoadFailed = false;
        const structured = result?.structuredContent || {};
        setPanelStatus(structured.progress ? '任务数据已读取，正在显示…' : '已连接当前会话，但任务数据为空。');
        latestUpdate = { type: 'update', session_id: sessionId, result };
        applyUpdate(latestUpdate);
      })
      .catch(error => {
        if (binding !== sessionBinding || disposed) { acknowledgeRefresh([receipt], 'skipped_session_changed'); return; }
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
    if (!renderedSession || renderedSession === sessionId) {
      setPanelStatus('');
      // Rendering and five-second polling must not hide a persistent
      // automatic-review authorization or recovery warning.
      if(autoReviewStates.has(activeHostSessionId))applyAutoReviewStatus(autoReviewStates.get(activeHostSessionId));
    }
    return;
  }
  if (message.method === 'ui/resource-teardown') {
    reply(message.id, {});
    disposed = true;
    stopThemeSync();
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
  } else if (message.type === 'task-init-error') {
    // Initializations from another Cindy conversation must not affect this panel.
    if (activeHostSessionId && message.host_session_id === activeHostSessionId) {
      setPanelStatus(`新任务未开始：${message.message || '请检查当前任务状态。'}`);
    }
  } else if (message.type === 'auto-review-state') {
    if(typeof message.host_session_id!=='string' || !message.host_session_id)return;
    const host=message.host_session_id;
    if(message.status==='disable_unconfirmed')unconfirmedReviewStops.add(host);
    if(['disabled','association_verified'].includes(message.status))unconfirmedReviewStops.delete(host);
    if(message.status==='association_unverified')unverifiedReviewAssociations.add(host);
    if(['disabled','association_verified','dispatched','dispatched_with_pending'].includes(message.status))unverifiedReviewAssociations.delete(host);
    const status=unconfirmedReviewStops.has(host) ? 'disable_unconfirmed' :
      ['empty','recovered'].includes(message.status) && unverifiedReviewAssociations.has(host) ? 'association_unverified' : message.status;
    autoReviewStates.set(host,status);
    while(autoReviewStates.size>100){const oldest=autoReviewStates.keys().next().value;autoReviewStates.delete(oldest);unverifiedReviewAssociations.delete(oldest);unconfirmedReviewStops.delete(oldest);}
    if(host===activeHostSessionId)applyAutoReviewStatus(status);
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
      const binding = sessionBinding;
      if (!requestedSession || disposed) { acknowledgeRefresh(receipts, 'skipped_session_changed'); return; }
      void callTool('get_progress_view', { session_id: requestedSession, include_history: false })
        .then(result => {
          if (binding !== sessionBinding || disposed) { acknowledgeRefresh(receipts, 'skipped_session_changed'); return; }
          acknowledgeRefresh(receipts, 'read_ok', result);
          latestUpdate = { type: 'update', session_id: requestedSession, result };
          if (appInitialized) applyUpdate(latestUpdate);
        }).catch(() => { acknowledgeRefresh(receipts, binding === sessionBinding && !disposed ? 'read_failed' : 'skipped_session_changed'); });
    }, 250);
  } else if (message.type === 'update' && message.result) {
    if (message.session_id && message.session_id !== sessionId) {
      void syncSessionFromKV();
      return;
    }
    if (message.tool === 'init_task_network' || message.tool === 'init_progress') setPanelStatus('');
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

const themeMedia = matchMedia('(prefers-color-scheme: light)');
const themeObserver = new MutationObserver(syncHostContext);
themeMedia.addEventListener('change', syncHostContext);
themeObserver.observe(document.head, { childList: true, subtree: true, attributes: true });
// Cindy injects its theme with webview.insertCSS(), which changes computed
// styles without a DOM mutation or OS media-query event. Sample that public
// style signal; syncHostContext only notifies the app when the value changes.
themeSyncTimer = window.setInterval(syncHostContext, 500);
function stopThemeSync() {
  window.clearInterval(themeSyncTimer);
  themeSyncTimer = null;
  themeMedia.removeEventListener('change', syncHostContext);
  themeObserver.disconnect();
}
window.addEventListener('pagehide', stopThemeSync, { once: true });
syncHostContext();

// The local app resource can load faster than this external script. Assign its
// URL only after every message handler is ready, or the app's one-shot
// ui/initialize request can race past the parent and leave the sidebar empty.
appFrame.setAttribute('src', 'core/panel-app.html?source=panel');
