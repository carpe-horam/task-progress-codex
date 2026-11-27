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
  async function post(endpoint, payload) {
    const response = await fetch(origin + endpoint, {method:'POST',
      headers:{Authorization:authorization, 'Content-Type':'application/json', Accept:'application/json'},
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
  const key = task => task.session_id + ':' + task.task_instance_id;
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
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
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
  await page.close();
  assert.equal((await rpc('get_progress', {session_id:a.session_id})).progress.status, 'in_progress');
  assert(!fs.existsSync(path.join(root, 'sessions', a.session_id + '.view.json')));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ok:true, checks:['real task catalogue', 'fixed task isolation', 'live progress updates',
    'unchanged app.html', 'native default dark shell', 'HTML/XLSX export', '8 task limit', 'selected scroll gating',
    'single/maximize switching', 'live new tasks', '320px layout', 'closing preserves execution']}));
}
main().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {
  if (browser) await browser.close();
  if (service && service.exitCode === null) {service.kill('SIGTERM'); await once(service, 'exit');}
  fs.rmSync(root, {recursive:true, force:true});
});
