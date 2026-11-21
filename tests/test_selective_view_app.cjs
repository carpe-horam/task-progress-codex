/* Raw wire receipts and app-only lazy reads, with an actual isolated server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');
const assignment = {execution_mode:'subagent',model:'fixture-model',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Produce isolated evidence for lazy record loading.'};
const mainAssignment = {execution_mode:'main_session',self_execution_reason:'Isolated browser fixture acceptance'};
const results = [];
let host;
const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
async function capture(page, name) {
  if (!artifacts) return;
  fs.mkdirSync(artifacts,{recursive:true});
  await page.evaluate(() => {const iframe=document.getElementById('main');iframe.style.height=iframe.contentDocument.documentElement.scrollHeight+'px';});
  await page.screenshot({path:path.join(artifacts,name+'.png'),fullPage:true});
}
const push = (page, value) => page.evaluate(value => document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:value}, '*'), value);
async function main() {
  host = await createHost();
  const init = await host.rawRpc('init_task_network', {task_title:'App-only selective view'});
  assert.equal(init.structuredContent.acknowledged, true);
  assert(!Object.hasOwn(init.structuredContent, 'progress'));
  const binding = {session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
  const decompose = await host.rawRpc('decompose_task', {...binding,task_id:'root',tasks:[{id:'work',title:'Lazy leaf',assignment}]});
  assert(!Object.hasOwn(decompose.structuredContent, 'progress'));
  const started = await host.rawRpc('update_task', {...binding,task_id:'work',execution_status:'running',assignment});
  const attempt = started.structuredContent.attempt_id;
  assert(attempt);
  await host.rawRpc('bind_task_agent', {...binding,task_id:'work',attempt_id:attempt,agent_name:'/fixture/real-agent'});
  const submitted = await host.rawRpc('update_task', {...binding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'LAZY_EVIDENCE_SENTINEL',evidence:['fixture://lazy-record']});
  assert(!JSON.stringify(submitted).includes('LAZY_EVIDENCE_SENTINEL'));
  const bootstrap = await host.rawRpc('render_progress', {session_id:binding.session_id});
  assert.equal(bootstrap.structuredContent.bootstrap, true);
  assert.equal(bootstrap.structuredContent.view_tool, 'get_progress_view');
  assert(!Object.hasOwn(bootstrap.structuredContent, 'progress'));
  const page = await host.page();
  const frame = await host.mount(page, 'main', bootstrap, 'fullscreen');
  await frame.locator('#task-title').filter({hasText:'App-only selective view'}).waitFor();
  assert.equal(await frame.locator('#session-work > details[open]').count(), 0);
  const leaf = frame.locator('#steps > .step > details > .children > .step');
  const row=frame.locator('#step-list .plan-row[data-step-id="work"]');
  const inlineMeta=row.locator('.plan-owner');
  assert.equal(await inlineMeta.locator('[aria-label="子智能体"]').count(),1);
  assert.match(await inlineMeta.innerText(),/fixture-model.*high/);
  assert.doesNotMatch(await row.innerText(),/real-agent/);
  assert.equal(await leaf.locator('.execution-review').getAttribute('open'), null);
  assert.equal(await page.evaluate(() => window.host.calls.filter(call => call.name === 'get_progress_view' && call.args.include_history).length), 0);
  assert.equal(await page.evaluate(() => window.host.calls.filter(call => call.name === 'get_progress').length), 0);
  results.push('Raw init/update receipts and render bootstrap contain no task tree; fullscreen panel initializes through get_progress_view.');
  await capture(page,'01-default-identity-collapsed');
  await row.locator('.plan-title').click();
  await page.evaluate(() => {
    const raw = window.hostRpc;
    window.lazy = {hold:true,release:null,fail:false};
    window.hostRpc = async (name,args) => {
      if (name === 'get_progress_view' && args.include_history) {
        if (window.lazy.fail) throw new Error('Injected record read failure');
        const result = await raw(name,args);
        if (window.lazy.hold) {window.lazy.hold=false;await new Promise(resolve => window.lazy.release=resolve);}
        return result;
      }
      return raw(name,args);
    };
  });
  await frame.locator('#detail-tab-records').click();
  await page.waitForFunction(() => !!window.lazy.release);
  await page.evaluate(() => window.lazy.release());
  await frame.locator('#detail-panel-records .detail-event').first().waitFor();
  assert.match(await frame.locator('#detail-panel-result').textContent(), /LAZY_EVIDENCE_SENTINEL/);
  await frame.locator('#detail-tab-raw').click();
  const execution=frame.locator('#detail-raw-fields details[data-key$=":config"]');
  await execution.locator(':scope > summary').click();
  assert.match(await execution.innerText(),/fixture-model[\s\S]*思考强度[\s\S]*高/);
  const ids=frame.locator('#detail-raw-fields details[data-key$=":ids"]');
  await ids.locator(':scope > summary').click();
  assert.match(await ids.innerText(),/智能体[\s\S]*\/fixture\/real-agent/);
  await frame.locator('#step-detail-back').click();
  await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
  assert.equal(await page.evaluate(() => window.host.calls.filter(call => call.name === 'get_progress_view' && call.args.include_history).length), 1);
  results.push('Execution/review disclosures deduplicate one full instance read; exact-binding records survive compact refresh.');
  // A small mutation receipt refreshes the panel without becoming a blank tree.
  const receipt = await host.rawRpc('review_task', {...binding,task_id:'work',attempt_id:attempt,decision:'accept',summary:'REVIEW_ACCEPTED_SENTINEL',evidence:['fixture://review'],reviewer:mainAssignment});
  assert(!Object.hasOwn(receipt.structuredContent, 'progress'));
  await push(page, receipt);
  assert.equal(await frame.locator('#task-title').textContent(), 'App-only selective view');
  await row.locator('.plan-title').click();
  await frame.locator('#detail-panel-result').filter({hasText:'REVIEW_ACCEPTED_SENTINEL'}).waitFor();
  const overview = await host.rawRpc('get_progress', {session_id:binding.session_id});
  assert.equal(overview.structuredContent.view, 'overview');
  await push(page, overview);
  assert.equal(await leaf.count(), 1, 'selective model query does not replace full panel snapshot');
  results.push('Mutation receipts refresh the existing tree; selective model query results cannot overwrite the panel.');
  await frame.locator('#step-detail-back').click();await frame.locator('#export-all').click();
  await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const save = await page.evaluate(() => window.host.calls.findLast(call => call.name === 'save_export_files'));
  const report = await host.browser.newPage();
  await report.goto('file://' + save.result.structuredContent.files.find(file => file.name.endsWith('.html')).path);
  assert.match(await report.locator('body').textContent(), /LAZY_EVIDENCE_SENTINEL/);
  assert.match(await report.locator('body').textContent(), /REVIEW_ACCEPTED_SENTINEL/);
  results.push('Export reads full history through the app-only tool and retains execution plus review evidence.');
  await capture(page,'02-expanded-execution-records');
  // A failed bootstrap read shows its cause beside the recovery action.
  const failedPage = await host.page();
  await failedPage.evaluate(() => {
    const raw = window.hostRpc;
    window.bootstrapMode = 'reject';
    window.hostRpc = async (name,args) => {
      if (name === 'get_progress_view') {
        if (window.bootstrapMode === 'reject') throw new Error('Injected bootstrap failure');
        if (window.bootstrapMode === 'hold') {
          await new Promise(resolve => {window.releaseRead = resolve;});
          return {isError:true,content:[{type:'text',text:'Read service unavailable'}]};
        }
      }
      return raw(name,args);
    };
  });
  const failureInit = await host.rawRpc('init_task_network',{task_title:'App-only selective view',atomic_reason:'Isolated bootstrap retry fixture'});
  const failureSid = failureInit.structuredContent.session_id;
  const currentBootstrap = await host.rawRpc('render_progress',{session_id:failureSid});
  const failedFrame = await host.mount(failedPage, 'main', currentBootstrap);
  await failedFrame.locator('#empty-message').filter({hasText:'Injected bootstrap failure'}).waitFor();
  const retry = failedFrame.getByRole('button',{name:'重试',exact:true});
  assert.equal(await failedFrame.getByRole('button').count(),1);
  const initialReadCount = await failedPage.evaluate(() => window.host.calls.filter(call => call.name === 'get_progress_view').length);
  await failedPage.evaluate(() => {window.bootstrapMode='hold';});
  await retry.click();
  await failedPage.waitForFunction(() => !!window.releaseRead);
  assert.equal(await failedFrame.getByRole('button',{name:'正在重试…'}).getAttribute('aria-disabled'),'true');
  await failedPage.keyboard.press('Enter');
  await failedPage.evaluate(() => window.releaseRead());
  await failedFrame.locator('#empty-message').filter({hasText:'Read service unavailable'}).waitFor();
  assert.equal(await failedPage.evaluate(() => window.host.calls.filter(call => call.name === 'get_progress_view').length),initialReadCount+1);
  assert.equal(await failedFrame.locator('#error').isVisible(),false);
  await failedPage.evaluate(() => {window.bootstrapMode='ready';});
  await retry.click();
  await failedFrame.locator('#task-title').filter({hasText:'App-only selective view'}).waitFor();
  assert.equal(await failedFrame.locator('#empty').isVisible(),false);
  assert.equal(await failedFrame.locator('#refresh').isVisible(),true);
  results.push('Initial transport and tool errors show one reason and retry action. Repeated input while waiting sends one read; retry restores the live workspace.');
  const reconnectPage = await host.page();
  await reconnectPage.evaluate(() => {window.host.initializeError='Host offline';});
  const reconnectFrame = await host.mount(reconnectPage,'main',currentBootstrap);
  await reconnectFrame.locator('#empty-message').filter({hasText:'Host offline'}).waitFor();
  await reconnectPage.evaluate(() => {window.host.initializeError='';});
  await reconnectFrame.getByRole('button',{name:'重新连接'}).click();
  await reconnectFrame.locator('#task-title').filter({hasText:'App-only selective view'}).waitFor();
  await reconnectPage.close();
  results.push('An initial handshake failure can reconnect and load the actual task without reopening the page.');
  // Empty-state receipts have no instance ID and must still query the panel.
  const cleared = await host.rawRpc('clear_progress', {session_id:failureSid});
  assert(!Object.hasOwn(cleared.structuredContent,'progress'));
  await push(failedPage,cleared);
  await failedFrame.locator('#empty').waitFor({state:'visible'});
  const emptyBootstrap = await host.rawRpc('render_progress',{session_id:failureSid});
  const emptyPage = await host.page();
  const emptyFrame = await host.mount(emptyPage,'main',emptyBootstrap);
  await emptyPage.waitForFunction(() => window.host.calls.some(call => call.name === 'get_progress_view' && call.completedAt));
  assert.equal(await emptyFrame.locator('#empty').isVisible(),true);
  assert.equal(await emptyFrame.locator('#error').isVisible(),false);
  results.push('Clear receipts and empty-session bootstrap query successfully without an instance ID.');
  if (process.env.TASK_PROGRESS_BROWSER_ARTIFACTS) {
    const directory = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
    fs.mkdirSync(directory,{recursive:true});
    const files = ['plugins/task-progress/app.html','plugins/task-progress/server.py','tests/test_selective_view_app.cjs','tests/browser_host.cjs','tests/snapshot_rpc.cjs'];
    fs.writeFileSync(path.join(directory,'selective-view-results.json'),JSON.stringify({results,verification:'Actual isolated Python server and Chrome; simulated MCP Apps host, not native Codex',sources:Object.fromEntries(files.map(file=>[file,createHash('sha256').update(fs.readFileSync(path.join(__dirname,'..',file))).digest('hex')]))},null,2));
  }
  await emptyPage.close(); await failedPage.close(); await report.close(); await page.close();
  assert.deepEqual(host.errors, []);
  console.log(JSON.stringify({passed:results},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
