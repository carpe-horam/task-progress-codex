/* Original app.html + original Python server; simulated MCP Apps host in real Chrome.
 * All sessions and generated files are fixtures, never production cache/downloads.
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const {chromium} = require('/Users/machengling/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-ui-audit-'));
const html = fs.readFileSync(path.join(root, 'plugins/task-progress/app.html'), 'utf8');
const pending = new Map(); let seq = 0; const results = []; let browser;
const python = spawn('python3', ['-c', `import importlib.util, pathlib, sys
spec=importlib.util.spec_from_file_location('audit_server',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.SESSION_DIR=pathlib.Path(sys.argv[2]);m.main()
`, path.join(root, 'plugins/task-progress/server.py'), temp]);
python.stderr.on('data', b => process.stderr.write(b));
readline.createInterface({input:python.stdout}).on('line', line => {
  const response = JSON.parse(line); const waiter = pending.get(response.id);
  if (!waiter) return; pending.delete(response.id);
  response.error ? waiter.reject(new Error(response.error.message)) : waiter.resolve(response.result);
});
function rpc(name, args) {
  const id = ++seq;
  return new Promise((resolve,reject) => { pending.set(id,{resolve,reject}); python.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args}})+'\n'); });
}
const fixtureAssignment = {execution_mode:'main_session',model:'audit-fixture',reasoning_effort:'high'};
const record = (name,category,observed) => results.push({case:name,category,observed});

async function main() {
  browser = await chromium.launch({channel:'chrome',headless:true});
  const page = await browser.newPage({viewport:{width:1100,height:1500}});
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.exposeFunction('auditRpc', async (name,args) => {
    if (name === 'save_export_files') {
      for (const file of args.files) {
        fs.writeFileSync(path.join(temp,file.name), file.encoding === 'base64' ? Buffer.from(file.content,'base64') : file.content);
      }
      return {structuredContent:{saved:true,output_directory:temp,files:args.files.map(f=>({path:path.join(temp,f.name)}))}};
    }
    return rpc(name,args);
  });
  await page.setContent('<!doctype html><title>Task Progress audit host</title><main id="frames"></main>');
  await page.evaluate(() => {
    window.audit={snapshots:{},held:[],holdRead:false,calls:[]};
    addEventListener('message', async event => {
      const m=event.data;
      if (!m || m.jsonrpc!=='2.0') return;
      const frame=[...document.querySelectorAll('iframe')].find(f=>f.contentWindow===event.source);
      if (!frame) return;
      const reply=result=>event.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*');
      if (m.method==='ui/initialize') reply({protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{displayMode:'inline'}});
      if (m.method==='ui/notifications/initialized') event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.audit.snapshots[frame.id]},'*');
      if (m.method==='tools/call') {
        window.audit.calls.push(m.params.name);
        const result=await window.auditRpc(m.params.name,m.params.arguments);
        if (m.params.name==='get_progress' && window.audit.holdRead) window.audit.held.push({source:event.source,id:m.id,result});
        else reply(result);
      }
    });
  });
  async function mount(id,snapshot) {
    await page.evaluate(({id,snapshot,html})=>{window.audit.snapshots[id]=snapshot;const f=document.createElement('iframe');f.id=id;f.name=id;f.style='display:block;width:1000px;height:650px;border:0';f.srcdoc=html;document.getElementById('frames').append(f);},{id,snapshot,html});
    const frame=page.frameLocator('#'+id);
    await frame.locator('#task-title').waitFor({state:'visible'});
    await page.waitForFunction(()=>window.audit.calls.includes('register_ui_view'));
    return frame;
  }
  async function notify(id,result) {
    await page.evaluate(({id,result})=>document.getElementById(id).contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:result},'*'),{id,result});
  }

  let initial=await rpc('init_task_network',{task_title:'浏览器隔离检查',main_session:{model:'audit-fixture',reasoning_effort:'high'}});
  const sid=initial.structuredContent.session_id;
  initial=await rpc('decompose_task',{session_id:sid,task_id:'root',tasks:[{id:'a',title:'处理 <script>文本 & 中文 😀'},{id:'b',title:'验证',depends_on:['a']}]});
  const card=await mount('current',initial);
  if (!await card.locator('details[data-key$=":root"]').evaluate(e=>e.open)) await card.locator('details[data-key$=":root"] > summary').click();
  assert.equal(await card.locator('.step-title').count(),3);
  assert.equal(await card.locator('#error').textContent(),'');
  await card.locator('#refresh').click();
  await card.locator('#refresh:not([disabled])').waitFor();
  assert(await card.locator('details[data-key$=":root"]').getAttribute('open') !== null);
  record('initial_render_refresh_expansion','pass','Original HTML handshake, task text, dependency labels, refresh and expansion work in Chrome');

  const running=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'running',assignment:fixtureAssignment});
  const aid=running.structuredContent.progress.nodes.find(n=>n.id==='a').attempt_id;
  await notify('current',running);
  await page.evaluate(()=>{window.audit.holdRead=true;});
  await card.locator('#refresh').click();
  await page.waitForFunction(()=>window.audit.held.length===1);
  const finished=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'finished',outcome:'resolved',attempt_id:aid,summary:'Fixture finished',evidence:['https://example.invalid/audit']});
  await notify('current',finished);
  await card.locator('li.step.completed').waitFor();
  const before=await card.locator('#fraction').textContent();
  await page.evaluate(()=>{window.audit.holdRead=false;for(const h of window.audit.held)h.source.postMessage({jsonrpc:'2.0',id:h.id,result:h.result},'*');window.audit.held=[];});
  await card.locator('li.step.running:has(> details[data-key$=":a"])').waitFor();
  const after=await card.locator('#fraction').textContent();
  record('stale_refresh_rolls_back_card','defect',{before,after,backend_a:'finished/resolved',displayed_a:'running'});
  await page.screenshot({path:path.join(__dirname,'2026-10-26-stale-card.png')});

  // Restore current snapshot; export exercises the real click handler and real generators.
  await notify('current',finished);
  await card.locator('li.step.completed').waitFor();
  await card.locator('#export-all').click();
  await card.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const exports=fs.readdirSync(temp).filter(n=>n.endsWith('.xlsx')||n.endsWith('.html'));
  assert.equal(exports.length,2);
  const exportHtml=exports.find(n=>n.endsWith('.html'));
  const report=await browser.newPage();
  await report.goto('file://'+path.join(temp,exportHtml));
  assert.equal(await report.title(),'Task Progress · 任务执行报告');
  assert((await report.locator('body').textContent()).includes('浏览器隔离检查'));
  record('export_real_click','pass',{files:exports,html_loaded:true});
  // Save only the generated fixture workbook for independent XML/package validation.
  fs.copyFileSync(path.join(temp,exports.find(n=>n.endsWith('.xlsx'))),path.join(__dirname,'2026-10-26-fixture-export.xlsx'));

  // Observe an actual 5-second polling interval, with backend state changed silently.
  const bRunning=await rpc('update_task',{session_id:sid,task_id:'b',execution_status:'running',assignment:fixtureAssignment});
  await card.locator('li.step.running:has(> details[data-key$=":b"])').waitFor({timeout:9000});
  record('automatic_poll','pass','Backend-only B update reaches card within 5-second polling cycle');

  const bAttempt=bRunning.structuredContent.progress.nodes.find(n=>n.id==='b').attempt_id;
  await rpc('update_task',{session_id:sid,task_id:'b',execution_status:'finished',outcome:'resolved',attempt_id:bAttempt});
  await rpc('update_task',{session_id:sid,task_id:'root',execution_status:'finished',outcome:'resolved'});
  await rpc('rebuild_task_network',{session_id:sid,reason:'Fixture second plan'});
  await rpc('decompose_task',{session_id:sid,task_id:'root-e1',tasks:[{id:'c',title:'第二轮任务'}]});
  let cRunning=await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'running',assignment:fixtureAssignment});
  let cAttempt=cRunning.structuredContent.progress.nodes.find(n=>n.id==='c').attempt_id;
  await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'failed',attempt_id:cAttempt,summary:'Fixture failure'});
  cRunning=await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'running',retry:true,assignment:fixtureAssignment});
  cAttempt=cRunning.structuredContent.progress.nodes.find(n=>n.id==='c').attempt_id;
  await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'finished',outcome:'resolved',attempt_id:cAttempt});
  await rpc('update_task',{session_id:sid,task_id:'root-e1',execution_status:'finished',outcome:'resolved'});

  // A historical tool-result iframe mounting later can supersede the newest card.
  const next=await rpc('init_task_network',{session_id:sid,task_title:'新的任务'});
  const newest=await mount('newest',next);
  await card.locator('.card.superseded').waitFor({timeout:9000});
  await newest.locator('#task-history-section > summary').click();
  assert((await newest.locator('#task-history-list').textContent()).includes('浏览器隔离检查'));
  await newest.locator('#export-all').click();
  await newest.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const historyFile=fs.readdirSync(temp).find(n=>n.includes('新的任务')&&n.endsWith('.xlsx'));
  assert(historyFile);
  fs.copyFileSync(path.join(temp,historyFile),path.join(__dirname,'2026-10-26-fixture-history-export.xlsx'));
  record('history_plan_retry_export','pass','Archived task with two plan epochs and two C attempts is displayed and exported; workbook rows validated separately');
  const oldRemounted=await mount('old-remounted',finished);
  await newest.locator('.card.superseded').waitFor({timeout:9000});
  record('old_card_remount_hides_newest','defect',{latest_card_collapsed:await newest.locator('.card').evaluate(e=>e.classList.contains('superseded')),old_card_visible:await oldRemounted.locator('#summary').isVisible()});
  record('runtime_page_errors','pass',errors);
  assert.equal(errors.length,0);
  fs.writeFileSync(path.join(__dirname,'2026-10-26-ui-results.json'),JSON.stringify({browser:await browser.version(),host:'simulated MCP Apps parent; original app HTML and original isolated server',cases:results},null,2)+'\n');
  console.log(JSON.stringify(results,null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();python.stdin.end();fs.rmSync(temp,{recursive:true,force:true});});
