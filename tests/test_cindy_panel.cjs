'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { createHost } = require('./browser_host.cjs');

const root = path.resolve(__dirname, '..', 'plugins/task-progress/cindy');
if (!fs.existsSync(path.join(root, 'core'))) {
  execFileSync('python3', [path.join(root, 'prepare.py')], { stdio: 'inherit' });
}
const mainAssignment = { execution_mode: 'main_session', self_execution_reason: 'Isolated Cindy panel display fixture' };
let host;
let server;
let currentKV;

async function main() {
  host = await createHost();
  const initialized = await host.rpc('init_task_network', {
    task_title: 'Cindy 端到端验证',
    tasks: [
      { id: 'stage', title: '验证 Task Progress 面板', assignment: mainAssignment },
      { id: 'poll', title: '进度自动刷新', assignment: mainAssignment }
    ]
  });
  const binding = {
    session_id: initialized.structuredContent.session_id,
    task_instance_id: initialized.structuredContent.progress.task_instance_id
  };
  currentKV = {
    taskProgressPanelSessionId: 'stale-session-from-another-chat',
    taskProgressActiveHostSessionId: 'cindy-session-current',
    taskProgressSessionsByHostSession: { 'cindy-session-current': binding.session_id }
  };
  await host.rpc('decompose_task', {
    ...binding,
    task_id: 'stage',
    tasks: [
      { id: 'bridge', title: '工具桥接与实时刷新', assignment: mainAssignment },
      { id: 'theme', title: '暗色主题与任务列表显示', assignment: mainAssignment }
    ]
  });

  const panelHTML = fs.readFileSync(path.join(root, 'panel.html'), 'utf8').replace('</head>', `<style>
:root { color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { color-scheme: dark; } }
</style></head>`);
  server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
    if (pathname === '/kv') {
      res.setHeader('content-type', 'application/json');
      return res.end(JSON.stringify(currentKV));
    }
    let file;
    if (pathname === '/' || pathname === '/panel.html') file = 'panel.html';
    else if (pathname === '/core/app.html') file = 'core/app.html';
    else file = pathname.slice(1);
    const allowed = ['panel.html', 'panel.js', 'panel.css', 'core/app.html', 'core/panel-app.html', 'core/panel-app.js'];
    if (!allowed.includes(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('content-type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    if (file === 'panel.html') return res.end(panelHTML);
    return res.end(fs.readFileSync(path.join(root, file)));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));

  const page = await host.page();
  await page.addInitScript(() => {
    localStorage.setItem('task-progress-panel-session', 'stale-session-from-another-chat');
    window.__bridgeStats = { wakes: 0, requests: 0, executions: 0, viewRegistrations: 0 };
    window.__wakeFailures = 2;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, ...args) => {
      if (String(input).startsWith('cindy-ghost://task-progress/wake')) {
        window.__bridgeStats.wakes++;
        if (window.__wakeFailures > 0) {
          window.__wakeFailures--;
          return Promise.resolve({ ok: false, status: 503 });
        }
        return Promise.resolve({ ok: true, status: 200 });
      }
      return nativeFetch(input, ...args);
    };
    const bridge = new BroadcastChannel('task-progress-panel');
    const requests = new Map();
    bridge.onmessage = async event => {
      const request = event.data;
      if (request?.type !== 'panel-tool-request') return;
      if (request.name === 'register_ui_view') window.__bridgeStats.viewRegistrations++;
      window.__bridgeStats.requests++;
      if (!requests.has(request.requestId)) {
        window.__bridgeStats.executions++;
        requests.set(request.requestId, (async () => {
          await new Promise(resolve => setTimeout(resolve, 500));
          try { return { ok: true, result: await window.hostRpc(request.name, request.arguments || {}) }; }
          catch (error) { return { ok: false, message: error.message }; }
        })());
      }
      const response = await requests.get(request.requestId);
      if (response.ok) bridge.postMessage({ type: 'panel-tool-result', requestId: request.requestId, result: response.result });
      else bridge.postMessage({ type: 'panel-tool-error', requestId: request.requestId, message: response.message });
    };
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/panel.html`);
  assert.equal(await page.locator('#progress-app').getAttribute('src'), 'core/panel-app.html?source=panel', 'panel starts the app only after registering its message bridge');
  await page.locator('#panel-status').filter({hasText:'侧栏同步失败'}).waitFor({timeout: 8000});
  assert.equal(await page.locator('#progress-app').evaluate(el => getComputedStyle(el).visibility), 'hidden', 'a failed read of a known session must not claim that it has no task');
  await page.waitForFunction(() => document.querySelector('#panel-status').textContent.includes('正在读取进度'), null, {timeout: 15000});
  assert.equal(await page.locator('#progress-app').evaluate(el => getComputedStyle(el).visibility), 'hidden', 'the uninitialized empty state must not claim that a known session has no task');
  await page.screenshot({path: path.join(__dirname, 'cindy-evidence/2026-11-18-loading-isolated.png')});
  const app = page.frameLocator('#progress-app');
  await app.locator('#task-title').filter({ hasText: 'Cindy 端到端验证' }).waitFor({ timeout: 15000 });
  assert.ok(await page.evaluate(() => window.__bridgeStats.wakes >= 3), 'panel retries after initial wake failures');
  await app.locator('#steps > .step').first().waitFor();
  await page.locator('#panel-status').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#progress-app').evaluate(el => getComputedStyle(el).visibility), 'visible', 'the task is shown after loading completes');
  await page.waitForFunction(() => window.__bridgeStats.wakes > 0 && window.__bridgeStats.requests > window.__bridgeStats.executions);
  assert.equal(await app.locator('#task-title').textContent(), 'Cindy 端到端验证');
  assert.match(await app.locator('#steps').innerText(), /工具桥接与实时刷新/);
  assert.match(await app.locator('#steps').innerText(), /暗色主题与任务列表显示/);
  const editLeaf = app.locator('li.step').filter({has: app.locator('.task-title-text', {hasText:'暗色主题与任务列表显示'})}).last();
  await editLeaf.locator(':scope > details > summary').click();
  await editLeaf.locator(':scope > details > details.execution-review > summary').click();
  await editLeaf.locator('.task-title-editor > summary').click();
  await editLeaf.locator('input[name="title"]').fill('暗色主题与任务显示已修订');
  await editLeaf.locator('.task-title-editor input[name="reason"]').fill('验证侧栏标题编辑');
  await editLeaf.locator('.task-title-editor button').click();
  await app.locator('#steps').getByText('暗色主题与任务显示已修订').waitFor();
  assert.equal(await page.evaluate(() => window.__bridgeStats.viewRegistrations), 0, 'the persistent sidebar must not compete with inline cards for latest-view ownership');
  const newerCardSource = (await host.rpc('get_progress_view', { session_id: binding.session_id })).structuredContent;
  const newerCard = await host.rpc('register_ui_view', {
    ...binding, view_id: 'newer-inline-card', display_mode: 'inline', mode_generation: 0,
    state_revision: newerCardSource.state_revision, view_sequence: newerCardSource.view_sequence
  });
  assert.equal(newerCard.structuredContent.is_latest, true, 'fixture must register a newer inline card');
  await host.rpc('update_task', { ...binding, task_id: 'bridge', execution_status: 'running', assignment: mainAssignment });
  await app.locator('#steps').filter({ hasText: '进行中' }).waitFor({ timeout: 8000 });
  assert.equal(await app.locator('#compact-view').isHidden(), true, 'a newer inline card must not collapse the sidebar');
  assert.equal(await app.locator('#auto-refresh-label').textContent(), '每 5 秒自动刷新');
  currentKV.taskProgressPanelDiagnostic = { kind: 'tool-call', hasHostSession: true, hasTaskSession: true, mapped: true };
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({ type: 'panel-diagnostic', diagnostic: { kind: 'tool-call', hasHostSession: true, hasTaskSession: true, mapped: true } }));
  await page.waitForTimeout(5300);
  assert.equal(await page.locator('#panel-status').isHidden(), true, 'successful binding must not reappear on every poll');
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({
    type: 'update', session_id: 'another-session',
    result: { structuredContent: { session_id: 'another-session', progress: { task_title: '不属于当前会话' } } }
  }));
  await page.waitForTimeout(300);
  assert.equal(await app.locator('#task-title').textContent(), 'Cindy 端到端验证', 'a background session broadcast cannot replace the current task');
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({ type: 'bind-session', session_id: 'another-session' }));
  await page.waitForTimeout(300);
  assert.equal(await app.locator('#task-title').textContent(), 'Cindy 端到端验证', 'a stale inline card cannot rebind the global panel');

  await page.emulateMedia({ colorScheme: 'dark' });
  await app.locator('html[data-theme="dark"]').waitFor();
  await app.locator('#refresh').click();
  await app.locator('#refresh:not([disabled])').waitFor();
  await app.locator('#refresh').click();
  await app.locator('#refresh:not([disabled])').waitFor();
  await app.locator('#steps').filter({ hasText: '进行中' }).waitFor();
  await app.locator('#status-filter').selectOption('running');
  assert.equal(await app.locator('.step.running').first().isVisible(), true, 'running task remains visible');
  assert.equal(await app.locator('.step').filter({hasText:'暗色主题与任务显示已修订'}).last().isVisible(), false, 'nonmatching leaf is hidden');
  await app.locator('#refresh').click();
  await app.locator('#refresh:not([disabled])').waitFor();
  assert.equal(await app.locator('#status-filter').inputValue(), 'running', 'refresh preserves filter selection');
  await app.locator('#status-filter').selectOption('review');
  assert.equal(await app.locator('#filter-empty').isVisible(), true, 'empty filter shows a visible explanation');
  await page.emulateMedia({ colorScheme: 'light' });
  await app.locator('html[data-theme="light"]').waitFor();
  assert.equal(await app.locator('#filter-empty').isVisible(), true, 'empty filter remains visible in light theme');
  await page.emulateMedia({ colorScheme: 'dark' });
  await app.locator('html[data-theme="dark"]').waitFor();
  await app.locator('#status-filter').selectOption('all');
  const theme = await app.locator('html').getAttribute('data-theme');
  assert.equal(theme, 'dark', 'embedded app follows the Cindy panel dark theme');
  assert.equal(await app.locator('#auto-refresh-label').textContent(), '每 5 秒自动刷新');

  const deleteLeaf = app.locator('li.step').filter({has: app.locator('.task-title-text', {hasText:'暗色主题与任务显示已修订'})}).last();
  if (!await deleteLeaf.locator(':scope > details').evaluate(el => el.open)) await deleteLeaf.locator(':scope > details > summary').click();
  if (!await deleteLeaf.locator(':scope > details > details.execution-review').evaluate(el => el.open)) await deleteLeaf.locator(':scope > details > details.execution-review > summary').click();
  await deleteLeaf.locator('.task-delete > summary').click();
  await deleteLeaf.locator('.task-delete input[name="reason"]').fill('隔离验收删除');
  await deleteLeaf.locator('.task-delete button').click();
  await app.locator('#steps').getByText('暗色主题与任务显示已修订').waitFor({state:'hidden'});
  const deletedState = (await host.rpc('get_progress',{...binding,view:'full'})).structuredContent.progress;
  assert(!deletedState.nodes.some(node => node.id === 'theme'));
  assert(deletedState.plan_revisions.some(record => record.operation === 'delete_task' && record.before_plan.nodes.theme));

  const switched = await host.rpc('init_task_network', {
    task_title: 'Cindy 会话切换与 5 秒轮询验证',
    tasks: [
      { id: 'live', title: '等待自动刷新显示进行中', assignment: mainAssignment },
      { id: 'second', title: '保持当前会话任务树一致', assignment: mainAssignment }
    ]
  });
  const switchedSession = switched.structuredContent.session_id;
  const switchedInstance = switched.structuredContent.progress.task_instance_id;
  currentKV = {
    ...currentKV,
    taskProgressSessionsByHostSession: { 'cindy-session-current': switchedSession }
  };
  await app.locator('#task-title').filter({ hasText: 'Cindy 会话切换与 5 秒轮询验证' }).waitFor({ timeout: 8000 });
  await page.evaluate(() => {
    const frame = document.createElement('iframe');
    frame.id = 'inline-app';
    frame.style = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;border:0';
    frame.src = '/core/app.html?source=inline';
    document.body.append(frame);
  });
  const inlineApp = page.frameLocator('#inline-app');
  await inlineApp.locator('#refresh').waitFor({ state: 'attached' });
  await page.locator('#inline-app').evaluate((frame, session_id) => {
    frame.contentWindow.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: { arguments: { session_id } } }, '*');
  }, switchedSession);
  await app.locator('#task-title').filter({ hasText: 'Cindy 会话切换与 5 秒轮询验证' }).waitFor();
  const current = await host.rpc('get_progress_view', { session_id: switchedSession });
  const pendingTask = current.structuredContent.progress.nodes.find(node => node.id !== 'root');
  await host.rpc('update_task', {
    session_id: switchedSession,
    task_instance_id: switchedInstance,
    task_id: pendingTask.id,
    execution_status: 'running',
    assignment: mainAssignment
  });
  await app.locator('#steps').filter({ hasText: '进行中' }).waitFor({ timeout: 8000 });
  await page.screenshot({ path: '/tmp/task-progress-cindy-reused-app.png' });
  currentKV = { ...currentKV, taskProgressSessionsByHostSession: {}, taskProgressPanelDiagnostic: { kind: 'session-switch', hasHostSession: true, mapped: false } };
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({
    type: 'bind-session', session_id: null,
    diagnostic: { kind: 'session-switch', hasHostSession: true, mapped: false }
  }));
  await app.locator('#empty-session-diagnostic').filter({ hasText: '已收到 Cindy 会话切换，但该会话尚未关联 Task Progress 数据。' }).waitFor();
  assert.equal(await page.locator('#progress-app').evaluate(el => getComputedStyle(el).visibility), 'visible', 'unmapped sessions still show the real empty state');
  await page.locator('#panel-status').filter({ hasText: '已收到 Cindy 会话切换，但该会话尚未关联 Task Progress 数据。' }).waitFor();
  assert.deepEqual(host.errors, []);
  console.log(JSON.stringify({
    pass: true,
    reused: 'plugins/task-progress/cindy/core/app.html',
    checks: ['full task network is visible', 'visible chat app binds the right panel to its current task session', 'panel wakes the resident tool bridge and retries until a reply arrives', 'repeated request ids execute only once', '5 second polling shows a running task without manual refresh', 'dark theme is passed into the embedded app', 'stale local cache cannot override the current session mapping', 'unmapped Cindy sessions show a visible sync diagnosis'],
    screenshot: '/tmp/task-progress-cindy-reused-app.png'
  }, null, 2));
  await page.close();
}

main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (server) await new Promise(resolve => server.close(resolve));
  await host?.close();
});
