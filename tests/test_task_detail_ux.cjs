/* Task-detail UX through the production resource and an isolated real backend. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {createHost}=require('./browser_host.cjs');
const out=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS||path.resolve(__dirname,'../.cindy-build/ux-acceptance/task-details-review');
const MAIN={execution_mode:'main_session',self_execution_reason:'Isolated task-detail acceptance fixture'};
const SUB={execution_mode:'subagent',complexity:'complex',tool_identifier:'collaboration.spawn_agent',
  dispatch_text:'检查移动端任务详情，提交可复核的结果和证据。',model:'fixture-model',reasoning_effort:'high'};
const task=(id,title,parent_id,assignment=SUB)=>({id,title,parent_id,assignment});
const binding=result=>({session_id:result.structuredContent.session_id,task_instance_id:result.structuredContent.task_instance_id||result.structuredContent.progress?.task_instance_id});
const scenarios=[
  {id:'accepted',name:'accepted-resolved',title:'核对移动端导航与操作入口',summary:'已核对所有入口，手机与桌面均可完成操作。',review:'已逐项复核导航与键盘操作，验收通过。'},
  {id:'pending',name:'pending-review',title:'复核任务结果与验收证据',summary:'已整理结果与证据，等待主会话复核。'},
  {id:'running',name:'running',title:'检查不同宽度下的阅读顺序',summary:''},
  {id:'failed',name:'failed',title:'生成可交付的离线报告',summary:'生成报告时发现缺少字体资源。',failure:'字体资源缺失，报告尚未生成。'},
  {id:'accepted_open',name:'accepted-open',title:'登记暂时无法修复的外部依赖',summary:'已确认问题范围，但外部服务尚未恢复。',failure:'外部接口不可用，结果仍未解决。',review:'已核实阻塞原因，接受本次调查结果；问题继续保留。'},
  {id:'rejected',name:'rejected',title:'复核超长标题与复杂内容',summary:'已提交兼容性检查结果。',review:'退回：缺少窄屏长标题的检查证据，需要补充。'},
];

async function start(host,bind,id,assignment=SUB){
  const result=await host.rawRpc('update_task',{...bind,task_id:id,execution_status:'running',assignment});
  const attempt_id=result.structuredContent.attempt_id;
  if(assignment.execution_mode==='subagent')await host.rawRpc('bind_task_agent',{...bind,task_id:id,attempt_id,agent_name:'detail-fixture-'+id});
  return {task_id:id,attempt_id};
}
async function rename(host,bind,id,title,reason){
  const progress=(await host.rawRpc('get_progress',{...bind})).structuredContent.progress;
  return host.rawRpc('revise_task_title',{...bind,task_id:id,display_title:title,reason,expected_epoch:progress.epoch,expected_revision:progress.revision});
}
async function makeFixtures(host){
  const initial=await host.rawRpc('init_task_network',{task_title:'任务详情 · 多状态验收样例',tasks:[
    task('states','执行与复核场景',undefined,MAIN),
    ...scenarios.map(s=>task(s.id,s.title,'states')),
    task('delivered','已经验收的交付工作包',undefined,MAIN),
    task('delivery_a','检查构建产物','delivered',MAIN),task('delivery_b','核对签收记录','delivered',MAIN),
  ]});
  const current=binding(initial),attempts={};
  for(const scene of scenarios){
    const attempt=await start(host,current,scene.id);attempts[scene.id]=attempt.attempt_id;
    if(scene.id==='running')continue;
    if(scene.id==='failed'){
      await host.rawRpc('update_task',{...current,...attempt,execution_status:'failed',summary:scene.summary,failure_reason:scene.failure});
      continue;
    }
    const open=scene.id==='accepted_open';
    await host.rawRpc('update_task',{...current,...attempt,execution_status:'finished',outcome:open?'open':'resolved',
      summary:scene.summary,evidence:['fixture://detail/'+scene.id+'/submission-proof'],...(open?{failure_reason:scene.failure}:{})});
    if(scene.id!=='pending')await host.rawRpc('review_task',{...current,...attempt,decision:scene.id==='rejected'?'reject':'accept',
      reviewer:MAIN,summary:scene.review,evidence:['fixture://detail/'+scene.id+'/review-proof']});
  }
  for(const id of ['delivery_a','delivery_b']){
    const attempt=await start(host,current,id,MAIN);attempts[id]=attempt.attempt_id;
    await host.rawRpc('update_task',{...current,...attempt,execution_status:'finished',outcome:'resolved',summary:'交付检查通过。',evidence:['fixture://detail/'+id+'/proof']});
  }
  const delivered=await host.rawRpc('update_task',{...current,task_id:'delivered',execution_status:'finished',outcome:'resolved',assignment:MAIN,
    summary:'构建产物和签收记录一致，工作包已整体验收。',evidence:['fixture://detail/group/synthesis-proof']});
  attempts.delivered=delivered.structuredContent.attempt_id;
  await rename(host,current,'accepted','核对移动端导航与快捷操作','补充实际检查范围，保留原始标题。');
  const snapshot=await host.rawRpc('get_progress_view',{...current,include_history:false});
  const fullSnapshot=await host.rawRpc('get_progress_view',{...current,include_history:true});
  const nodes=snapshot.structuredContent.progress.nodes;
  const byId=Object.fromEntries(nodes.map(n=>[n.id,n]));
  assert.equal(byId.accepted.review_status,'accepted');assert.equal(byId.accepted.outcome,'resolved');
  assert.equal(byId.pending.review_status,'pending');assert.equal(byId.running.execution_status,'running');
  assert.equal(byId.failed.execution_status,'failed');assert.equal(byId.accepted_open.review_status,'accepted');
  assert.equal(byId.accepted_open.outcome,'open');assert.equal(byId.rejected.review_status,'rejected');
  assert.equal(byId.delivered.outcome,'resolved');
  const prior=binding(await host.rawRpc('init_task_network',{task_title:'已归档的交付检查',atomic_reason:'One isolated completed historical task'}));
  const historyAttempt=await start(host,prior,'root',MAIN);
  await host.rawRpc('update_task',{...prior,...historyAttempt,execution_status:'finished',outcome:'resolved',summary:'旧任务的交付证据已核对完成。',evidence:['fixture://detail/history/proof']});
  const successor=await host.rawRpc('init_task_network',{session_id:prior.session_id,task_title:'后续交付检查',tasks:[
    task('next_a','准备下一轮材料',undefined,MAIN),task('next_b','确认后续验收范围',undefined,MAIN)],
    task_relationship:'independent',new_task_reason:'Keep the completed task as an immutable detail-view history fixture'});
  const archiveBinding=binding(successor);
  const archiveSnapshot=await host.rawRpc('get_progress_view',{...archiveBinding,include_history:false});
  return {current,attempts,snapshot,fullSnapshot,archiveBinding,archiveSnapshot,prior,historyAttempt};
}

async function openDetails(locator){
  if(!await locator.evaluate(el=>el.open))await locator.locator(':scope > summary').click();
}
async function prepareScene(host,fixture,scene,width,theme){
  const page=await host.page();
  await page.setViewportSize({width,height:1000});
  await page.emulateMedia({colorScheme:theme});
  await page.addStyleTag({content:'html,body{margin:0;padding:0}iframe{width:100%!important;height:100vh!important;border:0!important}'});
  const historical=scene.id==='history';
  const frame=await host.mount(page,'details',historical?fixture.archiveSnapshot:fixture.snapshot);
  await frame.locator('html').evaluate((el,theme)=>el.dataset.theme=theme,theme);
  let target;
  if(historical){
    await frame.locator('#workspace-tab-archives').click();
    const archive=frame.locator('#task-history-list > .history-item > details').filter({has:frame.locator('.task-history-name').filter({hasText:'已归档的交付检查'})});
    await openDetails(archive);
    await archive.locator('.task-tree > li > details > summary').click();
    target=frame.locator('#step-dialog');await target.waitFor();
    return {page,frame,target};
  }else{
    const group=scene.id==='delivered';
    const trigger=frame.locator(group ? '.plan-branch[data-step-id="delivered"] > .plan-branch-head .branch-title' : '.plan-row[data-step-id="'+scene.id+'"] .plan-title');
    await trigger.click();
    target=frame.locator('#step-dialog');
    await target.waitFor();
    return {page,frame,target};
  }
  await openDetails(target);
  // Scroll the actual content viewport, retaining the surrounding task context.
  await target.evaluate(el=>el.scrollIntoView({block:'start'}));
  await page.waitForTimeout(80);
  return {page,frame,target};
}

async function auditModal({page,frame,target},fixture,scene,width,theme,name){
  const result=frame.locator('#detail-panel-result'),records=frame.locator('#detail-panel-records'),raw=frame.locator('#detail-panel-raw');
  assert(await result.isVisible());assert(!await records.isVisible());assert(!await raw.isVisible());
  const visible=await target.innerText(),attempt=scene.id==='history'?fixture.historyAttempt.attempt_id:fixture.attempts[scene.id];
  const metrics=await result.evaluate(panel=>{
    const root=document.documentElement,dialog=panel.closest('dialog'),summary=panel.querySelector('.detail-result-summary'),css=summary&&getComputedStyle(summary);
    return {documentWidth:root.clientWidth,scrollWidth:root.scrollWidth,defaultHeight:dialog.clientHeight,width:dialog.clientWidth,
      resultFont:css?.fontSize,resultBorder:css?.borderTopWidth,resultBackground:css?.backgroundColor};
  });
  assert.equal(metrics.documentWidth,width);assert.ok(metrics.scrollWidth<=width+1);
  assert.doesNotMatch(visible,/时间来源|累计执行耗时|实际智能体 ID|标题变更 · 0 次|执行编号/);
  assert(!visible.includes(attempt));assert(!visible.includes(fixture.current.task_instance_id));
  if(scene.summary){assert(visible.includes(scene.summary));assert.equal(metrics.resultBorder,'0px');assert.equal(metrics.resultBackground,'rgba(0, 0, 0, 0)');assert(parseFloat(metrics.resultFont)>=12);}
  if(scene.failure)assert(visible.includes(scene.failure));if(scene.review)assert(visible.includes(scene.review));
  const status={accepted:/已验收/,pending:/待复核|等待验收/,running:/进行中|执行中/,failed:/执行失败/,accepted_open:/报告已接受，任务仍?未解决/,rejected:/退回/};
  if(status[scene.id])assert.match(visible,status[scene.id]);
  if(['accepted','delivered'].includes(scene.id))assert.doesNotMatch(visible,/待复核|待主会话验收|不等于验收通过|尚未接受|尚无验收记录/);
  if(!['running','failed'].includes(scene.id))assert.match(await result.innerText(),/fixture:\/\/detail\//);
  assert.equal(await result.locator('a[href^="fixture:"]').count(),0);
  await page.screenshot({path:path.join(out,name+'.png')});
  await frame.locator('#detail-tab-records').click();await records.locator('.detail-event').first().waitFor();
  const events=await records.innerText();
  assert(!events.includes(attempt));assert(!events.includes(fixture.current.task_instance_id));
  assert.doesNotMatch(events,/不等于验收通过|尚未接受|尚无验收记录|标题变更 · 0 次/);
  if(scene.review)assert(visible.includes(scene.review)||events.includes(scene.review),`${scene.id} 审核记录缺失：${scene.review}`);
  if(scene.summary)assert(visible.includes(scene.summary)||events.includes(scene.summary),`${scene.id} 结果摘要缺失：${scene.summary}`);
  if(width===390&&theme==='dark')await target.screenshot({path:path.join(out,scene.name+'-390-dark-records.png')});
  await frame.locator('#detail-tab-raw').click();
  const fields=raw.locator('#detail-raw-fields'),rawDefault=await fields.innerText();
  assert(!rawDefault.includes(attempt));
  assert.doesNotMatch(await raw.innerText(),/查看完整源数据|查看当前快照数据/);
  assert.equal(await raw.locator('#raw-json, #raw-json-value').count(),0);
  const timing=fields.locator('details[data-key$=":timing"]');
  await openDetails(timing);
  assert.equal(await timing.locator('details').count(),0,'timing fields are disclosed in one step');
  assert.match(await timing.innerText(),/耗时|历时|跨度/);
  const ids=fields.locator('details[data-key$=":ids"]');
  await openDetails(ids);
  assert((await ids.innerText()).includes(attempt),'current attempt identity is a readable field');
  if(scene.id==='accepted'){
    const revisions=fields.locator('details[data-key$=":audit"]');
    await openDetails(revisions);
    assert.match(await revisions.innerText(),/补充实际检查范围，保留原始标题/);
  }
  if(scene.review){
    const checked=records.locator('.detail-event[data-attempt-id="'+attempt+'"]').filter({hasText:/验收通过|验收退回|接受报告/});
    assert(await checked.count(),'验收事件保留在记录中');
    assert.match((await result.textContent())+'\n'+(await checked.textContent()),/fixture:\/\/detail\/[^/]+\/review-proof/);
  }
  await frame.locator('#step-detail-back').click();await target.waitFor({state:'hidden'});
  return {scene:scene.name,width,theme,defaultVisibleText:visible,metrics,screenshot:name+'.png',passed:true};
}

async function auditScene(host,fixture,scene,width,theme){
  const {page,frame,target}=await prepareScene(host,fixture,scene,width,theme);
  const name=scene.name+'-'+width+'-'+theme;
  try{
    return await auditModal({page,frame,target},fixture,scene,width,theme,name);
  }finally{await page.close();}
}

async function main(){
  fs.mkdirSync(out,{recursive:true});
  const sourcePath=path.resolve(__dirname,'../plugins/task-progress/app.html');
  const sourceSha=crypto.createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex');
  const host=await createHost();
  try{
    const fixture=await makeFixtures(host);
    const manifest={source:'Production app + isolated real Python backend; not native Cindy',
      sourceAppSha256:sourceSha,resourceHtmlSha256:crypto.createHash('sha256').update(host.html).digest('hex'),
      current:fixture.current,archive:fixture.archiveBinding,attempts:fixture.attempts,scenes:scenarios.map(s=>s.name).concat(['group','historical-task'])};
    fs.writeFileSync(path.join(out,'fixture-manifest.json'),JSON.stringify(manifest,null,2));
    fs.writeFileSync(path.join(out,'fixture-snapshot.json'),JSON.stringify(fixture.snapshot,null,2));
    fs.writeFileSync(path.join(out,'fixture-full.json'),JSON.stringify(fixture.fullSnapshot,null,2));
    console.log(JSON.stringify({fixture:true,scenes:manifest.scenes,source:manifest.source},null,2));
    if(process.env.DETAIL_UX_FIXTURE_ONLY)return;
    const results=[];
    const allScenes=scenarios.concat([{id:'delivered',name:'group',summary:'构建产物和签收记录一致，工作包已整体验收。'},{id:'history',name:'historical-task',summary:'旧任务的交付证据已核对完成。'}]);
    for(const scene of allScenes){
      for(const width of [320,390,900])for(const theme of ['light','dark']){
        const result=await auditScene(host,fixture,scene,width,theme);results.push(result);
        fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({...manifest,results},null,2));
        console.log('PASS '+result.screenshot);
      }
    }
    assert.deepEqual(host.errors,[],'browser JavaScript errors');
    const endSha=crypto.createHash('sha256').update(fs.readFileSync(sourcePath)).digest('hex');
    const report={...manifest,results,passed:results.length,browserErrors:host.errors,sourceStable:sourceSha===endSha};
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
    assert.equal(sourceSha,endSha,'Production app changed during screenshot capture; rerun against final source');
    fs.writeFileSync(path.join(out,'RESULTS.md'),[
      '# Task detail acceptance',
      '',
      'Evidence: actual production UI loaded through an isolated MCP Apps browser host and real Python backend. This suite does not claim native Cindy desktop acceptance.',
      '',
      '- Passed: '+results.length+' scenarios (8 states × 3 widths × 2 themes).',
      '- Widths: 320, 390 and 900 pixels; light and dark.',
      '- Browser JavaScript errors: '+host.errors.length+'.',
      '- Production app SHA-256: `'+sourceSha+'`.',
      '- Source remained stable during this run: '+report.sourceStable+'.',
      '',
      '| Scene | Results | Default details | Execution and review records |',
      '| --- | --- | --- | --- |',
      ...allScenes.map(scene=>'| '+scene.name+' | 6 / 6 | ['+scene.name+']('+scene.name+'-390-dark.png) | [Opened records]('+scene.name+'-390-dark-records.png) |'),
      '',
      'Checks: plain result text, no horizontal overflow, readable text size, no internal IDs or raw timing by default, no contradictory accepted-state notices, accepted/open stays unresolved, reject reason preserved, real evidence and raw attempt/review identities remain accessible, title revision history remains reachable. Adapter-specific title editing is covered separately by the Cindy panel suite.',
      '',
      'See report.json for per-scene metrics and exact visible text. fixture-full.json preserves the full isolated backend receipt used by companion export checks.',
      '',
    ].join('\n'));
    console.log(JSON.stringify({passed:results.length,browserErrors:host.errors,sourceAppSha256:manifest.sourceAppSha256,report:path.join(out,'report.json')},null,2));
  }finally{await host.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
