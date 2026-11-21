/* Dense, uneven work packages against the production UI and isolated backend. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
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

const out = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS || path.resolve(__dirname,
  '../.cindy-build/ux-acceptance/native-cindy/dense-packages');
const phase = process.env.DENSE_PACKAGE_PHASE || 'final';
const widths = [320,390,1280];
const checking = !phase.startsWith('baseline');
const assignment = {execution_mode:'main_session',self_execution_reason:'Isolated dense-package UI fixture'};
const reviewAssignment = {execution_mode:'subagent',tool_identifier:'collaboration.spawn_agent',
  dispatch_text:'Review the isolated dense-package fixture and submit findings'};
const task = (id,title,parent_id,extra={}) => ({id,title,assignment,...(parent_id?{parent_id}:{}),...extra});

async function makeFixture(host,sizes) {
  const setupStarted=Date.now();
  const tasks=[];
  sizes.forEach((count,group)=>{
    const parent='dense-group-'+group;
    tasks.push(task(parent,group===0?'兼容性检查与交付验收':group===1?'发布准备':'交接与归档'));
    for(let i=0;i<count;i++) {
      const index=String(i+1).padStart(3,'0');
      const title=i===count-1 ? '远端发布闸门 · '+index+' · 核对最终版本和交付签收' :
        i===Math.floor(count/2) ? '中段兼容回归 · '+index+' · 确认复杂嵌套图表在低分辨率设备上的可读性与完整验收证据' :
          '检查项 '+index+' · '+(i%9===0?'核对带有长标题、跨页面引用和多语言标注的复合工作包验收记录':'检查版式与交付记录');
      tasks.push(task('dense-'+group+'-'+i,title,parent,{
        ...(group===0&&i===2?{assignment:reviewAssignment}:{}),
        ...(group===0&&i===3?{depends_on:['dense-0-1']}:{}),
      }));
    }
  });
  const initial=await host.rawRpc('init_task_network',{task_title:sizes.length===1?'单工作包 · '+sizes[0]+'项任务':'不均衡工作包 · 240 / 3 / 5项任务',tasks});
  const {session_id,task_instance_id}=initial.structuredContent;
  const binding={session_id,task_instance_id};
  const start=async(task_id,executor=assignment)=>{
    const result=await host.rawRpc('update_task',{...binding,task_id,execution_status:'running',assignment:executor});
    return {task_id,attempt_id:result.structuredContent.attempt_id};
  };
  const accepted=await start('dense-0-0');
  await host.rawRpc('update_task',{...binding,...accepted,execution_status:'finished',outcome:'resolved',summary:'已通过样例验收',evidence:['Isolated fixture result']});
  const failed=await start('dense-0-1');
  await host.rawRpc('update_task',{...binding,...failed,execution_status:'failed',summary:'缺少源版本，等待补充',failure_reason:'来源版本未登记'});
  const review=await start('dense-0-2',reviewAssignment);
  await host.rawRpc('bind_task_agent',{...binding,...review,agent_name:'dense-review-fixture'});
  await host.rawRpc('update_task',{...binding,...review,execution_status:'finished',outcome:'resolved',summary:'已提交兼容性检查，等待复核',evidence:['Isolated review fixture']});
  const middle=Math.floor(sizes[0]/2);
  const running=await start('dense-0-'+middle);
  const snapshot=await host.rawRpc('get_progress_view',{session_id,include_history:false});
  return {binding,snapshot,middle,running,sizes,setupMs:Date.now()-setupStarted};
}

async function layout(frame) {
  return frame.locator('#package-grid').evaluate(grid=>{
    const viewport=document.documentElement.clientWidth;
    const rect=element=>{const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};};
    const cells=[...grid.querySelectorAll('.package-cell')].map(rect);
    const buttons=[...grid.querySelectorAll('button')].map(rect);
    const rows=[...document.querySelectorAll('#step-list .plan-row')].map(rect);
    return {viewport,documentWidth:document.documentElement.scrollWidth,documentHeight:document.documentElement.scrollHeight,
      grid:rect(grid),chart:rect(document.querySelector('#package-chart')),cells:cells.length,
      minCellWidth:Math.min(...cells.map(r=>r.width)),maxCellWidth:Math.max(...cells.map(r=>r.width)),
      minCellHeight:Math.min(...cells.map(r=>r.height)),buttons,
      cellsFit:cells.every(r=>r.width>0&&r.height>0&&r.x>=0&&r.right<=viewport+1),
      rowsVisibleInViewport:rows.filter(r=>r.height>0&&r.y>=0&&r.bottom<=innerHeight).length};
  });
}

async function checkPagination(host,page,frame,fixture,name) {
  const count=fixture.sizes[0], rows=frame.locator('#step-list .plan-row');
  const row=id=>frame.locator('#step-list .plan-row[data-step-id="'+id+'"]');
  const range=()=>frame.locator('#step-page-range').innerText();
  await frame.locator('#package-grid button').first().click();
  assert.equal(await rows.count(),count,'a selected work package shows all of its children');
  assert.match(await range(),/根任务 1–1 \/ 1/);
  assert(await frame.locator('#step-page-next').isHidden(),'single root does not invent child pages');
  await frame.locator('#refresh').click();await frame.locator('#refresh:not(:disabled)').waitFor();
  assert.equal(await rows.count(),count,'refresh keeps all children');
  await setSearch(frame,'中段兼容回归');assert(await row(fixture.running.task_id).isVisible());assert.equal(await rows.count(),1);
  await setSearch(frame,'dense-0-'+(count-1));assert(await row('dense-0-'+(count-1)).isVisible());
  assert.equal(await rows.locator('.plan-number').textContent(),'1-'+count,'search preserves original task path');
  await page.screenshot({path:path.join(out,phase+'-'+name+'-search-last.png')});
  await setSearch(frame,'');await setStatus(frame,'tone:pending');
  assert.equal(await rows.count(),count-5,'status filtering applies to all children');
  await setStatus(frame,'running');assert(await row(fixture.running.task_id).isVisible());assert.equal(await rows.count(),1);
  await setStatus(frame,'all');assert.equal(await rows.count(),count);
  await host.rawRpc('update_task',{...fixture.binding,...fixture.running,execution_status:'finished',outcome:'open',summary:'中段发现待处理问题',failure_reason:'缺少对照版本',evidence:['Isolated focus fixture']});
  await frame.locator('#refresh').click();
  await setSearch(frame,fixture.running.task_id);
  await row(fixture.running.task_id).locator('.plan-title').click();
  assert(await frame.locator('#step-dialog').isVisible());assert.match(await frame.locator('#detail-identity').innerText(),/中段兼容回归/);
  await frame.locator('#step-detail-back').click();
  assert.equal(await frame.locator('#package-grid button').first().getAttribute('aria-pressed'),'true');
  return {allChildrenVisible:true,refreshPreserved:true,middleSearch:true,lastIdSearch:true,globalStatusFilter:true,searchOpensMiddleRecord:true,leafCount:fixture.sizes.reduce((a,b)=>a+b,0)};
}

async function checkHistoryPaging(host,page,frame,fixture,name) {
  const scope='task:'+fixture.binding.task_instance_id, snapshot_id='dense-paging-snapshot';
  await setSearch(frame,'检查项');
  const currentRange=await frame.locator('#step-page-range').innerText();
  await host.rawRpc('snapshot_task_network',{...fixture.binding,snapshot_id,reason:'Verify historical work-package children are not paginated'});
  await page.evaluate(()=>window.host.holdExecution={frame:'main',name:'get_progress_view'});
  await frame.locator('#refresh').click();await page.waitForFunction(()=>window.host.heldExecution.length===1);
  await frame.locator('#workspace-tab-revisions').click();await page.evaluate(()=>window.host.heldExecution.shift()());
  const snapshot=frame.locator('#history-list > .history-item > details[data-key="'+scope+':snapshot:'+snapshot_id+'"]');
  await snapshot.locator(':scope > summary').click();
  const root=snapshot.locator('details[data-key="'+scope+':snapshot:'+snapshot_id+':root"]');
  if(!await root.evaluate(el=>el.open))await root.locator(':scope > summary').click();
  const group=snapshot.locator('details[data-key="'+scope+':snapshot:'+snapshot_id+':dense-group-0"]');
  if(!await group.evaluate(el=>el.open))await group.locator(':scope > summary').click();
  assert.equal(await group.locator(':scope > .children > .step:not(.page-hidden)').count(),fixture.sizes[0]);
  assert.equal(await group.locator(':scope > .child-pagination:visible').count(),0,'historical children have no pagination');
  await frame.locator('#workspace-tab-plan').click();assert.equal(await frame.locator('#step-page-range').innerText(),currentRange);
  return {firstSnapshotDuringRefresh:true,allHistoricalChildrenVisible:true,currentSelectionPreserved:true};
}

(async()=>{
  fs.mkdirSync(out,{recursive:true});
  const host=await createHost();
  const report={phase,sourceAppSha256:crypto.createHash('sha256').update(fs.readFileSync(path.resolve(__dirname,'../plugins/task-progress/app.html'))).digest('hex'),
    productionHtmlSha256:crypto.createHash('sha256').update(host.html).digest('hex'),
    source:'production app resource; isolated Python backend and headless Chrome; not a Cindy native capture',scenarios:[],issues:[]};
  let activePage=null;
  const reportPath=path.join(out,phase+'-report.json');
  fs.writeFileSync(path.join(out,phase+'-status.json'),JSON.stringify({status:'running',sourceAppSha256:report.sourceAppSha256}));
  try {
    const scenarioSizes=process.env.DENSE_PACKAGE_SIZES?JSON.parse(process.env.DENSE_PACKAGE_SIZES):[[128],[240,3,5],[1000]];
    for(const sizes of scenarioSizes) {
      const fixture=await makeFixture(host,sizes);
      const name=sizes.length===1?'single-'+sizes[0]:'uneven-240-3-5';
      const renderStarted=Date.now();
      const page=await host.page(),frame=await host.mount(page,'main',fixture.snapshot,'fullscreen');
      activePage=page;
      await page.evaluate(()=>{document.body.style.margin='0';Object.assign(document.getElementById('main').style,{width:'100%',height:'100vh',border:'none'});});
      await frame.locator('#package-overview').waitFor();
      const expected=sizes.reduce((a,b)=>a+b,0);
      assert.equal(await frame.locator('#package-grid .package-cell').count(),expected);
      for(const tone of ['accepted','running','review','blocked','open'])
        assert.equal(await frame.locator('#package-grid .package-cell[data-tone="'+tone+'"]').count(),1,'fixture includes '+tone);
      const scenario={name,sizes,setupMs:fixture.setupMs,renderMs:Date.now()-renderStarted,views:[],interaction:{}};report.scenarios.push(scenario);
      for(const width of widths) {
        await page.setViewportSize({width,height:900});
        await page.waitForFunction(width=>document.getElementById('main').contentDocument.documentElement.clientWidth===width,width);
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        for(const theme of ['light','dark']) {
          await page.evaluate(theme=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{theme}},'*'),theme);
          await frame.locator('html[data-theme="'+theme+'"]').waitFor();
          await frame.locator('body').evaluate(()=>scrollTo(0,0));
          const overview=await layout(frame);
          if(!overview.cellsFit)report.issues.push(name+' '+width+' '+theme+': cells have zero size or overflow');
          if(overview.documentWidth>width+1)report.issues.push(name+' '+width+' '+theme+': page overflows');
          if(overview.minCellWidth<6)report.issues.push(name+' '+width+' '+theme+': minimum cell width '+overview.minCellWidth.toFixed(2)+'px is below readable 6px');
          if(overview.buttons.some(button=>button.width<23.9))report.issues.push(name+' '+width+' '+theme+': small-package click target narrower than 24px');
          const prefix=phase+'-'+name+'-'+width+'-'+theme;
          await page.screenshot({path:path.join(out,prefix+'-overview.png')});
          const ledger=frame.locator('#step-list');
          await ledger.evaluate(element=>scrollTo(0,scrollY+element.getBoundingClientRect().top-12));
          const expanded=await layout(frame);
          if(checking){
            assert(expanded.rowsVisibleInViewport>=6,'list shows at least six readable steps at '+width+'px');
            assert.equal(await ledger.locator('.plan-row').count(),expected,'all descendants of visible root work packages are rendered');
          }
          await page.screenshot({path:path.join(out,prefix+'-expanded.png')});
          scenario.views.push({width,theme,overview,expanded,screens:[prefix+'-overview.png',prefix+'-expanded.png']});
        }
      }
      if(sizes[0]===1000){await page.close();continue;}
      if(checking){
        scenario.interaction=await checkPagination(host,page,frame,fixture,name);
        scenario.history=await checkHistoryPaging(host,page,frame,fixture,name);
        await page.close();continue;
      }
      const title=id=>frame.locator('#steps details[data-key="task:'+fixture.binding.task_instance_id+':epoch:0:'+id+'"] > summary');
      const search=frame.locator('#step-search');
      await setSearch(frame, '中段兼容回归');
      assert(await title('dense-0-'+fixture.middle).isVisible(),'middle task discoverable');
      await setSearch(frame, 'dense-0-'+(sizes[0]-1));
      assert(await title('dense-0-'+(sizes[0]-1)).isVisible(),'last task discoverable by ID');
      await page.screenshot({path:path.join(out,phase+'-'+name+'-search-last.png')});
      await setSearch(frame, '');
      await frame.locator('#package-grid button').first().click();
      assert.equal(await frame.locator('#package-grid button').first().getAttribute('aria-pressed'),'true');
      if(sizes.length>1)assert.equal(await title('dense-group-1').isVisible(),false,'package filter hides other packages');
      scenario.interaction={middleSearch:true,lastIdSearch:true,packageSelection:true,leafCount:expected};
      await page.close();
    }
    assert.deepEqual(host.errors,[]);
    report.pass=!checking||report.issues.length===0;
    fs.writeFileSync(reportPath,JSON.stringify(report,null,2));
    console.log(JSON.stringify({pass:report.pass,phase,source:report.source,scenarios:report.scenarios.map(s=>({name:s.name,views:s.views.length,renderMs:s.renderMs,minCellWidth:Math.min(...s.views.map(v=>v.overview.minCellWidth)),maxDocumentHeight:Math.max(...s.views.map(v=>v.overview.documentHeight)),interaction:s.interaction,history:s.history})),issues:report.issues,report:path.join(out,phase+'-report.json')},null,2));
    if(checking)assert.deepEqual(report.issues,[],'dense matrix geometry and readability');
    fs.writeFileSync(path.join(out,phase+'-status.json'),JSON.stringify({status:'passed',sourceAppSha256:report.sourceAppSha256}));
  } catch(error) {
    report.pass=false;report.error=error.message;
    fs.writeFileSync(reportPath,JSON.stringify(report,null,2));
    fs.writeFileSync(path.join(out,phase+'-status.json'),JSON.stringify({status:'failed',sourceAppSha256:report.sourceAppSha256,error:error.message}));
    if(activePage&&!activePage.isClosed())await activePage.screenshot({path:path.join(out,phase+'-failure.png')});
    throw error;
  } finally {await host.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
