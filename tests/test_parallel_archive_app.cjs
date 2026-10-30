/* Completed peer roots archive lazily; retry creates a separate frozen acceptance. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHost} = require('./browser_host.cjs');
const MAIN = {execution_mode:'main_session', self_execution_reason:'Inspect isolated archive fixture'};
const SUB = {execution_mode:'subagent', model:'fixture', reasoning_effort:'medium', tool_identifier:'collaboration.spawn_agent', dispatch_text:'Produce isolated archive evidence'};
let host;
async function main() {
  host = await createHost();
  let result = await host.rpc('init_task_network', {task_title:'仍在执行的主任务'});
  const sid = result.structuredContent.session_id;
  const foreground = {session_id:sid, task_instance_id:result.structuredContent.progress.task_instance_id};
  await host.rpc('decompose_task', {...foreground, task_id:'root', tasks:[{id:'same', title:'主任务工作', assignment:MAIN}]});
  result = await host.rpc('route_request', {...foreground, request_id:'parallel', request_text:'Independent archive fixture', task_title:'并行归档 P', mode:'parallel', reason:'Separate result', acceptance_criteria:['Retain each acceptance']});
  const iid = result.structuredContent.session_work.requests.find(item=>item.request_id==='parallel').task_instance_id;
  const peer = {session_id:sid, task_instance_id:iid};
  await host.rpc('decompose_task', {...peer, task_id:'root', tasks:[{id:'same', title:'并行工作', assignment:SUB}]});
  const page = await host.page();
  const frame = await host.mount(page, 'main', await host.rpc('get_progress_view', {session_id:sid}));
  const root = frame.locator('#steps > .step[data-task-instance="'+iid+'"]');
  async function refresh() { await frame.locator('#refresh').click(); await frame.locator('#refresh:not([disabled])').waitFor(); }
  async function start(retry=false) {
    const r = await host.rpc('update_task', {...peer, task_id:'same', execution_status:'running', assignment:SUB, retry});
    return r.structuredContent.progress.nodes.find(node=>node.id==='same').attempt_id;
  }
  async function submit(aid, label) {
    await host.rpc('bind_task_agent', {...peer, task_id:'same', attempt_id:aid, agent_name:'archive-fixture'});
    await host.rpc('update_task', {...peer, task_id:'same', attempt_id:aid, execution_status:'finished', outcome:'resolved', summary:label+' output', evidence:['fixture://'+label]});
    await refresh(); assert.equal(await root.count(),1); assert.match(await root.innerText(),/待主会话验收/);
    await host.rpc('review_task', {...peer, task_id:'same', attempt_id:aid, decision:'accept', reviewer:MAIN, summary:label+' independently reviewed', evidence:['fixture://review-'+label]});
    await refresh(); assert.equal(await root.count(),1); assert.match(await root.innerText(),/待汇总验收/);
  }
  async function synthesize(label) {
    await host.rpc('update_task', {...peer, task_id:'root', execution_status:'finished', outcome:'resolved', assignment:MAIN, summary:label+' synthesis', evidence:['fixture://root-'+label]});
    await refresh(); await root.waitFor({state:'detached'});
  }
  const oldAttempt = await start(); await submit(oldAttempt, 'completion-one'); await synthesize('completion-one');
  assert.equal(await frame.locator('#percent').textContent(),'0');
  assert.match(await frame.locator('#task-history-title').textContent(),/1 项/);
  assert.equal(await page.evaluate(()=>window.host.calls.filter(call=>call.name==='get_progress_view' && call.args.include_history).length),0,'Compact polling never loads archived evidence');
  await frame.locator('#task-history-title').click();
  const archives = frame.locator('#task-history-list > .history-item > details');
  await archives.waitFor(); await archives.locator(':scope > summary').click();
  const oldKey = await archives.getAttribute('data-key');
  assert.match(await archives.textContent(),/completion-one synthesis/);
  const frozen = (await host.rpc('get_progress_view',{session_id:sid,include_history:true})).structuredContent.progress.task_history[0];
  const newAttempt = await start(true); assert.notEqual(oldAttempt,newAttempt); await refresh(); await root.waitFor();
  assert.equal(await root.locator('.awaiting_review').count(),0);
  assert.match(await root.innerText(),/进行中/);
  assert.equal(await frame.locator('#task-history-list > .history-item > details').getAttribute('data-key'),oldKey);
  await assert.rejects(()=>host.rpc('update_task',{...peer,task_id:'same',attempt_id:oldAttempt,execution_status:'finished',outcome:'resolved',summary:'Late old output'}));
  await submit(newAttempt,'completion-two'); await synthesize('completion-two');
  await frame.locator('#task-history-title').filter({hasText:'2 项'}).waitFor();
  await archives.nth(1).waitFor();
  assert.deepEqual(await archives.evaluateAll(items=>items.map(item=>item.open)),[false,false],'New acceptance does not inherit an old disclosure');
  await archives.nth(1).locator(':scope > summary').click();
  assert.deepEqual(await archives.evaluateAll(items=>items.map(item=>item.open)),[false,true],'Each acceptance owns its expansion key');
  const keys = await archives.evaluateAll(items=>items.map(item=>item.dataset.key)); assert.equal(new Set(keys).size,2);
  const full = (await host.rpc('get_progress_view',{session_id:sid,include_history:true})).structuredContent.progress;
  assert.deepEqual(full.task_history[0],frozen); assert.equal(full.task_history.length,2);
  await refresh(); assert.equal(await archives.count(),2);
  assert.deepEqual(await archives.evaluateAll(items=>items.map(item=>item.open)),[false,true],'Same revision compact polls retain independent disclosures');
  // Old cached host payloads with only a request registry cannot resurrect completed placeholders.
  const oldCache = await host.rpc('get_progress_view',{session_id:sid,include_history:true});
  delete oldCache.structuredContent.progress.parallel_tasks;
  await page.evaluate(snapshot=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot},'*'),oldCache);
  assert.equal(await frame.locator('#steps > .parallel-placeholder').count(),0);
  await frame.locator('#export-all').click(); await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const saved = await page.evaluate(()=>window.host.calls.findLast(call=>call.name==='save_export_files').result.structuredContent.files);
  const html = saved.find(file=>file.name.endsWith('.html')).path;
  const report = await host.browser.newPage(); await report.goto('file://'+html);
  assert.equal(await report.locator('#rp-archives > .r-instance[data-archived-instance]').count(),2);
  const text = await report.locator('#rp-archives').textContent();
  assert(text.includes('completion-one synthesis') && text.includes('completion-two synthesis'));
  const xlsx = saved.find(file=>file.name.endsWith('.xlsx')).path;
  const xml = execFileSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print('\\n'.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xml')))",xlsx],{encoding:'utf8'});
  assert(xml.includes('completion-one synthesis') && xml.includes('completion-two synthesis') && xml.includes('completion-one output') && xml.includes('completion-two output'));
  // An inconsistent old completed root cannot hide a still-unresolved child.
  const inconsistent = structuredClone(oldCache), legacy = structuredClone(full.task_history[0]);
  legacy.task_instance_id='legacy-inconsistent'; legacy.request_status='completed';
  legacy.nodes.find(node=>node.id==='same').execution_status='pending';
  legacy.nodes.find(node=>node.id==='same').outcome=null;
  inconsistent.structuredContent.progress.parallel_tasks=[legacy];
  const legacyPage=await host.page();
  await legacyPage.evaluate(snapshot=>{const real=window.hostRpc;window.hostRpc=(name,args)=>name==='get_progress_view'?Promise.resolve(structuredClone(snapshot)):real(name,args);},inconsistent);
  const legacyFrame=await host.mount(legacyPage,'main',inconsistent);
  assert.equal(await legacyFrame.locator('#steps > .step[data-task-instance="legacy-inconsistent"]').count(),1);
  await legacyPage.close();
  const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
  if (artifacts) { fs.mkdirSync(artifacts,{recursive:true}); await report.screenshot({path:path.join(artifacts,'parallel-acceptance-history.png'),fullPage:true}); fs.writeFileSync(path.join(artifacts,'parallel-acceptance-history.json'),JSON.stringify(full,null,2)); }
  assert.deepEqual(host.errors,[]); await report.close(); await page.close();
  console.log('Completed peer archive, lazy history, explicit retry, frozen acceptances, unique disclosure/export keys, stale result rejection, compact refresh and complete HTML/XLSX evidence passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>host?.close());
