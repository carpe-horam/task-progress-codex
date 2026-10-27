/* Isolated MCP Apps lifecycle regression for stale inline to fullscreen recovery. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-view-lifecycle-'));
const html = fs.readFileSync(path.join(root, 'plugins/task-progress/app.html'), 'utf8');
const pending = new Map();
let seq = 0;
let browser;
const python = spawn('python3', ['-c', `import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('lifecycle_server',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.SESSION_DIR=pathlib.Path(sys.argv[2]);m.SESSION_DIR.mkdir(parents=True,exist_ok=True);m.main()
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
  await page.setContent('<!doctype html><title>Task Progress isolated host</title><main id="frames"></main>');
  await page.evaluate(() => {
    window.audit = {snapshots:{},modes:{},calls:[]};
    addEventListener('message', async event => {
      const message = event.data;
      if (!message || message.jsonrpc !== '2.0') return;
      const frame = [...document.querySelectorAll('iframe')].find(item => item.contentWindow === event.source);
      if (!frame) return;
      const reply = result => event.source.postMessage({jsonrpc:'2.0',id:message.id,result}, '*');
      if (message.method === 'ui/initialize') reply({protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{displayMode:window.audit.modes[frame.id]||'inline'}});
      if (message.method === 'ui/request-display-mode') {
        reply({displayMode:message.params.mode});
        event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{displayMode:message.params.mode}}, '*');
      }
      if (message.method === 'ui/notifications/initialized') {
        event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.audit.snapshots[frame.id]}, '*');
      }
      if (message.method === 'tools/call') {
        window.audit.calls.push(message.params.name);
        reply(await window.auditRpc(message.params.name,message.params.arguments));
      }
    });
  });

  async function mount(id, snapshot, mode='inline') {
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
    const first = await rpc('init_task_network',{task_title:'旧任务'});
    const sessionId = first.structuredContent.session_id;
    const oldFrame = await mount('old',first);
    const second = await rpc('init_task_network',{session_id:sessionId,task_title:'当前任务'});
    const latestFrame = await mount('latest',second);
    await latestFrame.locator('#task-title').filter({hasText:'当前任务'}).waitFor();
    const tabFrame = await mount('tab',second,'fullscreen');
    await tabFrame.locator('#task-title').filter({hasText:'当前任务'}).waitFor();
    await latestFrame.locator('.card.tab-hidden').waitFor({state:'attached',timeout:7000});
    const viewRegistry = JSON.parse(fs.readFileSync(path.join(temp,`${sessionId}.view.json`),'utf8'));
    for (const [id,view] of Object.entries(viewRegistry.views||{})) {
      if (view.display_mode === 'fullscreen') await rpc('unregister_ui_view',{session_id:sessionId,view_id:id});
    }
    await latestFrame.locator('.card:not(.tab-hidden)').waitFor({state:'visible',timeout:7000});
    await oldFrame.locator('.card.inline-stale').waitFor({timeout:9000});
    await oldFrame.locator('#compact-view:not(.hidden)').waitFor();
    await oldFrame.locator('#open-progress').click();
    await oldFrame.locator('.card:not(.inline-stale)').waitFor();
    await oldFrame.locator('#task-title').filter({hasText:'当前任务'}).waitFor();
    await oldFrame.locator('#refresh:not([disabled])').waitFor();
    await oldFrame.locator('#export-all:not(.hidden)').waitFor();
    assert.deepEqual(pageErrors,[]);
    console.log(JSON.stringify({host:'isolated simulated MCP Apps parent; not Codex desktop',cases:[
      {name:'inline_is_default_full_progress',result:'pass'},
      {name:'fullscreen_tab_hides_inline_and_close_restores_it',result:'pass'},
      {name:'old_inline_collapses',result:'pass'},
      {name:'compact_open_button_requests_fullscreen',result:'pass'},
      {name:'fullscreen_restores_and_loads_latest',result:'pass'},
      {name:'fullscreen_controls_enabled',result:'pass'}
    ]},null,2));
  } finally {
    if (browser) await browser.close();
    python.stdin.end();
    python.kill();
    fs.rmSync(temp,{recursive:true,force:true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
