/* Regression assertions against app.html + Python server in real Chrome.
 * All sessions and generated files are fixtures, never production cache/downloads.
 */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn, execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-ui-audit-'));
// Direct-source fixture only; the served resource injects the manifest version.
const html = fs.readFileSync(path.join(root, 'plugins/task-progress/app.html'), 'utf8').replaceAll('__TASK_PROGRESS_VERSION__', 'test-source');
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
  if (response.error || response.result?.isError) waiter.reject(new Error(response.error?.message || JSON.stringify(response.result.content)));
  else waiter.resolve(response.result);
});
function rpc(name, args) {
  const id = ++seq;
  return new Promise((resolve,reject) => { pending.set(id,{resolve,reject}); python.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args}})+'\n'); });
}
const fixtureAssignment = {execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation',model:'audit-fixture',reasoning_effort:'high'};
const record = (name,category,observed) => results.push({case:name,category,observed});

async function main() {
  browser = await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome',headless:true});
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
    window.audit={snapshots:{},modes:{},held:[],holdRead:false,calls:[]};
    addEventListener('message', async event => {
      const m=event.data;
      if (!m || m.jsonrpc!=='2.0') return;
      const frame=[...document.querySelectorAll('iframe')].find(f=>f.contentWindow===event.source);
      if (!frame) return;
      const reply=result=>event.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*');
      if (m.method==='ui/initialize') reply({protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{displayMode:window.audit.modes[frame.id]||'inline'}});
      if (m.method==='ui/notifications/initialized') event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.audit.snapshots[frame.id]},'*');
      if (m.method==='tools/call') {
        window.audit.calls.push(m.params.name);
        const result=await window.auditRpc(m.params.name,m.params.arguments);
        if (m.params.name==='get_progress' && window.audit.holdRead) window.audit.held.push({source:event.source,id:m.id,result});
        else reply(result);
      }
    });
  });
  async function mount(id,snapshot,displayMode='inline') {
    await page.evaluate(({id,snapshot,html,displayMode})=>{
      window.audit.snapshots[id]=snapshot;window.audit.modes[id]=displayMode;const f=document.createElement('iframe');f.id=id;f.name=id;f.style='display:block;width:1000px;height:650px;border:0';
      f.addEventListener('load',()=>f.contentWindow.addEventListener('message',event=>{if(event.data?.auditBarrier)f.contentWindow.parent.postMessage({auditAck:event.data.auditBarrier},'*');}));
      f.srcdoc=html;document.getElementById('frames').append(f);
    },{id,snapshot,html,displayMode});
    const frame=page.frameLocator('#'+id);
    await frame.locator('#summary:not(.hidden)').waitFor({state:'attached'});
    await page.waitForFunction(()=>window.audit.calls.includes('register_ui_view'));
    return frame;
  }
  async function notify(id,result) {
    await page.evaluate(({id,result})=>new Promise((resolve,reject)=>{
      const target=document.getElementById(id).contentWindow;const token=String(Math.random());
      const timeout=setTimeout(()=>{removeEventListener('message',ack);reject(new Error('Audit notification barrier timed out'));},3000);
      const ack=event=>{if(event.data?.auditAck===token){clearTimeout(timeout);removeEventListener('message',ack);resolve();}};
      addEventListener('message',ack);
      target.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:result},'*');
      target.postMessage({auditBarrier:token},'*');
    }),{id,result});
  }

  let initial=await rpc('init_task_network',{task_title:'浏览器隔离检查',main_session:{model:'audit-fixture',reasoning_effort:'high'}});
  const sid=initial.structuredContent.session_id;
  initial=await rpc('decompose_task',{session_id:sid,task_id:'root',tasks:[{id:'a',title:'处理 <script>文本 & 中文 😀',assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}},{id:'b',title:'验证',depends_on:['a'],assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}}]});
  const card=await mount('current',initial,'fullscreen');
  if (!await card.locator('details[data-key$=":root"]').evaluate(e=>e.open)) await card.locator('details[data-key$=":root"] > summary').click();
  assert.equal(await card.locator('.step-title').count(),3);
  assert.equal(await card.locator('#error').textContent(),'');
  assert.match(await card.locator('#network-meta').innerText(),/未经 Codex 宿主核验/);
  await card.locator('#refresh').click();
  await card.locator('#refresh:not([disabled])').waitFor();
  assert(await card.locator('details[data-key$=":root"]').getAttribute('open') !== null);
  record('initial_render_refresh_expansion','pass','Original HTML handshake, task text, dependency labels, refresh and expansion work in Chrome');

  const running=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'running',assignment:fixtureAssignment});
  const aid=running.structuredContent.progress.nodes.find(n=>n.id==='a').attempt_id;
  await notify('current',running);
  assert.match(await card.locator('details[data-key$=":a"] > summary .step-execution-meta').innerText(),/未由 Codex 核实/);
  await page.evaluate(()=>{window.audit.holdRead=true;});
  await card.locator('#refresh').click();
  await page.waitForFunction(()=>window.audit.held.length===1);
  const finished=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'finished',outcome:'resolved',attempt_id:aid,summary:'Fixture finished',evidence:['https://example.invalid/audit']});
  await notify('current',finished);
  await card.locator('li.step.completed').waitFor();
  const before=await card.locator('#fraction').textContent();
  await page.evaluate(()=>{window.audit.holdRead=false;for(const h of window.audit.held)h.source.postMessage({jsonrpc:'2.0',id:h.id,result:h.result},'*');window.audit.held=[];});
  await card.locator('#refresh:not([disabled])').waitFor();
  const after=await card.locator('#fraction').textContent();
  assert.equal(before,'1 / 2 项执行结束');
  assert.equal(after,before);
  assert.equal(await card.locator('li.step.completed:has(> details[data-key$=":a"])').count(),1);
  record('stale_refresh_is_ignored','pass',{before,after});

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
  assert.equal(await report.title(),'Task Progress · 浏览器隔离检查');
  assert((await report.locator('body').textContent()).includes('浏览器隔离检查'));
  record('export_real_click','pass',{files:exports,html_loaded:true});
  // Save only the generated fixture workbook for independent XML/package validation.
  const endedMetric=report.locator('.rp-metric').filter({hasText:'已结束'}).locator('.rp-number');
  assert((await endedMetric.textContent()).includes('1/ 2 项'));

  // Observe an actual 5-second polling interval, with backend state changed silently.
  const bRunning=await rpc('update_task',{session_id:sid,task_id:'b',execution_status:'running',assignment:fixtureAssignment});
  await card.locator('li.step.running:has(> details[data-key$=":b"])').waitFor({timeout:9000});
  record('automatic_poll','pass','Backend-only B update reaches card within 5-second polling cycle');

  const bAttempt=bRunning.structuredContent.progress.nodes.find(n=>n.id==='b').attempt_id;
  await rpc('update_task',{session_id:sid,task_id:'b',execution_status:'finished',outcome:'resolved',attempt_id:bAttempt});
  await rpc('update_task',{session_id:sid,task_id:'root',execution_status:'finished',outcome:'resolved',summary:'Browser fixture children verified',evidence:['isolated-browser-fixture'],assignment:fixtureAssignment});
  await rpc('rebuild_task_network',{session_id:sid,reason:'Fixture second plan'});
  await rpc('decompose_task',{session_id:sid,task_id:'root-e1',tasks:[{id:'c',title:'第二轮任务',assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}}]});
  let cRunning=await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'running',assignment:{...fixtureAssignment,dispatch_text:'First attempt line 1\nFirst attempt line 2'}});
  let cAttempt=cRunning.structuredContent.progress.nodes.find(n=>n.id==='c').attempt_id;
  await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'failed',attempt_id:cAttempt,summary:'Fixture failure'});
  cRunning=await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'running',retry:true,assignment:fixtureAssignment});
  cAttempt=cRunning.structuredContent.progress.nodes.find(n=>n.id==='c').attempt_id;
  await rpc('update_task',{session_id:sid,task_id:'c',execution_status:'finished',outcome:'resolved',attempt_id:cAttempt});
  await rpc('update_task',{session_id:sid,task_id:'root-e1',execution_status:'finished',outcome:'resolved',summary:'Browser fixture children verified',evidence:['isolated-browser-fixture'],assignment:fixtureAssignment});

  // Cross-task late notifications cannot restore an archived task.
  const next=await rpc('init_task_network',{session_id:sid,task_title:'新的任务'});
  const archivedC=next.structuredContent.progress.task_history[0].nodes.find(n=>n.id==='c');
  assert.deepEqual(archivedC.attempts.map(attempt=>attempt.attempt),[1,2]);
  await notify('current',next);
  await card.locator('#task-title').filter({hasText:'新的任务'}).waitFor();
  await notify('current',finished);
  assert.equal(await card.locator('#task-title').textContent(),'新的任务');
  await card.locator('#refresh').click();
  await card.locator('#refresh:not([disabled])').waitFor();
  assert.equal(await card.locator('#task-title').textContent(),'新的任务');
  record('stale_task_notification_is_ignored','pass','Archived task cannot replace the new task');
  const newest=await mount('newest',next);
  // The inline title is populated but intentionally hidden by the active fullscreen view.
  await newest.locator('#task-title').filter({hasText:'新的任务'}).waitFor({state:'attached'});
  await newest.locator('.card.tab-hidden').waitFor({state:'attached'});
  assert.equal(await card.locator('.card').evaluate(e=>e.classList.contains('compact')),false);
  await card.locator('#task-history-section > summary').click();
  assert((await card.locator('#task-history-list').textContent()).includes('浏览器隔离检查'));
  await card.locator('#export-all').click();
  await card.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const historyFile=fs.readdirSync(temp).find(n=>n.includes('新的任务')&&n.endsWith('.xlsx'));
  assert(historyFile);
  const historyHtml=fs.readdirSync(temp).find(n=>n.includes('新的任务')&&n.endsWith('.html'));
  assert(historyHtml);
  await report.goto('file://'+path.join(temp,historyHtml));
  const historyReportText=await report.locator('body').textContent();
  assert(historyReportText.includes('Fixture failure'));
  assert(historyReportText.includes('第 1 次'));
  const validation=execFileSync('python3',[path.join(__dirname,'validate_exports.py'),path.join(temp,historyFile)],{encoding:'utf8'});
  record('history_plan_retry_export','pass',JSON.parse(validation));
  const oldRemounted=await mount('old-remounted',finished);
  await oldRemounted.locator('.card.inline-stale').waitFor({timeout:9000});
  assert.equal(await newest.locator('.card').evaluate(e=>e.classList.contains('inline-stale')),false);
  record('old_card_remount_does_not_hide_newest','pass','Old source card collapses; newest remains visible');

  // Clear/recreate uses a session-wide monotonic state order, not timestamps.
  const cleared=await rpc('clear_progress',{session_id:sid});
  await notify('current',cleared);
  await card.locator('#empty').waitFor({state:'visible'});
  await notify('current',next);
  assert.equal(await card.locator('#summary').evaluate(e=>e.classList.contains('hidden')),true);
  const recreated=await rpc('init_task_network',{session_id:sid,task_title:'清除后重建'});
  await notify('current',recreated);
  await card.locator('#task-title').filter({hasText:'清除后重建'}).waitFor();
  await notify('current',cleared);
  assert.equal(await card.locator('#summary').evaluate(e=>e.classList.contains('hidden')),false);
  record('clear_and_recreate_order','pass','Old task and old clear responses cannot replace newer state');

  // Raw plan history must retain inherited dependencies in the UI and exports.
  const inheritedInit=await rpc('init_task_network',{task_title:'继承依赖检查'});
  const inheritedSid=inheritedInit.structuredContent.session_id;
  await rpc('decompose_task',{session_id:inheritedSid,task_id:'root',tasks:[{id:'a',title:'前置 A',assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}},{id:'g',title:'分组 G',depends_on:['a'],assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}}]});
  const inheritedSnapshot=await rpc('decompose_task',{session_id:inheritedSid,task_id:'g',tasks:[{id:'h',title:'继承依赖 H',assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}}]});
  const inheritedCard=await mount('inherited',inheritedSnapshot,'fullscreen');
  const replan=await rpc('rebuild_task_network',{session_id:inheritedSid,reason:'Validate raw history'});
  await notify('inherited',replan);
  assert.equal(await inheritedCard.locator('#history-list li.step.blocked:has(> details[data-key$=":h"])').count(),1);
  await inheritedCard.locator('#export-all').click();
  await inheritedCard.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const inheritedHtml=fs.readdirSync(temp).find(n=>n.includes('继承依赖检查')&&n.endsWith('.html'));
  await report.goto('file://'+path.join(temp,inheritedHtml));
  const inheritedNode=report.locator('#task-progress-report .rp-node[data-node-id="h"]');
  assert.equal(await inheritedNode.count(),1);
  const inheritedNodeText=await inheritedNode.textContent();
  assert(inheritedNodeText.includes('依赖阻塞'));
  assert(inheritedNodeText.includes('前置条件：前置 A（未满足）'), inheritedNodeText);
  assert(inheritedNodeText.includes('继承自 g'));
  record('inherited_dependencies_in_history_and_export','pass',inheritedNodeText);
  record('runtime_page_errors','pass',errors);
  assert.equal(errors.length,0);
  console.log(JSON.stringify({browser:await browser.version(),host:'simulated MCP Apps parent with isolated original server and app',cases:results},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();python.stdin.end();fs.rmSync(temp,{recursive:true,force:true});});
