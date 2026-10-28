/* Responsibility state is exercised through the real isolated server, app and offline exports. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHost} = require('./browser_host.cjs');
const {createHash} = require('node:crypto');
const sourceIdentity=()=>Object.fromEntries(['plugins/task-progress/app.html','plugins/task-progress/server.py','tests/test_responsibility_app.cjs'].map(file=>[file,createHash('sha256').update(fs.readFileSync(path.join(__dirname,'..',file))).digest('hex')]));
const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const mainAssignment = {execution_mode:'main_session',self_execution_reason:'Short isolated fixture operation and main-session acceptance'};
const subAssignment = {execution_mode:'subagent',complexity:'complex',model:'fixture-model',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Implement the isolated fixture and report its evidence.'};
const results = [];
let host;
const binding = r => ({session_id:r.structuredContent.session_id,task_instance_id:r.structuredContent.progress.task_instance_id});
const node = (r,id='worker') => r.structuredContent.progress.nodes.find(n=>n.id===id);
async function screenshot(page,name) {
  if (artifacts) { fs.mkdirSync(artifacts,{recursive:true}); await page.evaluate(()=>document.querySelectorAll('iframe').forEach(frame=>{if(frame.contentDocument)frame.style.height=frame.contentDocument.documentElement.scrollHeight+'px';})); await page.screenshot({path:path.join(artifacts,name+'.png'),fullPage:true}); }
}
async function checkApp(r,expected) {
  const page=await host.page(),frame=await host.mount(page,'main',r);
  // IDs are displayed as text; select the task's own detail rather than an ancestor group.
  const detail=frame.locator('#steps .step > details').filter({has:frame.locator(':scope > summary .step-id').filter({hasText:/^worker$/})});
  await detail.locator(':scope > summary').click();
  const text=await detail.textContent();
  if(node(r).review_status==='pending') {
    const rootResponsibility=frame.locator('#steps > .step > details > .task-details > .responsibility-record');
    assert((await rootResponsibility.textContent()).includes('待综合验收（须先完成子任务复核）'));
    assert(!(await rootResponsibility.textContent()).includes('无需独立提交复核'));
  }
  for (const value of expected) assert(text.includes(value),`App missing ${value}: ${text}`);
  assert.equal(await detail.locator('.review-history').count(),1);
  await detail.locator(':scope > .task-details > .review-history').evaluate(element=>{element.open=true;element.querySelectorAll('details').forEach(item=>item.open=true);});
  await screenshot(page,'app-'+node(r).review_status+'-'+(node(r).outcome||'pending'));
  await page.close();
}
function readWorkbook(workbook) {
  return JSON.parse(execFileSync('python3',['-c',`import json,sys,zipfile,xml.etree.ElementTree as E
ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with zipfile.ZipFile(sys.argv[1]) as z:
 book=E.fromstring(z.read('xl/workbook.xml')); result={}
 for i,sheet in enumerate(book.find('s:sheets',ns)):
  rows=E.fromstring(z.read('xl/worksheets/sheet%d.xml'%(i+1))).findall('.//s:row',ns)
  result[sheet.attrib['name']]=[[''.join(c.itertext()) for c in row.findall('s:c',ns)] for row in rows]
 print(json.dumps(result,ensure_ascii=False))`,workbook],{encoding:'utf8'}));
}
async function checkExport(r,expected,{reviewRows=1}={}) {
  const {hostPage,report}=await host.exportReport(r);
  const worker=report.locator('#rp-current [data-node-id="worker"]');
  const text=await worker.textContent();
  for (const value of expected) assert(text.includes(value),`HTML export missing ${value}`);
  assert.equal(await worker.locator('.rp-review-history').count(),1);
  assert.equal(await worker.locator('.rp-review').count(),reviewRows);
  assert.equal(await report.locator('.rp-delivery').textContent(),'尚未完成');
  const call=await hostPage.evaluate(()=>window.host.calls.find(c=>c.name==='save_export_files'));
  const workbook=call.result.structuredContent.files.find(f=>f.name.endsWith('.xlsx')).path;
  const sheets=readWorkbook(workbook);
  assert(sheets['验收历史'],'independent review worksheet');
  assert.equal(sheets['验收历史'].length,reviewRows+1);
  const allCells=JSON.stringify(sheets);
  for(const value of expected.filter(v=>!v.includes('不等于'))) assert(allCells.includes(value),`Excel missing ${value}`);
  assert(sheets['执行尝试'][0].includes('提交证据'));
  assert(!sheets['执行尝试'][0].includes('验收证据'),'review evidence belongs to independent history');
  if (artifacts) {
    fs.mkdirSync(artifacts,{recursive:true});
    fs.copyFileSync(workbook,path.join(artifacts,'responsibility-'+node(r).review_status+'-'+(node(r).outcome||'pending')+'.xlsx'));
    fs.writeFileSync(path.join(artifacts,'responsibility-'+node(r).review_status+'-'+(node(r).outcome||'pending')+'.html'),await report.content());
    fs.writeFileSync(path.join(artifacts,'responsibility-'+node(r).review_status+'-'+(node(r).outcome||'pending')+'-snapshot.json'),JSON.stringify(r,null,2));
  }
  await screenshot(report,'export-'+node(r).review_status+'-'+(node(r).outcome||'pending'));
  await report.close(); await hostPage.close();
}
async function checkKnownRoleLegacy(synthesis) {
  const name=synthesis?'legacy-known-role-synthesis':'legacy-known-role-unreviewed';
  let r=await host.rpc('init_task_network',{task_title:name,tasks:[{id:'legacy-leaf',title:'Known-role legacy leaf',assignment:mainAssignment},{id:'legacy-peer',title:'Known-role legacy peer',assignment:mainAssignment}]});
  const file=path.join(host.temp,'sessions',r.structuredContent.session_id+'.json');
  const legacy=JSON.parse(fs.readFileSync(file,'utf8'));
  for(const record of Object.values(legacy.nodes)) {
    for(const key of ['planned_assignment','responsibility_version','reassignment_history','dispatch','submission','review','review_status']) delete record[key];
    const assignment={execution_mode:'main_session',model:'legacy-recorded-model',reasoning_effort:'high'};
    const kind=synthesis && record.id===legacy.root_id?'synthesis':'execution';
    Object.assign(record,{assignment,execution_status:'finished',outcome:'resolved',summary:'KNOWN_LEGACY_'+kind,evidence:['KNOWN_LEGACY_EVIDENCE_'+kind],attempt:1,attempt_id:'old-'+record.id});
    record.attempts=[{attempt:1,attempt_id:record.attempt_id,assignment,kind,execution_status:'finished',outcome:'resolved',summary:record.summary,evidence:record.evidence}];
  }
  fs.writeFileSync(file,JSON.stringify(legacy)); const original=fs.readFileSync(file,'utf8');
  r=await host.rpc('get_progress',{session_id:r.structuredContent.session_id});
  assert.equal(fs.readFileSync(file,'utf8'),original);
  assert(r.structuredContent.progress.nodes.every(record=>record.review_status==='legacy_unrecorded'));
  const page=await host.page(),frame=await host.mount(page,'main',r);
  const text=await frame.locator('#steps').textContent();
  assert(text.includes('主会话'),'retain recorded executor role');
  assert(text.includes('验收未记录'));
  assert(text.includes('旧执行记录不代表主会话已接受'));
  assert(!text.includes('无需独立提交复核'),'legacy role must not grant current-policy exemption');
  assert(!text.includes('待综合验收'),'completed legacy group must not be placed back in current review flow');
  assert.equal(await frame.locator('#steps .review-record').count(),synthesis?1:0);
  if(synthesis)assert(text.includes('综合验收已记录') && text.includes('KNOWN_LEGACY_EVIDENCE_synthesis'));
  await frame.locator('#steps details').evaluateAll(elements=>elements.forEach(element=>element.open=true));
  await screenshot(page,name+'-app'); await page.close();
  const exported=await host.exportReport(r);
  const htmlText=await exported.report.locator('#rp-current').textContent();
  assert(htmlText.includes('主会话') && htmlText.includes('验收未记录'));
  assert(htmlText.includes('旧执行记录不代表主会话已接受'));
  assert(!htmlText.includes('无需独立提交复核') && !htmlText.includes('待综合验收'));
  assert.equal(await exported.report.locator('#rp-current .rp-review').count(),synthesis?1:0);
  const saved=await exported.hostPage.evaluate(()=>window.host.calls.find(call=>call.name==='save_export_files'));
  const workbook=saved.result.structuredContent.files.find(file=>file.name.endsWith('.xlsx')).path;
  const sheets=readWorkbook(workbook);
  for(const sheetName of ['任务清单','执行尝试']) {
    const [headers,...rows]=sheets[sheetName];
    const role=headers.indexOf('执行者'), review=headers.indexOf('验收状态'), kind=headers.indexOf('类型');
    assert.equal(rows.length,3);
    for(const row of rows) {
      assert.equal(row[role],'主会话');
      assert.equal(row[review],synthesis && (kind>=0?row[kind]==='综合验收':row[headers.indexOf('任务 ID')]==='root')?'综合验收已记录':'验收未记录');
    }
  }
  const reviews=sheets['验收历史'];assert.equal(reviews.length,synthesis?2:1);
  if(synthesis) {
    assert.equal(reviews[1][reviews[0].indexOf('验收决定')],'','historical synthesis must not invent accept/reject review decisions');
    assert.equal(reviews[1][reviews[0].indexOf('记录类型')],'综合验收执行记录');
    assert(reviews[1].includes('KNOWN_LEGACY_EVIDENCE_synthesis'));
  }
  assert(!JSON.stringify(sheets).includes('无需独立提交复核') && !JSON.stringify(sheets).includes('待综合验收'));
  await exported.report.locator('#rp-current details').evaluateAll(elements=>elements.forEach(element=>element.open=true));
  await screenshot(exported.report,name+'-export');
  if(artifacts) {
    fs.copyFileSync(workbook,path.join(artifacts,name+'.xlsx'));
    fs.writeFileSync(path.join(artifacts,name+'.html'),await exported.report.content());
    fs.writeFileSync(path.join(artifacts,name+'-snapshot.json'),JSON.stringify(r,null,2));
  }
  assert.equal(fs.readFileSync(file,'utf8'),original,'browsing/export never backfills review');
  await exported.report.close(); await exported.hostPage.close();
}
async function start(r) {
  r=await host.rpc('update_task',{...binding(r),task_id:'worker',execution_status:'running',retry:node(r).execution_status==='finished',assignment:subAssignment});
  const attempt_id=node(r).attempt_id;
  r=await host.rpc('bind_task_agent',{...binding(r),task_id:'worker',attempt_id,agent_name:'fixture-agent-actual-42'});
  return r;
}
async function submit(r,outcome='resolved') {
  return host.rpc('update_task',{...binding(r),task_id:'worker',attempt_id:node(r).attempt_id,execution_status:'finished',outcome,summary:'WORKER_SUBMISSION_SUMMARY',evidence:['WORKER_SUBMISSION_EVIDENCE']});
}
async function review(r,decision) {
  return host.rpc('review_task',{...binding(r),task_id:'worker',attempt_id:node(r).attempt_id,decision,summary:'MAIN_REVIEW_'+decision,evidence:['MAIN_REVIEW_EVIDENCE_'+decision],reviewer:mainAssignment});
}
async function main() {
  const sourceAtStart=sourceIdentity(),startedAt=new Date().toISOString();
  host=await createHost();
  let r=await host.rpc('init_task_network',{task_title:'Responsibility acceptance and retry',tasks:[{id:'worker',title:'Complex worker',assignment:subAssignment},{id:'consumer',title:'Main integration',depends_on:['worker'],assignment:mainAssignment}]});
  r=await start(r); r=await submit(r);
  assert.equal(node(r).outcome,null); assert.equal(node(r).review_status,'pending');
  const expected=['待主会话验收','fixture-agent-actual-42','WORKER_SUBMISSION_SUMMARY','WORKER_SUBMISSION_EVIDENCE','执行已结束，不等于验收通过'];
  await checkApp(r,expected); await checkExport(r,expected);
  results.push('Pending result remains unresolved; app/HTML/Excel preserve bound agent, submission and independent review history.');
  r=await review(r,'reject');
  assert.equal(node(r).outcome,'open');
  await checkApp(r,['已退回','MAIN_REVIEW_reject','MAIN_REVIEW_EVIDENCE_reject']);
  await checkExport(r,['已退回','MAIN_REVIEW_reject','MAIN_REVIEW_EVIDENCE_reject']);
  r=await start(r); r=await submit(r,'open'); r=await review(r,'accept');
  assert.equal(node(r).outcome,'open','accepting an open report does not resolve the task');
  await checkApp(r,['已接受','MAIN_REVIEW_accept','MAIN_REVIEW_EVIDENCE_accept','已退回','接受的是结果报告；任务仍未解决']);
  await checkExport(r,['已接受','MAIN_REVIEW_accept','MAIN_REVIEW_EVIDENCE_accept','已退回'],{reviewRows:2});
  results.push('Rejected/retried results retain separate rejection and acceptance evidence; accepting open keeps delivery incomplete.');
  r=await start(r); r=await submit(r); r=await review(r,'accept');
  assert.equal(node(r).outcome,'resolved');
  assert.equal(node(r,'consumer').status,'ready','accepted resolved report releases its consumer');
  await checkApp(r,['已接受','MAIN_REVIEW_accept','MAIN_REVIEW_EVIDENCE_accept']);
  await checkExport(r,['已接受','MAIN_REVIEW_accept','MAIN_REVIEW_EVIDENCE_accept'],{reviewRows:3});
  results.push('Accepted resolved result releases consumer while overall delivery still waits for main integration and root synthesis.');
  r=await host.rpc('update_task',{...binding(r),task_id:'consumer',execution_status:'running',assignment:mainAssignment});
  r=await host.rpc('update_task',{...binding(r),task_id:'consumer',execution_status:'finished',attempt_id:node(r,'consumer').attempt_id,outcome:'resolved',summary:'MAIN_INTEGRATION',evidence:['MAIN_INTEGRATION_EVIDENCE']});
  r=await host.rpc('update_task',{...binding(r),task_id:'root',execution_status:'finished',outcome:'resolved',summary:'ROOT_SYNTHESIS',evidence:['ROOT_SYNTHESIS_EVIDENCE'],assignment:mainAssignment});
  const rootApp=await host.page(),rootFrame=await host.mount(rootApp,'main',r);
  const consumer= rootFrame.locator('#steps .step > details').filter({has:rootFrame.locator(':scope > summary .step-id').filter({hasText:/^consumer$/})});
  const selfText=await consumer.locator(':scope > .task-details > .responsibility-record').textContent();
  assert(selfText.includes('主会话直接执行（无需独立提交复核）'));
  assert(selfText.includes(mainAssignment.self_execution_reason)); assert(!selfText.includes('未绑定'));
  const rootDetail=rootFrame.locator('#steps > .step > details');
  assert((await rootDetail.locator(':scope > .task-details > .review-history').textContent()).includes('ROOT_SYNTHESIS_EVIDENCE'));
  await screenshot(rootApp,'main-integration-root-synthesis'); await rootApp.close();
  const rootExport=await host.exportReport(r);
  assert.equal(await rootExport.report.locator('.rp-delivery').textContent(),'已解决');
  const rootRecord=rootExport.report.locator('#rp-current [data-node-id="root"] > .rp-subdetails.rp-node-body');
  assert.equal(await rootRecord.locator('.rp-review').count(),1);
  assert((await rootRecord.locator('.rp-review').textContent()).includes('ROOT_SYNTHESIS_EVIDENCE'));
  await screenshot(rootExport.report,'root-synthesis-independent-history'); await rootExport.report.close(); await rootExport.hostPage.close();
  results.push('Main self-execution exposes its reason without missing-agent warning; actual root synthesis appears in independent acceptance history.');

  r=await host.rpc('init_task_network',{task_title:'Legacy completed record without acceptance',atomic_reason:'Legacy compatibility fixture'});
  const legacyPath=path.join(host.temp,'sessions',r.structuredContent.session_id+'.json');
  // Install a pre-protocol record only in the private fixture directory; never amend a real session.
  const legacy=JSON.parse(fs.readFileSync(legacyPath,'utf8'));
  const legacyNode=legacy.nodes[legacy.root_id];
  for(const key of ['planned_assignment','responsibility_version','reassignment_history','dispatch','submission','review','review_status']) delete legacyNode[key];
  Object.assign(legacyNode,{assignment:{},execution_status:'finished',outcome:'resolved',summary:'OLD_EXECUTION_ONLY',evidence:['OLD_EXECUTION_EVIDENCE'],attempts:[],attempt:0,attempt_id:null});
  fs.writeFileSync(legacyPath,JSON.stringify(legacy)); const originalLegacy=fs.readFileSync(legacyPath,'utf8');
  r=await host.rpc('get_progress',{session_id:r.structuredContent.session_id});
  assert.equal(fs.readFileSync(legacyPath,'utf8'),originalLegacy,'legacy read never backfills acceptance');
  const legacyPage=await host.page(),legacyFrame=await host.mount(legacyPage,'main',r);
  assert((await legacyFrame.locator('#steps').textContent()).includes('旧执行记录不代表主会话已接受'));
  assert.equal(await legacyFrame.locator('.review-record').count(),0);
  await screenshot(legacyPage,'legacy-app-no-inferred-review'); await legacyPage.close();
  const {hostPage,report}=await host.exportReport(r);
  assert.equal(await report.locator('#rp-current .rp-review').count(),0);
  assert((await report.locator('#rp-current').textContent()).includes('旧执行记录不代表主会话已接受'));
  await screenshot(report,'legacy-no-inferred-review');
  await report.close(); await hostPage.close();
  results.push('Legacy completed results remain readable with zero fabricated acceptance records and no disk backfill.');
  await checkKnownRoleLegacy(false);
  results.push('Known main-session role on legacy leaf, group and attempt never implies review exemption or a new pending-review state in app/HTML/XLSX.');
  await checkKnownRoleLegacy(true);
  results.push('Actual legacy synthesis summary/evidence remains visible independently without inventing a review decision or changing stored legacy records.');
  assert.deepEqual(host.errors,[]);
  if(artifacts){
    fs.writeFileSync(path.join(artifacts,'results.json'),JSON.stringify(results,null,2));
    fs.writeFileSync(path.join(artifacts,'source-binding.json'),JSON.stringify({startedAt,finishedAt:new Date().toISOString(),head:execFileSync('git',['rev-parse','HEAD'],{cwd:path.join(__dirname,'..'),encoding:'utf8'}).trim(),sourceAtStart,sourceAtEnd:sourceIdentity(),browser:host.browser.version(),host:'Simulated MCP Apps parent forwarding actual app resource and isolated server requests',limits:'No real Codex host identity authentication, actual subagent spawn, installed-plugin session, or user-session writes.'},null,2));
  }
  console.log(JSON.stringify({passed:results},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{await host?.close();});
