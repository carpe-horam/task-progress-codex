/* Exercise the expanded raw-information topics against genuine isolated task records. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');
const output = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const source = path.resolve(__dirname,'../plugins/task-progress/app.html');
const hash = value => createHash('sha256').update(value).digest('hex');
const screenshots = [];
const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
const instructions = [
  '核对签收单、归档目录、授权清单与交付记录，逐一记录匹配或缺失的文件。',
  '复核过程中保留两轮执行的证据，并指出哪些时间包含等待、哪些只表示实际工作。',
  '附件文件名：' + 'technical-archive-'.repeat(8) + 'final.xlsx',
  '请在验收清单中保留最后一项签字。'
].join('\n');
const main = {execution_mode:'main_session',self_execution_reason:'核对任务参与者并整理可追溯的来源。'};
const worker = {execution_mode:'subagent',model:'review-model',reasoning_effort:'high',
  tool_identifier:'collaboration.spawn_agent',dispatch_text:instructions};
async function seed(host) {
  const initial=await host.rawRpc('init_task_network',{task_title:'核对归档与交付记录',tasks:[
    {id:'archive',title:'归档检查',assignment:main},
    {id:'receipt',parent_id:'archive',title:'复核签收资料',assignment:worker},
    {id:'pending',parent_id:'archive',title:'准备归档附件',assignment:main}
  ]});
  const {session_id,task_instance_id}=initial.structuredContent;
  const binding={session_id,task_instance_id};
  const change=(name,fields)=>host.rawRpc(name,{...binding,task_id:'receipt',...fields});
  for (let i=1;i<=2;i++) {
    const run=await change('update_task',{execution_status:'running',assignment:worker,retry:i===2});
    const attempt_id=run.structuredContent.attempt_id;
    await change('bind_task_agent',{attempt_id,agent_name:'delivery-proof-reader'});
    // Backend timestamps have one-second precision. Keep both execution and
    // the gap before retry measurable without modifying historical timestamps.
    if(i===1) await wait(1100);
    await change('update_task',{attempt_id,execution_status:'finished',outcome:'resolved',summary:'已核对第 '+i+' 轮签收资料。',
      evidence:['第 '+i+' 轮签收单']});
    await change('review_task',{attempt_id,decision:i===1?'reject':'accept',reviewer:main,
      summary:i===1?'缺少附件清单。':'附件清单与签收单一致。',evidence:[i===1?'退回记录':'归档记录']});
    if(i===1) await wait(2100);
  }
  return binding;
}
const topic=(raw,name)=>raw.locator('details[data-key$=":'+name+'"]');
async function open(topic) {
  const heading=topic.locator(':scope > summary');
  if(!await topic.evaluate(el=>el.open)) await heading.click();
  await heading.evaluate(el=>el.scrollIntoView({block:'start',behavior:'instant'}));
  assert(await heading.evaluate(el=>el.getBoundingClientRect().top>=document.getElementById('detail-tabs').getBoundingClientRect().bottom-1),
    'sticky tabs must not cover the requested topic heading');
  return heading;
}
async function capture(page,raw,width,theme,name,section) {
  const metrics=await raw.evaluate(el=>{
    const body=el.closest('#step-dialog-body'),doc=document.documentElement;
    const bounds=el.getBoundingClientRect();
    const overflow=[...el.querySelectorAll('summary, dt, dd, li, p, code')].filter(e=>{
      if(!e.getClientRects().length) return false;
      const rect=e.getBoundingClientRect();
      return rect.right>bounds.right+1 || rect.left<bounds.left-1 || e.scrollWidth>e.clientWidth+1 && getComputedStyle(e).display!=='inline';
    }).map(e=>e.textContent.slice(0,90));
    return {width:doc.clientWidth,pageOverflow:doc.scrollWidth-doc.clientWidth,
      dialogOverflow:body.scrollWidth-body.clientWidth,overflow};
  });
  assert.equal(metrics.width,width);
  assert.equal(metrics.pageOverflow,0);
  assert.equal(metrics.dialogOverflow,0);
  assert.deepEqual(metrics.overflow,[],name+' expanded text must wrap inside the dialog');
  const file=name+'-'+width+'-'+theme+'.png';
  const entry={file,width,theme,text:await section.innerText(),metrics};
  if(output) entry.sha256=hash(await page.screenshot({path:path.join(output,file)}));
  screenshots.push(entry);
}
async function testViewport(host,binding,width,theme,checkRefresh) {
  const view=()=>host.rawRpc('get_progress_view',{...binding,include_history:true});
  const receipt=await view();
  const nodes=receipt.structuredContent.progress.nodes;
  const current=nodes.find(n=>n.id==='receipt'),pending=nodes.find(n=>n.id==='pending');
  assert.equal(current.attempts.length,2);
  assert.equal(current.attempts[0].review.decision,'reject');
  assert.equal(current.attempts[1].review.decision,'accept');
  assert(!pending.started_at && !pending.finished_at);
  const page=await host.page();
  try {
    await page.setViewportSize({width,height:width===320?640:760});
    await page.emulateMedia({colorScheme:theme});
    await page.addStyleTag({content:'html,body{margin:0;padding:0}iframe{width:100vw!important;height:100vh!important;border:0!important}'});
    const frame=await host.mount(page,'details',receipt);
    const raw=frame.locator('#detail-panel-raw'), tab=frame.locator('#detail-tab-raw'), back=frame.locator('#step-detail-back');
    async function show(id,isGroup=false) {
      const base='#step-list [data-step-id="'+id+'"]';
      const trigger=frame.locator(base+(isGroup?' > .plan-branch-head .branch-title':' .plan-title'));
      await trigger.click();await tab.click();await raw.waitFor();
      assert.equal(await raw.locator('#raw-json, #raw-json-value').count(),0);
      assert.doesNotMatch(await raw.innerText(),/查看完整源数据|查看当前快照数据/);
      return trigger;
    }
    const receiptButton=await show('receipt');
    const timing=topic(raw,'timing');
    await open(timing);
    assert.equal(await timing.locator('details').count(),0);
    assert.match(await timing.innerText(),/耗时|历时/);
    assert.match(await timing.innerText(),/重试|等待/);
    const duration=attempt=>(Date.parse(attempt.finished_at)-Date.parse(attempt.started_at))/1000;
    const activeSeconds=current.attempts.reduce((sum,attempt)=>sum+duration(attempt),0);
    const wallSeconds=(Date.parse(current.finished_at)-Date.parse(current.attempts[0].started_at))/1000;
    assert(wallSeconds>activeSeconds);
    const cell=label=>timing.locator('dt').filter({hasText:label}).locator('..').locator('dd');
    assert.match(await cell('累计执行').innerText(),new RegExp('^'+activeSeconds+'秒'));
    assert.match(await cell('整体历时').innerText(),new RegExp('^'+wallSeconds+'秒'));
    await capture(page,raw,width,theme,'retry-time',timing);
    const config=topic(raw,'config');
    const heading=await open(config);
    const identity=await config.innerText();
    assert(identity.includes('review-model'));
    assert(!identity.includes(current.attempt_id));
    assert.doesNotMatch(identity,/\bbound\b|\bsubagent\b|\bmain_session\b/);
    if(checkRefresh) {
      await heading.focus();
      const snapshot=receipt.structuredContent.progress;
      const nextTitle='复核签收资料与版本记录';
      await host.rawRpc('revise_task_title',{...binding,task_id:'receipt',display_title:nextTitle,reason:'区分复核内容',
        expected_epoch:snapshot.epoch,expected_revision:snapshot.revision});
      const updated=await view();
      await page.evaluate(data=>document.querySelector('#details').contentWindow.postMessage(
        {jsonrpc:'2.0',method:'ui/notifications/tool-result',params:data},'*'),updated);
      await frame.locator('#detail-name').filter({hasText:nextTitle}).waitFor();
      assert(await heading.evaluate(el=>el===document.activeElement),'focus stays on configuration after a changed revision');
      assert(await timing.evaluate(e=>e.open) && await config.evaluate(e=>e.open),'open sections survive a changed revision');
    }
    await capture(page,raw,width,theme,'execution',config);
    const ids=topic(raw,'ids');
    await open(ids);
    const identifiers=await ids.innerText();
    for(const value of [current.attempt_id,'delivery-proof-reader',binding.task_instance_id]) {
      assert(identifiers.includes(value) || identity.includes(value),value+' missing from execution or identities');
    }
    await capture(page,raw,width,theme,'identifiers',ids);
    const dispatch=topic(raw,'dispatch');
    await open(dispatch);
    for(const line of instructions.split('\n')) assert((await dispatch.innerText()).includes(line),'full dispatch is readable on first expansion');
    await capture(page,raw,width,theme,'dispatch',dispatch);
    await page.keyboard.press('Escape');
    assert(await receiptButton.evaluate(el=>el===document.activeElement));
    await show('archive',true);
    const groupTiming=topic(raw,'timing');await open(groupTiming);
    assert.equal(await groupTiming.locator('details').count(),0);
    assert((await groupTiming.innerText()).includes(current.title) || (await groupTiming.innerText()).includes(current.display_title));
    assert.match(await groupTiming.innerText(),/观察|子任务/);
    await capture(page,raw,width,theme,'group-time',groupTiming);
    await back.click();await show('pending');
    const missingTime=topic(raw,'timing');
    if(await missingTime.count()) await open(missingTime);
    assert.doesNotMatch(await raw.innerText(),/\d{2}\/\d{2} \d{2}:\d{2}|0秒|0 秒/,'unstarted task must not invent time');
    await capture(page,raw,width,theme,'unstarted',raw);
    await back.click();
  } finally {await page.close();}
}
(async()=>{
  if(output) fs.mkdirSync(output,{recursive:true});
  const sourceHash=hash(fs.readFileSync(source)), host=await createHost();
  try {
    const binding=await seed(host);
    for(const width of [320,390,960]) for(const theme of ['light','dark']) {
      await testViewport(host,binding,width,theme,width===320 && theme==='light');
    }
    assert.deepEqual(host.errors,[]);
    assert.equal(sourceHash,hash(fs.readFileSync(source)),'shared app changed during this run');
    const report={appSha256:sourceHash,resourceSha256:hash(host.html),source:'Isolated production resource and genuine Python task transitions',screenshots};
    if(output) fs.writeFileSync(path.join(output,'raw-details.json'),JSON.stringify(report,null,2)+'\n');
    console.log('Passed expanded retry, configuration, long dispatch, derived group and unstarted task across six viewports/themes. '+sourceHash);
  } finally {await host.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
