/* Serve the production MCP App over an isolated Python backend and drive its package overview. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHost} = require('./browser_host.cjs');

async function setSearch(frame, value) {
  await frame.locator('#step-search-open').click();
  await frame.locator('#step-search').fill(value);
  await frame.locator('#search-dialog-done').click();
}
async function setStatus(frame, value) {
  await frame.locator('#status-chips [data-status-filter="all"]').click();
  if (value !== 'all') await frame.locator('#status-chips [data-status-filter="'+(value.startsWith('tone:') ? value : 'tone:'+value)+'"]').click();
}

const groupNames = ['目标与范围', '数据底稿', '收入与回款', '成本与效率', '渠道与获客', '销售与转化',
  '留存与续约', '产品使用', '客户支持', '区域表现', '客户访谈', '风险与行动',
  '演示稿制作', '交叉校对', '分享与权限', '交付与归档'];
const steps = ['登记资料', '核对版本', '清理明细', '确认口径', '汇总指标', '编写结论', '交叉校验', '签收记录'];
const assignment = {execution_mode:'main_session', self_execution_reason:'独立浏览器验收样本'};
const reviewerAssignment = {execution_mode:'subagent', tool_identifier:'collaboration.spawn_agent',
  dispatch_text:'检查渠道归因口径并提交复核记录'};
const task = (id,title) => ({id,title,assignment});
const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;

async function makeProject(host) {
  const initial = await host.rawRpc('init_task_network', {
    task_title:'第三季度经营复盘', tasks:groupNames.map((title,i)=>task('group-'+i,title))
  });
  const {session_id,task_instance_id} = initial.structuredContent;
  const binding = {session_id,task_instance_id};
  for (const [i,title] of groupNames.entries()) {
    await host.rawRpc('decompose_task', {...binding,task_id:'group-'+i,
      tasks:steps.map((name,j)=>{
        const leaf=task('g'+i+'-'+j,title+' · '+name);
        if (i===5 && j===5) leaf.assignment=reviewerAssignment;
        if ((i===6 || i===9) && j===7) leaf.depends_on=['g'+i+'-6'];
        return leaf;
      })});
  }
  async function start(id, executor=assignment) {
    const r = await host.rawRpc('update_task',{...binding,task_id:id,execution_status:'running',assignment:executor});
    return {task_id:id,attempt_id:r.structuredContent.attempt_id};
  }
  const completedPerPackage=[6,1,5,6,5,5,4,6,5,5,6,6,5,6,6,6];
  for (const [i,count] of completedPerPackage.entries()) {
    for (let j=0; j<count; j++) {
      const current=await start('g'+i+'-'+j);
      await host.rawRpc('update_task',{...binding,...current,execution_status:'finished',
        outcome:'resolved',summary:'已核对记录',evidence:['已检查资料版本']});
    }
  }
  const submission=await start('g5-5',reviewerAssignment);
  await host.rawRpc('bind_task_agent',{...binding,...submission,agent_name:'review-fixture'});
  await host.rawRpc('update_task',{...binding,...submission,execution_status:'finished',
    outcome:'resolved',summary:'请主会话复核销售与转化章节的指标口径',evidence:['CRM 漏斗与来源底稿已逐项核对']});
  const running = await start('g1-1');
  return {binding,running,submission,snapshot:await host.rawRpc('get_progress_view',{session_id,include_history:false})};
}

async function checkWidths(page,frame) {
  await page.evaluate(()=>{
    document.body.style.margin='0';
    const iframe=document.querySelector('#main');
    Object.assign(iframe.style,{width:'100%',height:'100vh',border:'none'});
  });
  if (artifacts) fs.mkdirSync(artifacts,{recursive:true});
  for (const width of [320,390,768,1280]) {
    await page.setViewportSize({width,height:900});
    // The outer viewport changes before the embedded document receives layout.
    await page.waitForFunction(expected =>
      document.getElementById('main').contentDocument.documentElement.getBoundingClientRect().width === expected, width);
    for (const theme of ['light','dark']) {
      await page.evaluate(theme=>document.getElementById('main').contentWindow.postMessage({
        jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{theme}
      },'*'),theme);
      await frame.locator('html[data-theme="'+theme+'"]').waitFor();
      const layout = await frame.locator('#package-grid').evaluate(grid=>{
        const cells=[...grid.querySelectorAll('.package-cell')].map(cell=>cell.getBoundingClientRect());
        const {width:viewport}=document.documentElement.getBoundingClientRect();
        return {viewport, fits:cells.every(r=>r.width>0 && r.height>0 && r.left>=0 && r.right<=viewport+1)};
      });
      assert.equal(layout.viewport,width);
      assert(layout.fits,'all 128 cells fit at '+width+'px in '+theme+' mode');
      if (artifacts) await page.screenshot({path:path.join(artifacts,'package-'+width+'-'+theme+'.png')});
    }
  }
}

module.exports = {makeProject};
if (require.main === module) (async()=>{
  const host = await createHost();
  try {
    const {binding,running,submission,snapshot} = await makeProject(host);
    const page = await host.page();
    const frame = await host.mount(page,'main',snapshot,'fullscreen');
    const overview = frame.locator('#package-overview');
    await overview.waitFor();
    const columns=frame.locator('#package-grid button');
    assert.equal(await columns.count(),16);
    assert.equal(await frame.locator('#package-grid .package-cell').count(),128);
    assert.equal(await frame.locator('#package-grid .package-cell[data-tone="accepted"]').count(),83);
    assert.equal(await frame.locator('#package-grid .package-cell[data-tone="blocked"]').count(),2);
    assert.equal(await frame.locator('#package-grid .package-cell[data-tone="review"]').count(),1);
    const rootRow=frame.locator('#steps > .main-root-flat > details > summary');
    assert.equal(await rootRow.isVisible(),false,'the repeated main-task row is not a navigable layer');
    assert.equal(await frame.locator('#task-info, #root-review').count(),0);
    assert.equal(await frame.locator('#percent').innerText(),'64');
    assert.match(await frame.locator('#fraction').innerText(),/83 \/ 128/);
    assert.equal(await frame.locator('#steps .root-metadata').count(),0,'root audit is not repeated in the work-package list');
    assert.deepEqual(await frame.locator('#steps > .main-root-flat > details > .children > .step > details > summary .task-number').allTextContents(),
      Array.from({length:16},(_,i)=>String(i+1)),'visible work packages start at 1 without the hidden-root prefix');
    await checkWidths(page,frame);

    const first=columns.nth(0),second=columns.nth(1);
    for(const width of [320,390,960]) {
      await page.setViewportSize({width,height:900});
      await frame.locator('html').evaluate(() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      await second.hover();
      const tooltip=frame.locator('#package-tooltip');
      assert.equal(await tooltip.innerText(),'数据底稿 · 8 项');
      const boxes=await tooltip.evaluate(e=>{const a=e.getBoundingClientRect(),o=e.closest('#package-overview').getBoundingClientRect();return {left:a.left,right:a.right,top:a.top,bottom:a.bottom,outerLeft:o.left,outerRight:o.right,outerTop:o.top,outerBottom:o.bottom};});
      assert(boxes.left>=boxes.outerLeft && boxes.right<=boxes.outerRight && boxes.top>=boxes.outerTop && boxes.bottom<=boxes.outerBottom,'tooltip remains inside the visible overview at '+width+'px');
      await second.focus();await first.focus();
      assert.equal(await tooltip.innerText(),'目标与范围 · 8 项','keyboard focus exposes the package name');
      await first.press('Escape');assert.equal(await tooltip.isHidden(),true);
    }
    await page.setViewportSize({width:1000,height:900});
    const treeTitle = id => frame.locator('#steps details[data-key="task:'+binding.task_instance_id+':epoch:0:'+id+'"] > summary');
    const list=frame.locator('#step-list');
    const row=id=>list.locator('.plan-row[data-step-id="'+id+'"]');
    const title=id=>row(id).locator('.plan-title');
    const numbers=()=>list.locator('.plan-number').allTextContents();
    assert.equal(await list.locator('.plan-row').count(),80,'10 root work packages render all children');
    assert.deepEqual((await numbers()).slice(0,10),['1-1','1-2','1-3','1-4','1-5','1-6','1-7','1-8','2-1','2-2']);
    assert.match(await frame.locator('#step-page-total').innerText(),/全部任务 144/);
    const found=new Set();
    for(let pageIndex=0;pageIndex<2;pageIndex++){
      const rows=await list.locator('.plan-row').evaluateAll(items=>items.map(item=>item.dataset.stepId));
      rows.forEach(id=>{assert(!found.has(id));found.add(id);});
      if(pageIndex<1)await frame.locator('#step-page-next').click();
    }
    assert.equal(found.size,128);
    assert.equal(await frame.locator('#step-page-next').isDisabled(),true);
    await second.click();
    assert.equal(await second.getAttribute('aria-pressed'),'true');
    assert(await title('g1-1').isVisible());
    assert.equal(await row('g1-1').locator('.plan-package').count(),0,'package is represented by its tree branch');
    assert.match(await frame.locator('#status-scope').innerText(),/全部叶任务/,'status counts disclose their global scope');
    const selectedDetail=frame.locator('#step-list .plan-branch[data-step-id="group-1"] > .plan-branch-head .branch-title');
    assert(await selectedDetail.isVisible());
    await selectedDetail.click();
    assert(await frame.locator('#step-detail-back').isVisible());
    await frame.locator('#step-detail-back').click();
    assert(await title('g1-1').isVisible(),'return from package detail preserves the selection');
    assert.equal(await row('g1-1').locator('.plan-number').innerText(),'2-2','package filtering preserves the original path');
    assert.equal(await title('group-0').isVisible(),false);
    await setStatus(frame, 'running');
    assert(await title('g1-1').isVisible());
    assert.equal(await title('g1-0').isVisible(),false);
    const search=frame.locator('#step-search');
    await setSearch(frame, '登记资料');
    assert(await frame.locator('#filter-empty').isVisible());
    await frame.locator('#clear-plan-filters').click();
    assert.equal(await search.inputValue(),'');
    assert.equal(await frame.locator('#status-chips [data-status-filter="all"]').getAttribute('aria-pressed'),'true');
    assert.equal(await frame.locator('#package-grid [aria-pressed="true"]').count(),0);
    await second.click();
    await setStatus(frame, 'running');
    await setSearch(frame, 'g1-1');
    assert(await title('g1-1').isVisible());
    assert.equal(await frame.locator('#filter-empty').isVisible(),false);
    await setSearch(frame, '');
    assert(await title('g1-1').isVisible());
    await first.click();
    assert.equal(await frame.locator('#status-chips [data-status-filter="tone:running"]').getAttribute('aria-pressed'),'true');
    assert(await frame.locator('#filter-empty').isVisible());
    await first.click();
    assert.equal(await frame.locator('#package-grid [aria-pressed="true"]').count(),0);
    assert(await title('g1-1').isVisible());

    await setStatus(frame, 'all');
    await second.click();
    await setSearch(frame, '不存在的资料');
    assert(await frame.locator('#filter-empty').isVisible());
    await setSearch(frame, '第三季度经营复盘');
    assert(await frame.locator('#filter-empty').isVisible(),'hidden root title cannot count as a visible search result');
    await setSearch(frame, '');
    await second.click();
    await host.rawRpc('update_task',{...binding,...running,execution_status:'finished',
      outcome:'open',summary:'底稿缺少版本号',failure_reason:'来源版本未登记',evidence:['来源清单缺少版本字段']});
    await frame.locator('#refresh').click();
    await setSearch(frame, running.task_id);
    await title(running.task_id).click();
    assert.match(await frame.locator('#detail-panel-result').innerText(),/来源版本未登记/);
    await frame.locator('#step-detail-back').click();
    await setSearch(frame, '');
    await host.rawRpc('review_task',{...binding,...submission,decision:'accept',reviewer:assignment,
      summary:'已核验销售章节的提交',evidence:['已对照 CRM 漏斗与底稿']});
    const next = await host.rpc('init_task_network',{session_id:binding.session_id,
      task_title:'下一季度经营复盘',tasks:[task('group-0','收入'),task('group-1','成本')],
      task_relationship:'independent',new_task_reason:'保留旧计划，检查新季度独立的工作包选择'});
    assert.notEqual(next.structuredContent.progress.task_instance_id,binding.task_instance_id);
    await page.evaluate(next=>document.getElementById('main').contentWindow.postMessage({
      jsonrpc:'2.0',method:'ui/notifications/tool-result',params:next
    },'*'),next);
    await frame.locator('#task-title').filter({hasText:'下一季度经营复盘'}).waitFor();
    assert.equal(await frame.locator('#package-grid [aria-pressed="true"]').count(),0);
    assert(await frame.locator('#step-list .plan-title').filter({hasText:'收入'}).isVisible());
    const archiveShortcut=frame.locator('.history-links [data-history-target="task-history-section"]');
    await archiveShortcut.click();
    assert(await frame.locator('#task-history-section').evaluate(el=>el.open));
    await frame.locator('#task-history-list').getByText('第三季度经营复盘',{exact:true}).first().waitFor();
    await frame.locator('#workspace-tab-plan').click();
    assert(await frame.locator('#workspace-tab-plan').evaluate(el=>el===document.activeElement));
    const nextBinding={session_id:binding.session_id,task_instance_id:next.structuredContent.progress.task_instance_id};
    for (const id of ['group-0','group-1']) {
      const started=await host.rawRpc('update_task',{...nextBinding,task_id:id,execution_status:'running',assignment});
      await host.rawRpc('update_task',{...nextBinding,task_id:id,attempt_id:started.structuredContent.attempt_id,
        execution_status:'finished',outcome:'resolved',summary:'合成验收样例完成',evidence:['独立测试回执']});
    }
    await frame.locator('#refresh').click();
    await frame.locator('#task-status-label').filter({hasText:'待整体验收'}).waitFor();
    assert.equal(await frame.locator('.package-focus, #package-focus-open').count(),0);
    assert.match(await frame.locator('#fraction').innerText(),/2 \/ 2/);
    const atomic=await host.rpc('init_task_network',{task_title:'独立单步验收样例',atomic_reason:'单一可检查结果'});
    const atomicPage=await host.page(),atomicFrame=await host.mount(atomicPage,'main',atomic,'fullscreen');
    assert(await atomicFrame.locator('#step-list .plan-title').isVisible(),'atomic root is a real task and stays visible');
    assert.equal(await atomicFrame.locator('#step-list .plan-number').textContent(),'1');
    assert.equal(await atomicFrame.locator('#steps > .main-root-flat').count(),0);
    assert.equal(await atomicFrame.locator('#root-review > .root-metadata').count(),0);
    await atomicPage.close();
    assert.deepEqual(host.errors,[]);
    console.log('Package distribution passed: 16 groups / 128 real leaf tasks, 4 widths × 2 themes, package / status / search, focus navigation, task replacement and archive return; grouped main-root row and detail drawer removed, atomic task preserved.');
  } finally { await host.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
