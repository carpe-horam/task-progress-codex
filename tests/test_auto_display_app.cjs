/* Real app/backend + isolated simulated host; not native Codex tab evidence. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-auto-display-'));
const html = fs.readFileSync(path.join(root, 'plugins/task-progress/app.html'), 'utf8');
const pending = new Map();
let sequence = 0, browser;
const python = spawn('python3', ['-B', '-c', `import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location('auto_display_server',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.LEGACY_DB_PATH=pathlib.Path(sys.argv[2])/'missing.sqlite3';m.SESSION_DIR=pathlib.Path(sys.argv[2]);m.main()
`, path.join(root, 'plugins/task-progress/server.py'), temp]);
python.stderr.on('data', data => process.stderr.write(data));
readline.createInterface({input: python.stdout}).on('line', line => {
  const result = JSON.parse(line), waiter = pending.get(result.id);
  if (!waiter) return;
  pending.delete(result.id);
  if (result.error || result.result?.isError) waiter.reject(new Error(JSON.stringify(result)));
  else waiter.resolve(result.result);
});
function rpc(name, args) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    python.stdin.write(JSON.stringify({jsonrpc:'2.0', id, method:'tools/call', params:{name, arguments:args}}) + '\n');
  });
}
async function main() {
  browser = await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome', headless:true});
  const page = await browser.newPage({viewport:{width:1200,height:1000}});
  page.setDefaultTimeout(10000);
  const errors = [], cases = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.exposeFunction('backendRpc', rpc);
  await page.setContent('<main id="frames"></main>');
  await page.evaluate(() => {
    window.audit = {config:{}, calls:[], requests:[], modes:{}};
    addEventListener('message', async event => {
      const message = event.data;
      if (!message || message.jsonrpc !== '2.0') return;
      const frame = [...document.querySelectorAll('iframe')].find(f => f.contentWindow === event.source);
      if (!frame) return;
      const c = audit.config[frame.id];
      const notify = (method, params) => event.source.postMessage({jsonrpc:'2.0',method,params}, '*');
      const reply = result => event.source.postMessage({jsonrpc:'2.0',id:message.id,result}, '*');
      if (message.method === 'ui/initialize') {
        if (c.early) notify('ui/notifications/tool-result', c.bootstrap);
        reply({hostCapabilities:{},hostContext:{displayMode:c.initial || 'inline', availableDisplayModes:c.modes || ['inline','fullscreen']}});
      }
      if (message.method === 'ui/notifications/initialized' && !c.early) notify('ui/notifications/tool-result',c.bootstrap);
      if (message.method === 'ui/request-display-mode') {
        audit.requests.push({id:frame.id,mode:message.params.mode});
        if (c.error) {
          event.source.postMessage({jsonrpc:'2.0',id:message.id,error:{code:-32601,message:'unsupported'}}, '*'); return;
        }
        const mode = c.decline ? 'inline' : message.params.mode;
        audit.modes[frame.id] = mode;
        reply(c.ackOnly ? {} : {mode});
      }
      if (message.method === 'tools/call') {
        audit.calls.push({id:frame.id,name:message.params.name,args:message.params.arguments});
        reply(await backendRpc(message.params.name,message.params.arguments));
      }
    });
  });
  async function fixture() {
    const result = await rpc('init_task_network',{task_title:'Synthetic display fixture',atomic_reason:'One display fixture'});
    const sid = result.structuredContent.session_id;
    return {sid,bootstrap:await rpc('render_progress',{session_id:sid})};
  }
  async function mount(id, bootstrap, config={}) {
    await page.evaluate(({id,bootstrap,config,html}) => {
      audit.config[id] = {...config,bootstrap};
      const frame = document.createElement('iframe'); frame.id=id; frame.style='width:1100px;height:650px';frame.srcdoc=html;
      document.getElementById('frames').append(frame);
    },{id,bootstrap,config,html});
    const frame = page.frameLocator('#'+id);
    await frame.locator('#task-title').filter({hasText:'Synthetic display fixture'}).waitFor();
    await page.waitForFunction(id => audit.calls.some(c=>c.id===id&&c.name==='register_ui_view'),id);
    return frame;
  }
  async function count(id, mode='fullscreen') {
    return page.evaluate(({id,mode})=>audit.requests.filter(r=>r.id===id&&r.mode===mode).length,{id,mode});
  }
  async function notify(id, method, params) {
    await page.evaluate(({id,method,params})=>document.getElementById(id).contentWindow.postMessage({jsonrpc:'2.0',method,params},'*'),{id,method,params});
  }
  try {
    const a = await fixture();
    const frame = await mount('automatic',a.bootstrap);
    assert.equal(await count('automatic'),1);
    await frame.locator('#open-progress-panel.hidden').waitFor({state:'attached'});
    cases.push('inline automatically requests once and honors actual response mode without a notification');
    await notify('automatic','ui/notifications/host-context-changed',{displayMode:'inline'});
    await frame.locator('#open-progress-panel:not(.hidden)').waitFor();
    await notify('automatic','ui/notifications/tool-result',a.bootstrap);
    await page.waitForTimeout(5500);
    assert.equal(await count('automatic'),1);
    await frame.locator('#open-progress-panel').click();
    assert.equal(await count('automatic'),2);
    cases.push('close + replay + real polling does not reopen; explicit click reopens');
    await mount('duplicate',a.bootstrap);
    assert.equal(await count('duplicate'),0);
    const another = await rpc('render_progress',{session_id:a.sid});
    await mount('lease',another);
    assert.equal(await count('lease'),0);
    cases.push('same intent remount and new render with existing fullscreen lease do not duplicate');

    const b = await fixture();
    await mount('early',b.bootstrap,{early:true});
    assert.equal(await count('early'),1);
    cases.push('bootstrap before initialize reply loads and requests once');
    const c = await fixture();
    await mount('native-preferred',c.bootstrap,{initial:'fullscreen'});
    assert.equal(await count('native-preferred'),0);
    const remount = await mount('fullscreen-replay',c.bootstrap,{initial:'fullscreen'});
    await remount.locator('#open-progress-panel:not(.hidden)').waitFor({state:'attached'});
    assert.equal(await count('fullscreen-replay','inline'),1);
    cases.push('host-preferred fullscreen needs no promotion; replay requests demotion');

    const d = await fixture();
    const newest = await rpc('render_progress',{session_id:d.sid});
    await mount('old',d.bootstrap);
    assert.equal(await count('old'),0);
    await mount('new',newest);
    assert.equal(await count('new'),1);
    cases.push('old unconsumed render cannot auto-open after newer issuance');
    for (const [id,config] of [['unsupported',{modes:['inline']}],['declined',{decline:true}],['error',{error:true}],['ack',{ackOnly:true}]]) {
      const item = await fixture();
      const f = await mount(id,item.bootstrap,config);
      assert.equal(await count(id),id==='unsupported'?0:1);
      await f.locator('#open-progress-panel:not(.hidden)').waitFor();
      await notify(id,'ui/notifications/tool-result',item.bootstrap);
      assert.equal(await count(id),id==='unsupported'?0:1);
    }
    cases.push('unsupported, declined, rejected and ack-only responses retain inline and do not retry');
    const delayed = page.frameLocator('#ack');
    await delayed.locator('#display-feedback:not(.hidden)').waitFor();
    await notify('ack','ui/notifications/host-context-changed',{displayMode:'fullscreen'});
    await delayed.locator('#display-feedback.hidden').waitFor({state:'attached'});
    assert.equal(await count('ack'),1);
    cases.push('delayed fullscreen confirmation clears earlier inline fallback feedback');
    const declinedSid = await page.evaluate(()=>audit.config.declined.bootstrap.structuredContent.session_id);
    await page.evaluate(()=>{ audit.config.declined.decline = false; });
    const reopened = await rpc('render_progress',{session_id:declinedSid});
    await notify('declined','ui/notifications/tool-result',reopened);
    await page.frameLocator('#declined').locator('#open-progress-panel.hidden').waitFor({state:'attached'});
    assert.equal(await count('declined'),2);
    cases.push('explicit new render after a refusal can reopen the same mounted app');
    assert.deepEqual(errors,[]);
    const audit = await page.evaluate(()=>audit);
    assert.ok(audit.calls.filter(c=>c.name==='get_progress_view').length>0);
    assert.equal(audit.calls.some(c=>c.name==='get_progress'),false);
    cases.push('panel uses app-only reader; no model full query');
    const report = {host:'isolated simulated MCP Apps parent; not native Codex',cases,errors,requests:audit.requests};
    if (process.env.TASK_PROGRESS_BROWSER_ARTIFACTS) {
      fs.mkdirSync(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,{recursive:true});
      fs.writeFileSync(path.join(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,'auto-display-browser.json'),JSON.stringify(report,null,2));
    }
    console.log(JSON.stringify(report,null,2));
  } finally {
    await browser?.close(); python.stdin.end(); python.kill();fs.rmSync(temp,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;python.kill();});
