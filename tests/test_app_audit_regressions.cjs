/* Positive acceptance for F03–F06 and the R01 delayed-execution protocol. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {createHost}=require('./browser_host.cjs');
const assignment={execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let host;
const artifacts=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
if(artifacts)fs.mkdirSync(artifacts,{recursive:true});
let reportNumber=0;
const results=[];
async function capture(page,name){
  if(!artifacts)return;
  await page.screenshot({path:path.join(artifacts,name+'.png'),fullPage:true});
  const trace=await page.evaluate(()=>window.host ? {calls:window.host.calls.map(c=>c.name==='save_export_files'?{...c,args:{files:c.args.files.map(({content,...file})=>({...file,contentLength:content.length}))}}:c),sizes:window.host.sizes,initializations:window.host.initializations}:null);
  if(trace)fs.writeFileSync(path.join(artifacts,name+'-trace.json'),JSON.stringify(trace,null,2));
}
const binding=r=>({session_id:r.structuredContent.session_id,task_instance_id:r.structuredContent.progress.task_instance_id});
async function endLeaf(r,id,status='finished'){
  const running=await host.rpc('update_task',{...binding(r),task_id:id,execution_status:'running',assignment});
  return host.rpc('update_task',{...binding(r),task_id:id,execution_status:status,attempt_id:running.structuredContent.progress.nodes.find(n=>n.id===id).attempt_id,...(status==='finished'?{outcome:'resolved'}:{})});
}
async function network(title){return host.rpc('init_task_network',{task_title:title,tasks:[{id:'a',title:'Leaf A',assignment},{id:'b',title:'Leaf B',assignment}]});}
async function checkReport(r,check){
  const {hostPage,report}=await host.exportReport(r);
  const number=String(++reportNumber).padStart(2,'0');
  try{return await check(report);}finally{
    if(artifacts){fs.writeFileSync(path.join(artifacts,'report-'+number+'.html'),await report.content());fs.writeFileSync(path.join(artifacts,'report-'+number+'-snapshot.json'),JSON.stringify(r,null,2));await capture(report,'report-'+number);await capture(hostPage,'report-'+number+'-app');}
    await report.close();await hostPage.close();
  }
}
async function main(){
  host=await createHost();
  assert(!host.html.includes('__TASK_PROGRESS_VERSION__'),'served resource must inject manifest version');
  let r=await host.rpc('init_progress',{task_title:'Partial linear report',legacy_linear:true,steps:['First','Second']});
  const linear=binding(r);
  r=await host.rpc('update_step',{...linear,step_index:0,status:'in_progress',assignment});
  let attempt=r.structuredContent.progress.steps[0].attempt_id;
  r=await host.rpc('update_step',{...linear,step_index:0,status:'completed',attempt_id:attempt});
  await checkReport(r,async report=>{
    assert.equal(await report.locator('.rp-delivery').textContent(),'尚未完成');
    assert.match(await report.locator('.rp-metrics').textContent(),/已结束\s*1\s*\/\s*2/);
    await report.locator('#rp-tab-table').click();
    await report.getByRole('tab',{name:'任务清单',exact:true}).click();
    const tasks=report.locator('.rp-task-table');
    assert.equal(await tasks.locator('tbody tr').count(),2);
    await tasks.locator('.rp-table-task').first().waitFor();
    assert.deepEqual(await tasks.locator('.rp-table-title').allTextContents(),['First','Second']);
  });
  r=await host.rpc('update_step',{...linear,step_index:1,status:'in_progress',assignment});
  attempt=r.structuredContent.progress.steps[1].attempt_id;
  await checkReport(r,async report=>{
    assert.equal(await report.locator('.rp-delivery').textContent(),'尚未完成');
    assert.match(await report.locator('.rp-metrics').textContent(),/1 项执行中/);
  });
  r=await host.rpc('update_step',{...linear,step_index:1,status:'completed',attempt_id:attempt});
  await checkReport(r,async report=>{
    assert.equal(await report.locator('.rp-delivery').textContent(),'已解决');
    assert.equal(await report.locator('.rp-delivery').getAttribute('data-tone'),'success');
    assert.equal(await report.locator('.rp-attention').count(),0,'accepted completed report has no current attention warning');
    assert.match(await report.locator('.rp-metrics').textContent(),/已结束\s*2\s*\/\s*2/);
  });
  results.push('F03: partial/running/completed linear reports use overall task status and normalized counts');

  for(const status of ['failed','cancelled']){
    r=await network('Root '+status);
    await endLeaf(r,'a',status);await endLeaf(r,'b',status);
    r=await host.rpc('update_task',{...binding(r),task_id:'root',execution_status:status,summary:'ROOT_TERMINATION_'+status,evidence:['ROOT_TERMINATION_EVIDENCE_'+status],assignment});
    await checkReport(r,async report=>{
      assert.equal(await report.locator('.rp-delivery').textContent(),status==='failed'?'执行失败':'已取消');
      assert.equal(await report.locator('.rp-delivery').getAttribute('data-tone'),status==='failed'?'danger':'neutral','termination cannot show as green completion');
      assert(!(await report.locator('body').textContent()).includes('仍在进行'));
      assert((await report.locator('.rp-metrics').textContent()).includes('2 项已结束未解决'));
      const root=report.locator('#rp-current [data-node-id="root"]');
      assert.equal(await root.count(),1);
      const text=await root.textContent();
      assert(text.includes('ROOT_TERMINATION_'+status));
      assert(text.includes('ROOT_TERMINATION_EVIDENCE_'+status));
      assert(text.includes('分组收尾'));
      assert.equal(await root.locator(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').count(),1);
      assert((await root.locator(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').textContent()).includes('ROOT_TERMINATION_'+status));
    });
  }
  results.push('F04/F05: failed and cancelled roots preserve delivery, termination evidence and attempts');
  r=await network('Resolved root acceptance');
  await endLeaf(r,'a');await endLeaf(r,'b');
  r=await host.rpc('update_task',{...binding(r),task_id:'root',execution_status:'finished',outcome:'resolved',summary:'ROOT_ACCEPTANCE_UNIQUE_SUMMARY',evidence:['ROOT_ACCEPTANCE_UNIQUE_EVIDENCE'],assignment});
  await checkReport(r,async report=>{
    assert.equal(await report.locator('.rp-delivery').textContent(),'已解决');
    assert.equal(await report.locator('.rp-delivery').getAttribute('data-tone'),'success');
    assert.equal(await report.locator('.rp-attention').count(),0,'accepted completed report has no current attention warning');
    const root=report.locator('#rp-current [data-node-id="root"]');
    assert.equal(await root.count(),1);
    const text=await root.textContent();
    assert(text.includes('ROOT_ACCEPTANCE_UNIQUE_SUMMARY'));
    assert(text.includes('ROOT_ACCEPTANCE_UNIQUE_EVIDENCE'));
    assert(text.includes('综合验收'));
    assert.equal(await root.locator(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').count(),1);
    assert((await root.locator(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').textContent()).includes('ROOT_ACCEPTANCE_UNIQUE_EVIDENCE'));
    assert.match(await report.locator('.rp-metrics').textContent(),/已结束\s*2\s*\/\s*2/);
    await report.locator('#rp-tab-table').click();
    await report.locator('#rp-sheet-tab-3').click();
    // A task result and its synthesis attempt may independently reference the same evidence.
    await report.locator('#rp-sheet-panel-3').getByText('ROOT_ACCEPTANCE_UNIQUE_EVIDENCE',{exact:true}).first().waitFor();
  });
  const archived=await host.rpc('init_task_network',{...binding(r),task_title:'Next after acceptance',atomic_reason:'one fixture'});
  await checkReport(archived,async report=>{
    const root=report.locator('#rp-archives [data-node-id="root"]');
    assert.equal(await root.count(),1);
    const text=await root.textContent();
    assert(text.includes('ROOT_ACCEPTANCE_UNIQUE_SUMMARY'));
    assert(text.includes('ROOT_ACCEPTANCE_UNIQUE_EVIDENCE'));
    assert(text.includes('综合验收'));
    assert.equal(await root.locator(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').count(),1);
    assert((await root.locator(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').textContent()).includes('ROOT_ACCEPTANCE_UNIQUE_EVIDENCE'));
  });
  results.push('F05: current and archived HTML include root acceptance summary/evidence/synthesis attempts without counting the root as an extra leaf');

  for(const linearMode of [false,true]){
    r=linearMode?await host.rpc('init_progress',{task_title:'Archived linear running',legacy_linear:true,steps:['Only step']}):await host.rpc('init_task_network',{task_title:'Archived HTN running',atomic_reason:'one fixture'});
    const task=binding(r);
    if(linearMode)await host.rpc('update_step',{...task,step_index:0,status:'in_progress',assignment});
    else await host.rpc('update_task',{...task,task_id:'root',execution_status:'running',assignment});
    await pause(1100);
    const archivedTask=(await host.rpc('get_progress_view',{session_id:task.session_id,include_history:true})).structuredContent.progress;
    archivedTask.archived_at=new Date().toISOString();
    // Pre-protocol archives may contain running records. Install a historical
    // snapshot in a different private session; current APIs must never archive live work.
    for(const record of [...(archivedTask.nodes || []),...(archivedTask.steps || [])]) {
      for(const key of ['planned_assignment','responsibility_version','review_status','review','submission','dispatch'])delete record[key];
    }
    r=await host.rpc('init_task_network',{task_title:'Current after archive',atomic_reason:'one fixture'});
    const currentFile=path.join(host.temp,'sessions',r.structuredContent.session_id+'.json');
    const fixture=JSON.parse(fs.readFileSync(currentFile,'utf8'));fixture.task_history=[archivedTask];fs.writeFileSync(currentFile,JSON.stringify(fixture));
    const historicalBytes=fs.readFileSync(currentFile,'utf8');
    r=await host.rpc('get_progress_view',{session_id:r.structuredContent.session_id,include_history:true});
    assert.equal(linearMode?archivedTask.steps[0].status:archivedTask.nodes[0].execution_status,linearMode?'in_progress':'running');
    const page=await host.page(),frame=await host.mount(page,'main',r);
    await frame.locator('#task-history-section > summary').click();
    await frame.locator('#task-history-list > li > details > summary').click();
    const elapsed=frame.locator('#task-history-list .step-elapsed');
    const first=await elapsed.textContent();assert.match(first,/截至归档已用/);assert.doesNotMatch(first,/已用 0秒/);
    assert((await frame.locator('#task-history-list').textContent()).includes('归档时进行中'));
    await pause(1100);await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
    assert.equal(await elapsed.textContent(),first);
    await frame.locator('body').evaluate(()=>{const now=Date.now;Date.now=()=>now()+3600000;});
    await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
    assert.equal(await elapsed.textContent(),first);
    assert.equal(fs.readFileSync(currentFile,'utf8'),historicalBytes,'reading historical running snapshots never rewrites task data');
    await capture(page,'F06-archive-'+(linearMode?'linear':'htn'));
    await page.close();
  }
  results.push('F06: HTN and linear archived running durations freeze across real wait/refresh and a one-hour page clock advance; source status stays running');

  r=await host.rpc('init_task_network',{task_title:'Mode generation order',atomic_reason:'one fixture'});
  let page=await host.page(),frame=await host.mount(page,'main',r);
  await page.waitForFunction(()=>window.host.calls.some(c=>c.name==='register_ui_view'&&c.result));
  await host.mode(page,'main','fullscreen');
  await page.waitForFunction(()=>window.host.calls.some(c=>c.name==='register_ui_view'&&c.args.display_mode==='fullscreen'&&c.result));
  await host.rpc('update_task',{...binding(r),task_id:'root',execution_status:'running',assignment});
  await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
  await page.waitForFunction(()=>window.host.calls.some(c=>c.name==='register_ui_view'&&c.args.state_revision>1&&c.result));
  let calls=await page.evaluate(()=>window.host.calls.filter(c=>c.name==='register_ui_view'));
  assert(calls.filter(c=>c.args.display_mode==='fullscreen').every(c=>c.args.mode_generation===1));
  await host.mode(page,'main','inline');await host.mode(page,'main','inline');
  await page.waitForFunction(()=>window.host.calls.some(c=>c.name==='register_ui_view'&&c.args.display_mode==='inline'&&c.args.mode_generation===2&&c.result));
  calls=await page.evaluate(()=>window.host.calls.filter(c=>c.name==='register_ui_view'));
  assert(calls.every(c=>Number.isSafeInteger(c.args.mode_generation)&&c.args.mode_generation>=0));
  assert.equal(Math.max(...calls.map(c=>c.args.mode_generation)),2);
  const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../plugins/task-progress/.codex-plugin/plugin.json'),'utf8'));
  const initialization=await page.evaluate(()=>window.host.initializations[0]);
  assert.equal(initialization.appInfo.version,manifest.version);
  await capture(page,'R01-mode-generation');
  await page.close();
  results.push('R01/R02: mode_generation starts at zero, changes only with mode, and served appInfo matches manifest version');

  r=await host.rpc('init_task_network',{task_title:'Late fullscreen execution after timeout',atomic_reason:'one fixture'});
  const sid=r.structuredContent.session_id;
  page=await host.page();await host.mount(page,'tab',r,'fullscreen');
  await page.waitForFunction(()=>window.host.calls.some(c=>c.frame==='tab'&&c.name==='register_ui_view'&&c.result));
  const main=await host.mount(page,'main',await host.rpc('get_progress_view',{session_id:sid}));
  await main.locator('.card.tab-hidden').waitFor({state:'attached'});
  await page.evaluate(()=>window.host.holdExecution={frame:'tab',name:'register_ui_view',mode:'fullscreen'});
  await page.waitForFunction(()=>window.host.heldExecution.length>0,null,{timeout:12000});
  await host.mode(page,'tab','inline');
  await page.waitForFunction(()=>window.host.calls.some(c=>c.frame==='tab'&&c.name==='register_ui_view'&&c.args.display_mode==='inline'&&c.result),null,{timeout:18000});
  await main.locator('.card:not(.tab-hidden):not(.inline-stale)').waitFor({state:'attached'});
  assert(!Object.values(host.registry(sid).views).some(v=>v.display_mode==='fullscreen'));
  await page.evaluate(()=>window.host.heldExecution.splice(0).forEach(resolve=>resolve()));
  await page.waitForFunction(()=>window.host.calls.some(c=>c.heldAt&&c.result));
  const late=await page.evaluate(()=>window.host.calls.find(c=>c.heldAt));
  assert(late.executedAt-late.sentAt>=15000);
  assert.notEqual(late.result.structuredContent.status,'registered');
  await pause(3500);
  assert(!Object.values(host.registry(sid).views).some(v=>v.display_mode==='fullscreen'));
  assert.equal(await main.locator('.card').getAttribute('class'),'card');
  await capture(page,'R01-late-execution-rejected');
  await page.close();
  results.push('R01: a real fullscreen request executed after the 15-second timeout and successful inline registration cannot revive its lease or hide the card');
  // A stale task source must still advance mode order when it releases a lease.
  // This is the server order of a delayed current-task fullscreen request, so
  // it does not need another 15-second browser timeout to reproduce the risk.
  const oldTask=await host.rpc('init_task_network',{task_title:'Cross-task mode A',atomic_reason:'one fixture'});
  const crossSid=oldTask.structuredContent.session_id;
  const viewSource=result=>({state_revision:result.structuredContent.state_revision,view_sequence:result.structuredContent.view_sequence,task_instance_id:result.structuredContent.progress.task_instance_id});
  const crossCalls=[];
  const crossRpc=async(name,args)=>{const result=await host.rpc(name,{session_id:crossSid,...args});crossCalls.push({name,args,result});return result;};
  await crossRpc('register_ui_view',{view_id:'cross-tab',...viewSource(oldTask),display_mode:'fullscreen',mode_generation:0});
  const completedOld=await host.rpc('update_task',{...binding(oldTask),task_id:'root',execution_status:'running',assignment});
  await host.rpc('update_task',{...binding(oldTask),task_id:'root',execution_status:'finished',outcome:'resolved',attempt_id:completedOld.structuredContent.progress.nodes[0].attempt_id,summary:'Old fixture task completed',evidence:['private-fixture-completion']});
  const currentTask=await host.rpc('init_task_network',{session_id:crossSid,task_title:'Cross-task mode B',atomic_reason:'one fixture'});
  await crossRpc('register_ui_view',{view_id:'cross-main',...viewSource(currentTask),display_mode:'inline',mode_generation:0});
  const delayedFullscreen={view_id:'cross-tab',...viewSource(currentTask),display_mode:'fullscreen',mode_generation:1};
  const demoted=await crossRpc('register_ui_view',{view_id:'cross-tab',...viewSource(oldTask),display_mode:'inline',mode_generation:2});
  assert.equal(demoted.structuredContent.status,'superseded');
  const delayedResult=await crossRpc('register_ui_view',delayedFullscreen);
  assert.notEqual(delayedResult.structuredContent.status,'registered');
  const remaining=host.registry(crossSid);
  assert.equal(remaining.views['cross-tab'].mode_generation,2);
  assert.equal(remaining.views['cross-tab'].display_mode,'inline');
  assert.equal(remaining.views['cross-tab'].expires_at,null);
  assert.equal((await crossRpc('check_ui_view',{view_id:'cross-main'})).structuredContent.status,'latest');
  if(artifacts)fs.writeFileSync(path.join(artifacts,'R01-cross-task-mode-trace.json'),JSON.stringify(crossCalls,null,2));
  results.push('R01 cross-task: stale-source inline generation 2 releases the lease and blocks delayed current-source fullscreen generation 1');
  assert.deepEqual(host.errors,[]);
  console.log(JSON.stringify({browser:await host.browser.version(),host:'simulated MCP Apps host; real current app/server; native Codex scheduling frequency not measured',cases:results},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(host)await host.close();});
