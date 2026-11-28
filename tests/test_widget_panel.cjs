/* Real shared service + canonical widget shell + unchanged progress App. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-widget-'));
const state = path.join(root, 'service');
fs.mkdirSync(state, {mode:0o700});
const authorization = 'Bearer ' + crypto.randomBytes(48).toString('base64url');
fs.writeFileSync(path.join(state, 'http-headers.json'), JSON.stringify({Authorization:authorization}), {mode:0o600});
let service, browser, sequence = 0;
async function main() {
  service = spawn('python3', ['-B', path.resolve(__dirname, '../plugins/task-progress/shared_service.py'),
    '--port', '0', '--state-dir', state, '--session-dir', path.join(root, 'sessions')], {env:{...process.env, HOME:root}});
  service.stderr.on('data', chunk => process.stderr.write(chunk));
  for (let i = 0; i < 150 && !fs.existsSync(path.join(state, 'daemon.json')); i++) {
    if (service.exitCode !== null) throw new Error('Service exited');
    await pause(20);
  }
  const {port} = JSON.parse(fs.readFileSync(path.join(state, 'daemon.json')));
  const origin = `http://127.0.0.1:${port}`;
  async function post(endpoint, payload, source) {
    const response = await fetch(origin + endpoint, {method:'POST',
      headers:{Authorization:authorization, 'Content-Type':'application/json', Accept:'application/json',
        ...(source ? {'X-Task-Progress-Source':source} : {})},
      body:JSON.stringify(payload), signal:AbortSignal.timeout(8000)});
    assert.equal(response.status, 200);
    return response.json();
  }
  async function rpc(name, args) {
    const value = await post('/mcp', {jsonrpc:'2.0', id:++sequence, method:'tools/call', params:{name, arguments:args}});
    assert(!value.error && !value.result?.isError, JSON.stringify(value));
    return value.result.structuredContent;
  }
  const assignments = {execution_mode:'main_session', self_execution_reason:'Isolated widget fixture'};
  const a = await rpc('init_task_network', {task_title:'真实任务 A', tasks:Array.from({length:8}, (_, i) =>
    ({id:'step-' + i, title:'进度验证步骤 ' + (i + 1), assignment:assignments}))});
  const rest = [];
  for (let i = 0; i < 9; i++) rest.push(await rpc('init_task_network', {task_title:'其他任务 ' + i, atomic_reason:'One fixture operation'}));
  const link = await post('/widget/open', {session_id:a.session_id});
  browser = await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome', headless:true});
  const context = await browser.newContext({acceptDownloads:true});
  const page = await context.newPage({viewport:{width:1280, height:800}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(12000);
  await page.goto(link.url);
  const key = task => task.session_id;
  const panel = task => page.frameLocator(`[data-app-task="${key(task)}"]`);
  await panel(a).locator('#task-title').filter({hasText:'真实任务 A'}).waitFor();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
  assert.equal(await page.locator('.task-row').count(), 10);
  assert.equal(await page.locator('.main > .main-heading').count(), 0);
  assert.equal(await panel(a).locator('html').getAttribute('data-theme'), 'dark');
  // This is the production resource, including the production version replacement.
  const resource = await post('/mcp', {jsonrpc:'2.0', id:++sequence, method:'resources/read',
    params:{uri:'ui://task-progress/progress-inspector/v4'}});
  assert.equal(await page.locator(`[data-app-task="${key(a)}"]`).getAttribute('srcdoc'), resource.result.contents[0].text);
  const base = {session_id:a.session_id, task_instance_id:a.task_instance_id, task_id:'step-0'};
  const started = await rpc('update_task', {...base, execution_status:'running', assignment:assignments});
  await rpc('update_task', {...base, execution_status:'finished', outcome:'resolved',
    attempt_id:started.attempt_id, summary:'LIVE_WIDGET_RESULT'});
  await panel(a).getByText('LIVE_WIDGET_RESULT', {exact:true}).first().waitFor({state:'attached', timeout:15000});
  const b = rest.at(-1);
  await page.locator(`[data-task="${key(b)}"]`).click();
  await panel(b).locator('#task-title').filter({hasText:'其他任务 8'}).waitFor();
  assert(!(await panel(b).locator('body').innerText()).includes('LIVE_WIDGET_RESULT'));
  await page.locator(`[data-task="${key(a)}"]`).click();
  const downloads = [];
  page.on('download', download => downloads.push(download));
  await panel(a).locator('#export-all').click();
  await panel(a).locator('#export-feedback').filter({hasText:'已开始下载 Excel 和 HTML'}).waitFor();
  for (let i = 0; i < 60 && downloads.length < 2; i++) await pause(50);
  assert.equal(downloads.length, 2);
  for (const download of downloads) {
    const bytes = fs.readFileSync(await download.path());
    assert(bytes.length > 100);
    if (download.suggestedFilename().endsWith('.xlsx')) assert.equal(bytes.subarray(0, 2).toString(), 'PK');
    else assert(bytes.toString().includes('LIVE_WIDGET_RESULT'));
  }
  await page.locator('[data-action=tile]').click();
  assert.equal(await page.locator('.frame-page.visible').count(), 8);
  assert.equal(await page.locator('.frame-page.visible iframe:not([inert])').count(), 1);
  const inactiveId = await page.locator('.frame-page.visible:not(.focused)').first().getAttribute('data-tile');
  const inactiveFrame = page.frameLocator(`[data-app-task="${inactiveId}"]`);
  await inactiveFrame.locator('#task-title').waitFor();
  const readScroll = locator => locator.locator('body').evaluate(() => document.scrollingElement.scrollTop);
  const before = await readScroll(inactiveFrame);
  const box = await page.locator(`[data-app-task="${inactiveId}"]`).boundingBox();
  // A bottom-row iframe can be partly clipped: its geometric center may lie
  // below the scroll container, so wheeling there tests the footer instead.
  const mainBox = await page.locator('#main-body').boundingBox();
  const viewport = page.viewportSize();
  const visible = {left:Math.max(box.x,mainBox.x,0), right:Math.min(box.x+box.width,mainBox.x+mainBox.width,viewport.width),
    top:Math.max(box.y,mainBox.y,0), bottom:Math.min(box.y+box.height,mainBox.y+mainBox.height,viewport.height)};
  assert(visible.right > visible.left && visible.bottom > visible.top, 'inactive iframe must intersect the visible grid');
  const pointer = {x:(visible.left+visible.right)/2,y:(visible.top+visible.bottom)/2};
  assert.equal(await page.evaluate(({pointer,id}) =>
    document.elementFromPoint(pointer.x,pointer.y)?.closest('[data-tile]')?.dataset.tile, {pointer,id:inactiveId}), inactiveId,
    'wheel input must hit the actual inactive tile, not clipped content or the footer');
  await page.mouse.move(pointer.x, pointer.y);
  await page.mouse.wheel(0, 240); await pause(350);
  assert.equal(await readScroll(inactiveFrame), before);
  await page.waitForFunction(() => document.getElementById('main-body').scrollTop > 0, undefined, {timeout:2000}).catch(async () => {
    console.error('wheel geometry', await page.locator('#main-body').evaluate(el => ({height:el.clientHeight, scrollHeight:el.scrollHeight, width:el.clientWidth, top:el.scrollTop, overflow:getComputedStyle(el).overflow, frames:[...el.querySelectorAll('.visible')].map(x => ({id:x.dataset.tile, rect:x.getBoundingClientRect().toJSON()}))})), box);
    throw new Error('Inactive tile did not pass wheel scrolling to outer grid');
  });
  await page.locator('#main-body').evaluate(el => el.scrollTop = 0);
  await page.locator(`[data-tile="${inactiveId}"] .frame-activation`).click();
  assert.equal(await page.locator(`[data-app-task="${inactiveId}"]`).evaluate(el => el.inert), false);
  assert.equal(await page.locator('.frame-page.visible iframe:not([inert])').count(), 1);
  assert.equal(await page.locator('.frame-page.visible .tile-header').first().innerText(), '');
  const ninth = await page.locator('.tile-check[aria-disabled=true]').first().getAttribute('data-check');
  await page.locator(`[data-check="${ninth}"]`).click({force:true});
  assert.equal(await page.locator('.frame-page.visible').count(), 8);
  await page.locator(`[data-maximize="${inactiveId}"]`).click();
  assert.equal(await page.locator('.frame-page.visible').count(), 1);
  await page.locator('[data-action=back-tiles]').click();
  assert.equal(await page.locator('.frame-page.visible').count(), 8);
  await page.locator('[data-action=theme]').click();
  await panel(a).locator('html[data-theme=light]').waitFor({state:'attached'});
  await page.locator('[data-action=theme]').click();
  // New real tasks appear without reloading or changing the selected task.
  const activeBefore = await page.locator('.frame-page.visible.focused').getAttribute('data-tile');
  const added = await rpc('init_task_network', {task_title:'新增实时任务', atomic_reason:'One fixture operation'});
  await page.locator(`[data-task="${key(added)}"]`).waitFor({timeout:10000});
  assert.equal(await page.locator('.frame-page.visible.focused').getAttribute('data-tile'), activeBefore);
  const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
  if (artifacts) {
    fs.mkdirSync(artifacts, {recursive:true});
    await page.screenshot({path:path.join(artifacts, 'live-widget-tiled.png'), fullPage:true});
  }
  for (const width of [1024, 390, 320]) {
    await page.setViewportSize({width, height:800}); await pause(200);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.notEqual(await page.locator('.sidebar').evaluate(el => getComputedStyle(el).display), 'none');
    assert((await page.locator('.sidebar').boundingBox()).x >= 0, 'Resize must not fold the sidebar');
  }
  await page.locator('[data-action=sidebar-collapse]').click();
  assert.equal(await page.locator('.sidebar').evaluate(el => getComputedStyle(el).display), 'none');
  await page.setViewportSize({width:1024, height:800});
  assert.equal(await page.locator('.sidebar').evaluate(el => getComputedStyle(el).display), 'none', 'Manual choice survives resize');
  await page.locator('[data-action=drawer]').click();
  assert.notEqual(await page.locator('.sidebar').evaluate(el => getComputedStyle(el).display), 'none');
  // A session with historical and parallel records still occupies one sidebar
  // row. Record selection is local to this viewer, including exact deep links.
  await page.locator('[data-action=single]').click();
  await page.locator(`[data-task="${key(b)}"]`).click();
  const bbase = {session_id:b.session_id, task_instance_id:b.task_instance_id, task_id:b.root_id};
  const brun = await rpc('update_task', {...bbase, execution_status:'running', assignment:assignments});
  await rpc('update_task', {...bbase, attempt_id:brun.attempt_id, execution_status:'finished',
    outcome:'resolved', summary:'HISTORICAL_SESSION_RESULT'});
  await page.locator(`[data-task="${key(a)}"]`).click();
  const bnew = await rpc('init_task_network', {session_id:b.session_id, task_title:'会话 B 新任务',
    atomic_reason:'Independent fixture', task_relationship:'independent', new_task_reason:'Previous goal completed'});
  await rpc('route_request', {session_id:b.session_id, task_instance_id:bnew.task_instance_id,
    request_id:'widget-peer', request_text:'Independent task', task_title:'会话 B 并行任务', mode:'parallel',
    reason:'Isolated fixture', acceptance_criteria:['Evidence']});
  await page.locator(`[data-task="${key(b)}"] .task-title`).filter({hasText:'会话 B 新任务'}).waitFor({timeout:10000});
  await page.locator(`[data-task="${key(b)}"]`).click();
  await panel(b).locator('#task-title').filter({hasText:'会话 B 新任务'}).waitFor({timeout:10000});
  const picker = page.locator(`[data-tile="${key(b)}"] select[data-instance]`);
  await picker.locator('option').nth(2).waitFor({state:'attached'});
  assert.equal(await page.locator('#task-count').innerText(), '11');
  assert.equal(await page.locator('.task-row').count(), 11);
  assert.equal(await page.locator(`[data-task="${key(b)}"]`).count(), 1);
  await picker.selectOption(b.task_instance_id);
  await panel(b).getByText('HISTORICAL_SESSION_RESULT', {exact:true}).first().waitFor({state:'attached'});
  await page.waitForTimeout(3300); // Catalogue refresh must not reset explicit record selection.
  assert.equal(await picker.inputValue(), b.task_instance_id);
  await page.locator('#search').fill('其他任务 8');
  assert.equal(await page.locator('.task-row').count(), 1, 'historical title search returns its session once');
  await page.locator('#search').fill('');
  const historyLink = await post('/widget/open', {session_id:b.session_id, task_instance_id:b.task_instance_id});
  const historyPage = await context.newPage();
  await historyPage.goto(historyLink.url);
  await historyPage.frameLocator(`[data-app-task="${key(b)}"]`).getByText('HISTORICAL_SESSION_RESULT', {exact:true}).first().waitFor({state:'attached'});
  assert.equal(await historyPage.locator('#task-count').innerText(), '11');
  await historyPage.close();
  const listLink = await post('/widget/open', {});
  const listPage = await context.newPage();
  await listPage.goto(listLink.url);
  await listPage.locator('.frame-page.visible iframe').waitFor();
  assert.equal(await listPage.locator('#task-count').innerText(), '11');
  await listPage.close();
  await picker.selectOption(bnew.task_instance_id);
  await panel(b).locator('#task-title').filter({hasText:'会话 B 新任务'}).waitFor();
  const foreground = await rpc('get_progress', {session_id:b.session_id});
  assert.equal(foreground.task_instance_id, bnew.task_instance_id, 'record viewing never changes foreground');
  // Host archival uses an independent marker: catalogue polling moves the
  // unfinished task into history while its original result remains readable.
  await page.locator(`[data-task="${key(a)}"]`).click();
  const archived = bnew; // This session contains current, parallel and historical records.
  const archiveMessage = {jsonrpc:'2.0', id:++sequence, method:'task-progress/archive-host-session',
    params:{session_id:archived.session_id, host_session_id:'host-widget', source:'cindy'}};
  const archiveResult = await post('/mcp', archiveMessage);
  assert.equal(archiveResult.result.changed, true);
  assert.equal((await post('/mcp', {...archiveMessage, id:++sequence})).result.changed, false);
  await page.locator('.history-toggle').filter({hasText:'已收束 / 历史 1'}).waitFor({timeout:10000});
  assert.equal(await page.locator(`[data-task="${key(archived)}"]`).count(), 0);
  await page.locator('.history-toggle').click();
  await page.locator(`[data-task="${key(archived)}"]`).click();
  await panel(archived).locator('#task-title').filter({hasText:'会话 B 新任务'}).waitFor();
  assert.equal(await page.locator('#task-count').innerText(), '11', 'archival changes placement, not session total');
  await page.locator(`[data-tile="${key(b)}"] select[data-instance]`).selectOption(b.task_instance_id);
  await panel(b).getByText('HISTORICAL_SESSION_RESULT', {exact:true}).first().waitFor({state:'attached'});
  await page.locator('[data-filter=unfinished]').click();
  assert.equal(await page.locator(`[data-task="${key(archived)}"]`).count(), 0);
  assert.equal((await rpc('get_progress', {session_id:archived.session_id})).progress.status, 'in_progress');
  const denied = await fetch(origin + '/widget/rpc', {method:'POST', headers:{
    Authorization:'Bearer ' + link.url.split('#')[1], 'Content-Type':'application/json', Accept:'application/json'},
    body:JSON.stringify(archiveMessage)}).then(response => response.json());
  assert.equal(denied.error.code, -32602, 'read-only widget credentials cannot archive');
  // All hosts share a daemon, but source-scoped pages never share task lists.
  const scopedPages = {};
  const scopedTasks = {};
  for (const source of ['codex','cindy']) {
    const wire = await post('/mcp', {jsonrpc:'2.0',id:++sequence,method:'tools/call',
      params:{name:'init_task_network',arguments:{task_title:source+' 独立任务',atomic_reason:'Source fixture'}}},source);
    assert(!wire.error && !wire.result.isError);
    scopedTasks[source] = wire.result.structuredContent;
    const link = await post('/widget/open',{source});
    const scoped = await context.newPage();scopedPages[source] = scoped;await scoped.goto(link.url);
    await scoped.locator('#task-count').filter({hasText:'1'}).waitFor();
    assert.equal(await scoped.locator('#task-count').innerText(),'1');
    assert.equal(await scoped.locator('.task-row').count(),1);
    assert.equal(await scoped.locator('[data-row]').getAttribute('data-row'),scopedTasks[source].session_id);
    assert((await scoped.locator('#catalogue-title').innerText()).includes(source==='cindy'?'Cindy':'Codex'));
  }
  await post('/mcp',{jsonrpc:'2.0',id:++sequence,method:'task-progress/archive-host-session',
    params:{session_id:scopedTasks.cindy.session_id,host_session_id:'scoped-host',source:'cindy'}});
  await scopedPages.cindy.locator('.history-toggle').filter({hasText:'已收束 / 历史 1'}).waitFor({timeout:10000});
  assert.equal(await scopedPages.codex.locator('#task-count').innerText(),'1');
  assert.equal(await scopedPages.codex.locator('.history-toggle').count(),0);
  assert.equal(await page.locator('#task-count').innerText(),'11','unclassified list excludes both classified hosts');
  for (const scoped of Object.values(scopedPages)) await scoped.close();
  await page.close();
  assert.equal((await rpc('get_progress', {session_id:a.session_id})).progress.status, 'in_progress');
  assert(!fs.existsSync(path.join(root, 'sessions', a.session_id + '.view.json')));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ok:true, checks:['real task catalogue', 'fixed task isolation', 'live progress updates',
    'unchanged app.html', 'native default dark shell', 'HTML/XLSX export', '8 task limit', 'selected scroll gating',
    'single/maximize switching', 'live new tasks', '320px layout', 'closing preserves execution',
    'one row and count per session', 'nested current/parallel/history selection', 'historical deep link',
    'hidden foreground refresh', 'history title search', 'host archive moves entire session into history',
    'archive does not modify results', 'widget cannot write archive',
    'Codex/Cindy/unknown lists and counts isolated', 'archive does not cross source lists']}));
}
main().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {
  if (browser) await browser.close();
  if (service && service.exitCode === null) {service.kill('SIGTERM'); await once(service, 'exit');}
  fs.rmSync(root, {recursive:true, force:true});
});
