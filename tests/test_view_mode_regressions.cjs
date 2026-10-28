/* Isolated browser regressions for fullscreen view recovery and deduplication. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-view-regressions-'));
// Direct-source fixture only; the served resource injects the manifest version.
const html = fs.readFileSync(path.join(root, 'plugins/task-progress/app.html'), 'utf8').replaceAll('__TASK_PROGRESS_VERSION__', 'test-source');
const pending = new Map();
let seq = 0;
let browser;
const python = spawn('python3', ['-c', `import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('view_regression_server',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.LEGACY_DB_PATH=pathlib.Path(sys.argv[2])/'progress-v3.sqlite3';m.SESSION_DIR=pathlib.Path(sys.argv[2]);m.SESSION_DIR.mkdir(parents=True,exist_ok=True);m.main()
`, path.join(root, 'plugins/task-progress/server.py'), temp]);
python.stderr.on('data', chunk => process.stderr.write(chunk));
readline.createInterface({input: python.stdout}).on('line', line => {
  const response = JSON.parse(line);
  const waiter = pending.get(response.id);
  if (!waiter) return;
  pending.delete(response.id);
  if (response.error || response.result?.isError) waiter.reject(new Error(response.error?.message || JSON.stringify(response.result.content)));
  else waiter.resolve(response.result);
});

function rpc(name, args) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    python.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args}}) + '\n');
  });
}

async function main() {
  browser = await chromium.launch({channel: process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome', headless: true});
  const page = await browser.newPage({viewport:{width:1100,height:1200}});
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.exposeFunction('auditRpc', rpc);
  await page.exposeFunction('readViewRegistry', sessionId => {
    const file = path.join(temp,`${sessionId}.view.json`);
    return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file,'utf8')) : {};
  });
  await page.setContent('<!doctype html><title>Task Progress isolated host</title><main id="frames"></main>');
  await page.evaluate(() => {
    window.audit = {snapshots:{},modes:{},modeChanges:[]};
    addEventListener('message', async event => {
      const message = event.data;
      if (!message || message.jsonrpc !== '2.0') return;
      const frame = [...document.querySelectorAll('iframe')].find(item => item.contentWindow === event.source);
      if (!frame) return;
      const reply = result => event.source.postMessage({jsonrpc:'2.0',id:message.id,result}, '*');
      if (message.method === 'ui/initialize') reply({protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{displayMode:window.audit.modes[frame.id]||'inline'}});
      if (message.method === 'ui/request-display-mode') {
        window.audit.modeChanges.push({frame:frame.id,mode:message.params.mode});
        reply({displayMode:message.params.mode});
        event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{displayMode:message.params.mode}}, '*');
      }
      if (message.method === 'ui/notifications/initialized') {
        event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.audit.snapshots[frame.id]}, '*');
      }
      if (message.method === 'tools/call') reply(await window.auditRpc(message.params.name,message.params.arguments));
      if (message.method === 'ui/resource-teardown') reply({});
    });
  });

  async function mount(id, snapshot, mode) {
    await page.evaluate(({id,snapshot,html,mode}) => {
      window.audit.snapshots[id] = snapshot;
      window.audit.modes[id] = mode;
      const frame = document.createElement('iframe');
      frame.id = id;
      frame.style = 'display:block;width:1000px;height:700px;border:0';
      frame.srcdoc = html;
      document.getElementById('frames').append(frame);
    }, {id,snapshot,html,mode});
    const frame = page.frameLocator('#' + id);
    await frame.locator('#summary').waitFor({state:'attached'});
    return frame;
  }

  try {
    const old = await rpc('init_task_network',{task_title:'Old task result',atomic_reason:'One isolated UI fixture'});
    const sessionId = old.structuredContent.session_id;
    const oldRunning=await rpc('update_task',{session_id:sessionId,task_id:'root',execution_status:'running',assignment:{execution_mode:'main_session',self_execution_reason:'Complete isolated view fixture before new task'}});
    await rpc('update_task',{session_id:sessionId,task_id:'root',execution_status:'finished',outcome:'resolved',attempt_id:oldRunning.structuredContent.progress.nodes[0].attempt_id});
    await rpc('init_task_network',{session_id:sessionId,task_title:'Current task',atomic_reason:'One isolated UI fixture'});
    const staleFull = await mount('stale-full',old,'fullscreen');
    await staleFull.locator('#task-title').filter({hasText:'Current task'}).waitFor({timeout:8000});
    await staleFull.locator('.card:not(.inline-stale)').waitFor({state:'visible'});
    await staleFull.locator('#refresh:not([disabled])').waitFor();
    const staleState = await staleFull.locator('.card').getAttribute('class');
    assert.equal(staleState.includes('inline-stale'),false);

    const fresh = await rpc('init_task_network',{task_title:'One fullscreen lease',atomic_reason:'One isolated UI fixture'});
    const sid2 = fresh.structuredContent.session_id;
    await mount('full-one',fresh,'fullscreen');
    await page.waitForFunction(async sid => {
      const value = await window.readViewRegistry(sid);
      return Object.values(value.views||{}).some(view => view.display_mode === 'fullscreen');
    },sid2);
    await mount('full-two',fresh,'fullscreen');
    await page.waitForFunction(() => window.audit.modeChanges.some(change => change.frame === 'full-two' && change.mode === 'inline'));
    await page.waitForFunction(async sid => {
      const value = await window.readViewRegistry(sid);
      const modes=Object.values(value.views||{}).map(view => view.display_mode);
      return modes.filter(mode => mode === 'fullscreen').length === 1 && modes.includes('inline');
    },sid2);

    const current = await rpc('init_task_network',{task_title:'Close restores inline',atomic_reason:'One isolated UI fixture'});
    const sid3 = current.structuredContent.session_id;
    const inline = await mount('inline-close',current,'inline');
    await inline.locator('#task-title').filter({hasText:'Close restores inline'}).waitFor();
    const full = await mount('full-close',current,'fullscreen');
    await inline.locator('.card.tab-hidden').waitFor({state:'attached'});
    await rpc('update_task',{session_id:sid3,task_id:'root',execution_status:'running',assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}});
    await page.waitForFunction(async sid => {
      const value=await window.readViewRegistry(sid);
      return Object.values(value.views||{}).some(view=>view.display_mode==='fullscreen'&&view.state_revision>1);
    },sid3,{timeout:10000});
    await page.evaluate(() => {
      const frame=document.getElementById('full-close');
      frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/resource-teardown',id:999},'*');
    });
    await page.waitForFunction(async sid => {
      const value=await window.readViewRegistry(sid);
      return !Object.values(value.views||{}).some(view=>view.display_mode==='fullscreen');
    },sid3,{timeout:3000});
    await page.evaluate(() => document.getElementById('full-close').remove());
    await inline.locator('.card:not(.tab-hidden):not(.inline-stale)').waitFor({state:'visible',timeout:8000});
    await inline.locator('#refresh:not([disabled])').waitFor();
    assert.deepEqual(pageErrors,[]);
    console.log(JSON.stringify({host:'isolated simulated MCP Apps parent; not Codex desktop',cases:[
      {name:'stale_fullscreen_result_recovers_latest_data_and_visible_controls',result:'pass'},
      {name:'second_fullscreen_lease_is_demoted_to_inline',result:'pass'},
      {name:'fullscreen_progress_update_close_restores_inline',result:'pass'}
    ]},null,2));
  } finally {
    if (browser) await browser.close();
    python.stdin.end();
    python.kill();
    fs.rmSync(temp,{recursive:true,force:true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
