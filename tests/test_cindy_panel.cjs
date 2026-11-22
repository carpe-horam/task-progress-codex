'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { createHost } = require('./browser_host.cjs');

const root = path.resolve(__dirname, '..', 'plugins/task-progress/cindy');
// Test this source tree, even when a prior run left a generated core behind.
execFileSync('python3', [path.join(root, 'prepare.py')], { stdio: 'inherit' });
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
    window.__themeTimers = new Set();
    const nativeSetInterval = window.setInterval.bind(window);
    const nativeClearInterval = window.clearInterval.bind(window);
    window.setInterval = (fn, delay, ...args) => {
      const id = nativeSetInterval(fn, delay, ...args);
      if (delay === 500) window.__themeTimers.add(id);
      return id;
    };
    window.clearInterval = id => {
      window.__themeTimers.delete(id);
      return nativeClearInterval(id);
    };
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
  await app.locator('#step-list .plan-row').first().waitFor();
  await page.locator('#panel-status').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#progress-app').evaluate(el => getComputedStyle(el).visibility), 'visible', 'the task is shown after loading completes');
  const timeoutRecovery = await page.evaluate(async expectedSession => {
    const originalFetch = window.fetch;
    let releaseLate, readCount = 0;
    window.fetch = (input, ...args) => {
      if (String(input) !== '/kv') return originalFetch(input, ...args);
      if (++readCount === 1) return new Promise(resolve => {
        releaseLate = () => resolve(new Response(JSON.stringify({taskProgressActiveHostSessionId:'old-host',
          taskProgressSessionsByHostSession:{'old-host':'stale-session'}}), {status:200}));
      });
      return originalFetch(input, ...args);
    };
    try {
      const first = syncSessionFromKV();
      await new Promise(resolve=>setTimeout(resolve,30));
      const channel = new BroadcastChannel('task-progress-panel');
      channel.postMessage({type:'bind-session',session_id:expectedSession});
      await new Promise(resolve=>setTimeout(resolve,30));
      await first;
      const deadline=Date.now()+4000;
      while ((sessionSyncBusy || readCount < 2) && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,20));
      releaseLate();
      await new Promise(resolve=>setTimeout(resolve,50));
      channel.close();
      return {binding:sessionId,readCount,busy:sessionSyncBusy};
    } finally { window.fetch = originalFetch; }
  }, binding.session_id);
  assert.equal(timeoutRecovery.binding, binding.session_id, 'a late KV response cannot replace the recovered active session');
  assert.ok(timeoutRecovery.readCount >= 2 && !timeoutRecovery.busy, 'a queued switch rereads KV after the timeout');
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'other-chat',status:'uncertain'}));
  await page.waitForTimeout(100);assert.equal(await page.locator('#panel-status').isHidden(),true,'other sessions cannot surface auto-review state');
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'uncertain'}));
  await page.locator('#panel-status').filter({hasText:'派发结果待核实'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'review_pending'}));
  await page.locator('#panel-status').filter({hasText:'可能正在排队或检查中'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'host_suspended'}));
  await page.locator('#panel-status').filter({hasText:'自动验收暂缓'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'disable_unconfirmed'}));
  await page.locator('#panel-status').filter({hasText:'关闭设置尚未确认保存'}).waitFor();
  for (const status of ['empty','recovered','dispatched']) {
    await page.evaluate(status => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status}),status);
    await page.waitForTimeout(50);
    assert.match(await page.locator('#panel-status').innerText(),/关闭设置尚未确认保存/,'unconfirmed disable survives stale status');
  }
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'disabled'}));
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => {
    window.__reviewReplayChannel=new BroadcastChannel('task-progress-panel');
    window.__reviewReplayChannel.onmessage=event=>{
      if(event.data?.type==='panel-review-state-request' && event.data.host_session_id==='cindy-session-current')
        window.__reviewReplayChannel.postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'disable_unconfirmed'});
    };
    return syncSessionFromKV();
  });
  await page.locator('#panel-status').filter({hasText:'关闭设置尚未确认保存'}).waitFor();
  await page.evaluate(() => {window.__reviewReplayChannel.close();new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'disabled'});});
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'association_unverified'}));
  await page.locator('#panel-status').filter({hasText:'会话授权尚未确认'}).waitFor();
  await app.locator('html').evaluate((_,session)=>window.parent.postMessage({jsonrpc:'2.0',method:'task-progress/rendered',params:{session_id:session}},'*'),binding.session_id);
  await page.locator('#panel-status').filter({hasText:'会话授权尚未确认'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'empty'}));
  await page.locator('#panel-status').filter({hasText:'会话授权尚未确认'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'association_verified'}));
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'consent_required'}));
  await page.locator('#panel-status').filter({hasText:'需要重新启用'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'disabled'}));
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'event_gap'}));
  await page.locator('#panel-status').filter({hasText:'会话事件有缺失'}).waitFor();
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'empty'}));
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'next-chat',status:'association_unverified'}));
  assert.equal(await page.locator('#panel-status').isHidden(),true,'a background session warning stays scoped');
  currentKV.taskProgressSessionsByHostSession['next-chat']=binding.session_id;
  currentKV.taskProgressActiveHostSessionId='next-chat';
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'bind-session',session_id:null}));
  await page.locator('#panel-status').filter({hasText:'会话授权尚未确认'}).waitFor();
  currentKV.taskProgressActiveHostSessionId='cindy-session-current';
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'bind-session',session_id:null}));
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'auto-review-state',host_session_id:'cindy-session-current',status:'empty'}));
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'task-init-error',host_session_id:'other-chat',message:'其他会话错误'}));
  await page.waitForTimeout(100);
  assert.equal(await page.locator('#panel-status').isHidden(), true, 'another conversation cannot surface an init error here');
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({type:'task-init-error',host_session_id:'cindy-session-current',message:'请说明独立新目标'}));
  await page.locator('#panel-status').filter({hasText:'新任务未开始：请说明独立新目标'}).waitFor();
  await page.evaluate(session => new BroadcastChannel('task-progress-panel').postMessage({type:'update',tool:'init_task_network',session_id:session,result:{structuredContent:{}}}), binding.session_id);
  await page.locator('#panel-status').waitFor({state:'hidden'});
  await page.waitForFunction(() => window.__bridgeStats.wakes > 0 && window.__bridgeStats.requests > window.__bridgeStats.executions);
  assert.equal(await app.locator('#task-title').textContent(), 'Cindy 端到端验证');
  assert.match(await app.locator('#step-list').innerText(), /工具桥接与实时刷新/);
  assert.match(await app.locator('#step-list').innerText(), /暗色主题与任务列表显示/);
  await app.locator('#step-list .plan-title').filter({hasText:'暗色主题与任务列表显示'}).click();
  const editLeaf = app.locator('li.step').filter({has: app.locator('.task-title-text', {hasText:'暗色主题与任务列表显示'})}).last();
  await app.locator('#detail-tab-raw').click();
  assert.equal(await app.locator('.task-title-editor, form[data-title-edit]').count(),0,'title editing is absent from every current task');
  await app.locator('#step-detail-back').click();
  assert.equal(await page.evaluate(() => window.__bridgeStats.viewRegistrations), 0, 'the persistent sidebar must not compete with inline cards for latest-view ownership');
  const newerCardSource = (await host.rpc('get_progress_view', { session_id: binding.session_id })).structuredContent;
  const newerCard = await host.rpc('register_ui_view', {
    ...binding, view_id: 'newer-inline-card', display_mode: 'inline', mode_generation: 0,
    state_revision: newerCardSource.state_revision, view_sequence: newerCardSource.view_sequence
  });
  assert.equal(newerCard.structuredContent.is_latest, true, 'fixture must register a newer inline card');
  await host.rpc('update_task', { ...binding, task_id: 'bridge', execution_status: 'running', assignment: mainAssignment });
  await app.locator('#step-list').filter({ hasText: '进行中' }).waitFor({ timeout: 8000 });
  assert.equal(await app.locator('#compact-view').isHidden(), true, 'a newer inline card must not collapse the sidebar');
  assert.equal(await app.locator('#auto-refresh-label').textContent(), '自动更新');
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

  assert.equal(await app.locator('#task-info, #root-review, .task-title-editor, form[data-title-edit]').count(),0);
  assert(await app.locator('#leaf-progress').isVisible());
  assert.equal(await app.locator('#progressbar').getAttribute('aria-valuenow'),'0');
  assert.equal(await app.getByText('修改标题',{exact:true}).count(),0);
  assert.equal(await app.getByText('保存标题',{exact:true}).count(),0);
  const reviewArtifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS || path.resolve(__dirname,'../.cindy-build/ux-acceptance/task-details-review');
  fs.mkdirSync(reviewArtifacts,{recursive:true});
  await app.locator('#leaf-progress').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(reviewArtifacts,'cindy-leaf-progress.png')});

  await page.emulateMedia({ colorScheme: 'dark' });
  await app.locator('html[data-theme="dark"]').waitFor();
  // Electron insertCSS changes computed styles without changing the head DOM.
  // Keep the OS scheme dark while switching only the host-injected stylesheet.
  await page.evaluate(() => {
    window.__themeMessages = [];
    document.querySelector('#progress-app').contentWindow.addEventListener('message', event => {
      if (event.data?.method === 'ui/notifications/host-context-changed') window.__themeMessages.push(event.data.params.theme);
    });
    window.__themeDOMChanges = 0;
    window.__themeDOMObserver = new MutationObserver(records => { window.__themeDOMChanges += records.length; });
    window.__themeDOMObserver.observe(document.head, {childList:true, subtree:true, attributes:true});
    window.__hostThemeSheet = new CSSStyleSheet();
    window.__hostThemeSheet.replaceSync(':root { color-scheme: light; }');
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, window.__hostThemeSheet];
  });
  await app.locator('html[data-theme="light"]').waitFor({timeout:2500});
  assert.equal(await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches), true, 'host theme overrides the OS theme');
  assert.equal(await page.evaluate(() => window.__themeDOMChanges), 0, 'host stylesheet change must not notify the DOM observer');
  await page.waitForTimeout(1100);
  assert.deepEqual(await page.evaluate(() => window.__themeMessages), ['light'], 'unchanged host theme does not resend messages on each poll');
  await page.evaluate(() => window.__hostThemeSheet.replaceSync(':root { color-scheme: dark; }'));
  await app.locator('html[data-theme="dark"]').waitFor({timeout:2500});
  assert.deepEqual(await page.evaluate(() => window.__themeMessages), ['light', 'dark'], 'CSSOM-only changes synchronize in both directions');
  await page.evaluate(() => {
    window.__themeDOMObserver.disconnect();
    document.adoptedStyleSheets = document.adoptedStyleSheets.filter(sheet => sheet !== window.__hostThemeSheet);
  });
  await app.locator('#refresh').click();
  await app.locator('#refresh:not([disabled])').waitFor();
  await app.locator('#refresh').click();
  await app.locator('#refresh:not([disabled])').waitFor();
  await app.locator('#step-list').filter({ hasText: '进行中' }).waitFor();
  await app.locator('#status-chips [data-status-filter="tone:running"]').click();
  assert.equal(await app.locator('#step-list .plan-row[data-step-id="bridge"]').isVisible(), true, 'running task remains visible');
  assert.equal(await app.locator('#step-list .plan-row[data-step-id="theme"]').count(), 0, 'nonmatching leaf is hidden');
  await app.locator('#refresh').click();
  await app.locator('#refresh:not([disabled])').waitFor();
  assert.equal(await app.locator('#status-chips [data-status-filter="tone:running"]').getAttribute('aria-pressed'), 'true', 'refresh preserves filter selection');
  await app.locator('#step-search-open').click();
  await app.locator('#step-search').fill('不存在的任务');
  await app.locator('#search-dialog-done').click();
  assert.equal(await app.locator('#filter-empty').isVisible(), true, 'empty filter shows a visible explanation');
  await page.emulateMedia({ colorScheme: 'light' });
  await app.locator('html[data-theme="light"]').waitFor();
  assert.equal(await app.locator('#filter-empty').isVisible(), true, 'empty filter remains visible in light theme');
  await page.emulateMedia({ colorScheme: 'dark' });
  await app.locator('html[data-theme="dark"]').waitFor();
  await app.locator('#clear-plan-filters').click();
  const theme = await app.locator('html').getAttribute('data-theme');
  assert.equal(theme, 'dark', 'embedded app follows the Cindy panel dark theme');
  assert.equal(await app.locator('#auto-refresh-label').textContent(), '自动更新');

  const deleteLeaf = app.locator('li.step').filter({has: app.locator('.task-title-text', {hasText:'暗色主题与任务列表显示'})}).last();
  await app.locator('#step-list .plan-title').filter({hasText:'暗色主题与任务列表显示'}).click();
  await app.locator('#detail-tab-raw').click();
  await app.locator('#detail-panel-raw .task-delete > summary').click();
  for (const width of [320,390]) {
    await page.setViewportSize({width,height:900});
    await page.waitForFunction(width => {
      const frame=document.querySelector('#progress-app');
      return Math.round(frame.getBoundingClientRect().width)===width && frame.contentDocument.documentElement.clientWidth>0;
    },width,{timeout:8000});
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      await app.locator('html[data-theme="'+theme+'"]').waitFor();
      await app.locator('#detail-panel-raw .task-delete').scrollIntoViewIfNeeded();
      const formLayout = await app.locator('#detail-panel-raw .task-delete form').evaluate(form => ({
        display:getComputedStyle(form).display,
        fields:[...form.querySelectorAll('input,button')].map(control => {
          const box=control.getBoundingClientRect(),style=getComputedStyle(control);
          return {width:box.width,height:box.height,left:box.left,right:box.right,fontSize:parseFloat(style.fontSize),viewport:innerWidth};
        })
      }));
      assert.equal(formLayout.display,'grid','delete controls retain a structured form layout');
      assert(formLayout.fields.every(control => control.width>0 && control.height>=30 && control.fontSize>=12 && control.left>=0 && control.right<=control.viewport+1),
        'delete controls remain readable and inside the '+width+'px '+theme+' panel: '+JSON.stringify(formLayout));
      await page.screenshot({path:path.join(reviewArtifacts,'cindy-delete-'+width+'-'+theme+'.png')});
    }
  }
  await page.setViewportSize({width:1150,height:1200});
  await app.locator('#detail-panel-raw .task-delete input[name="reason"]').fill('隔离验收删除');
  await app.locator('#detail-panel-raw .task-delete button').click();
  let deletedState;
  for (let i=0; i<40; i++) {
    deletedState = (await host.rpc('get_progress',{...binding,view:'full'})).structuredContent.progress;
    if (!deletedState.nodes.some(node => node.id === 'theme')) break;
    await page.waitForTimeout(250);
  }
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
  await app.locator('#step-list').filter({ hasText: '进行中' }).waitFor({ timeout: 8000 });
  await page.screenshot({ path: '/tmp/task-progress-cindy-reused-app.png' });
  currentKV = { ...currentKV, taskProgressSessionsByHostSession: {}, taskProgressPanelDiagnostic: { kind: 'session-switch', hasHostSession: true, mapped: false } };
  await page.evaluate(() => new BroadcastChannel('task-progress-panel').postMessage({
    type: 'bind-session', session_id: null,
    diagnostic: { kind: 'session-switch', hasHostSession: true, mapped: false }
  }));
  await app.locator('#empty-session-diagnostic').filter({ hasText: '已收到 Cindy 会话切换，但该会话尚未关联 Task Progress 数据。' }).waitFor();
  assert.equal(await page.locator('#progress-app').evaluate(el => getComputedStyle(el).visibility), 'visible', 'unmapped sessions still show the real empty state');
  await page.locator('#panel-status').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(() => window.__themeTimers.size), 1, 'one theme watcher belongs to the mounted panel');
  await app.locator('html').evaluate(() => window.parent.postMessage({jsonrpc:'2.0', id:'theme-teardown', method:'ui/resource-teardown'}, '*'));
  await page.waitForFunction(() => window.__themeTimers.size === 0);
  assert.deepEqual(host.errors, []);
  console.log(JSON.stringify({
    pass: true,
    reused: 'plugins/task-progress/cindy/core/app.html',
    checks: ['full task network is visible', 'title editing is removed from root and leaf details', 'pending leaf deletion form is readable at 320/390px in both themes and deletes through the real backend', 'visible chat app binds the right panel to its current task session', 'panel wakes the resident tool bridge and retries until a reply arrives', 'repeated request ids execute only once', '5 second polling shows a running task without manual refresh', 'dark theme is passed into the embedded app', 'CSSOM-only host theme changes synchronize without duplicate notifications', 'theme watcher is cleared on resource teardown', 'stale local cache cannot override the current session mapping', 'unmapped Cindy sessions show a visible sync diagnosis'],
    screenshot: '/tmp/task-progress-cindy-reused-app.png'
  }, null, 2));
  await page.close();
}

main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  await host?.close();
  if (server) await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
});
