/* Production task tree with real isolated task hierarchy, parallel roots and paging. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');

const out = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const assignment={execution_mode:'main_session',self_execution_reason:'逐项核对展示结果与交付记录。'};
const title='核对多语言长标题在七层任务树中的完整显示，记录竖屏设备上所有编号、状态、结果和补充说明';
const ancestors=['release','documents','regional','locale','layout','proof'];
const groups=['发布验收','交付文档','地区版本','语言版本','版式核对','内容校样'];
const cases=[],photos=[],metrics=[];
const hash=data=>createHash('sha256').update(data).digest('hex');
const task=(id,title,parent_id)=>({id,title,assignment,...(parent_id?{parent_id}:{})});
const branch=(frame,id,instance)=>frame.locator('#step-list .plan-branch[data-instance="'+instance+'"][data-step-id="'+id+'"]');
const leaf=(frame,id,instance)=>frame.locator('#step-list .plan-row[data-instance="'+instance+'"][data-step-id="'+id+'"]');
const toggle=group=>group.locator(':scope > .plan-branch-head .branch-toggle');
const branchNumber = group => group.locator(':scope > .plan-branch-head .branch-number').innerText();
const detailNumber = async frame => (await frame.locator('#detail-name').innerText()).split(' ')[0];
const deepPath = '1-1-1-1-1-1-1';
async function iconStyle(row) {
  const icon = row.locator(':scope > .step-icon');
  assert(await icon.isVisible(), 'task row has a visible state icon');
  return icon.evaluate(e => {
    const style = getComputedStyle(e);
    return {
      color: style.color, background: style.backgroundColor, border: style.borderColor,
      radius: style.borderRadius, width: e.offsetWidth, height: e.offsetHeight,
    };
  });
}
async function expand(group,want) {
  const control=toggle(group);
  await control.waitFor();
  if ((await control.getAttribute('aria-expanded')==='true')!==want) await control.click();
  assert.equal(await control.getAttribute('aria-expanded'),String(want));
}

async function mount(host,snapshot) {
  const page=await host.page();
  await page.setViewportSize({width:390,height:780});
  await page.addStyleTag({content:'html,body{margin:0;padding:0;color-scheme:light dark}iframe{width:100vw!important;height:100vh!important;border:none!important}'});
  const frame=await host.mount(page,'main',snapshot,'fullscreen');
  return {page,frame};
}
async function viewport(page,width,theme){
  const height=width===320?640:width===390?780:760;
  await page.setViewportSize({width,height});await page.emulateMedia({colorScheme:theme});
  await page.waitForFunction(({width,theme})=>{const win=document.querySelector('iframe').contentWindow;return win.innerWidth===width&&win.document.documentElement.dataset.theme===theme;},{width,theme});
}
async function capture(page,frame,anchor,name) {
  await anchor.evaluate(e=>e.scrollIntoView({block:'start'}));
  const measured=await frame.locator('html').evaluate(e=>({width:innerWidth,scrollWidth:e.scrollWidth,scrollY:scrollY}));
  assert.equal(measured.width,measured.scrollWidth,name+' horizontal overflow');
  if (out) {
    const file=name+'.png',data=await page.screenshot({path:path.join(out,file)});
    photos.push({file,sha256:hash(data),...measured});
  }
}
async function deepAndParallel(host) {
  const tasks=ancestors.map((id,i)=>task(id,groups[i],ancestors[i-1]));
  tasks.push(task('same',title,ancestors.at(-1)),task('notes','核对注释与术语清单',ancestors.at(-1)),task('other','签收归档'),task('handover','复核交付签收记录','other'));
  const receipt=await host.rawRpc('init_task_network',{task_title:'交付检查与归档',tasks});
  const {session_id,task_instance_id:main}=receipt.structuredContent;
  const peerReceipt=await host.rawRpc('route_request',{session_id,task_instance_id:main,request_id:'independent',request_text:'独立复核签名',task_title:'并行签名验证',mode:'parallel',reason:'独立验证签名验证器',acceptance_criteria:['签名结果一致']});
  const peer=peerReceipt.structuredContent.request_task_instance_id;
  await host.rawRpc('decompose_task',{session_id,task_instance_id:peer,task_id:'root',tasks:[task('release','并行签名校验'),task('same','独立复核包的签名来源','release')]});
  const attempt=await host.rawRpc('update_task',{session_id,task_instance_id:main,task_id:'same',execution_status:'running',assignment});
  assert(attempt.structuredContent.attempt_id);
  const snapshot=await host.rawRpc('get_progress_view',{session_id,include_history:false});
  const {page,frame}=await mount(host,snapshot);
  try {
    const mainRoot=branch(frame,'release',main), peerRoot=branch(frame,'root',peer);
    const rootBranches = frame.locator('#step-list > .plan-branch');
    await mainRoot.waitFor();
    assert.deepEqual(await rootBranches.evaluateAll(items => items.map(e => [e.dataset.instance,e.dataset.stepId])), [
      [main,'release'], [main,'other'], [peer,'root'],
    ], 'main packages and the named parallel instance share the first level');
    assert.deepEqual(await rootBranches.locator(':scope > .plan-branch-head .branch-number').allInnerTexts(),['1','2','3']);
    assert.match(await peerRoot.locator(':scope > .plan-branch-head').innerText(), /并行签名验证/);
    for(const [i,id] of ancestors.entries()) {
      const group=branch(frame,id,main);
      await expand(group,true);
      assert.equal(await branchNumber(group),Array(i+1).fill('1').join('-'));
    }
    const deepest=leaf(frame,'same',main);
    await deepest.waitFor();
    const ancestry=await deepest.evaluate(e=>{const path=[];for(let b=e.parentElement.closest('.plan-branch');b;b=b.parentElement.closest('.plan-branch'))path.unshift(b.dataset.stepId);return path;});
    assert.deepEqual(ancestry,ancestors,'DOM nesting starts at the main package and preserves every level');
    for(const [width,theme] of [[320,'light'],[320,'dark'],[390,'light'],[390,'dark'],[1100,'light'],[1100,'dark']]){
      await viewport(page,width,theme);
      await capture(page,frame,mainRoot.locator(':scope > .plan-branch-head'),'deep-tree-'+width+'-'+theme);
      const m=await deepest.locator('.plan-title').evaluate(e=>{
        const r=e.getBoundingClientRect(), s=getComputedStyle(e);
        const n=e.parentElement.querySelector('.plan-number').getBoundingClientRect();
        return {x:r.x,width:r.width,right:r.right,height:r.height,client:e.clientWidth,scroll:e.scrollWidth,
          font:parseFloat(s.fontSize),text:e.textContent,numberOffsetY:n.y-r.y,numberRight:n.right,
          numberLeft:n.left,numberBottom:n.bottom,titleTop:r.top};
      });
      const inline=Math.abs(m.numberOffsetY)<m.font && m.numberRight<=m.x;
      const above=m.numberBottom<=m.titleTop+1 && Math.abs(m.numberLeft-m.x)<1;
      assert(inline || above,'complete path has its own space adjacent to the deep title');
      const running = await iconStyle(deepest), pending = await iconStyle(leaf(frame,'notes',main));
      const groupIcon = await iconStyle(mainRoot.locator(':scope > .plan-branch-head'));
      for (const icon of [running, pending, groupIcon]) {
        assert.equal(icon.width, icon.height, 'state marker stays circular');
        assert(icon.width>0 && icon.radius!=='0px');
      }
      assert.notDeepEqual([running.color, running.background, running.border], [pending.color, pending.background, pending.border], 'active and pending states have distinct colors');
      assert.equal(m.text,title);assert(m.right<=width+1&&m.x>=0);assert(m.scroll<=m.client+1,'deep title overflows '+width);metrics.push({viewport:width,theme,...m,running,pending,groupIcon});
      if(width===320||width===1100)await capture(page,frame,branch(frame,'layout',main).locator(':scope > .plan-branch-head'),'deep-leaf-'+width+'-'+theme);
    }
    await viewport(page,390,'dark');
    await deepest.locator('.plan-title').click();
    const inspected=frame.locator('#steps .inspected');
    assert.equal(await inspected.getAttribute('data-node-id'),'same');
    assert.equal(await detailNumber(frame),deepPath);
    assert.match(await frame.locator('#detail-name').innerText(),/核对多语言长标题/);
    await capture(page,frame,frame.locator('#step-detail-nav'),'leaf-detail-390-dark');
    await frame.locator('#step-detail-back').click();
    assert.equal(await deepest.locator('.plan-title').evaluate(e=>document.activeElement===e),true);
    const subgroup=branch(frame,'layout',main);
    await subgroup.locator(':scope > .plan-branch-head .branch-title').click();
    assert.equal(await inspected.getAttribute('data-node-id'),'layout');
    assert.equal(await detailNumber(frame),'1-1-1-1-1');
    await capture(page,frame,frame.locator('#step-detail-nav'),'branch-detail-390-dark');
    await frame.locator('#step-detail-back').click();
    await expand(subgroup,false);assert.equal(await deepest.isVisible(),false);
    await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
    assert.equal(await toggle(subgroup).getAttribute('aria-expanded'),'false','manual collapse survives a real refresh');
    const search=frame.locator('#step-search');
    await frame.locator('#step-search-open').click();
    await search.fill('核对注释与术语清单');
    assert.equal(await leaf(frame,'notes',main).locator('.plan-number').innerText(),'1-1-1-1-1-1-2');
    await search.fill('多语言长标题');await deepest.waitFor();
    assert.equal(await leaf(frame,'same',peer).isVisible(),false,'search only reveals matching instance');
    await frame.locator('#search-query-clear').click();
    await frame.locator('#search-dialog-done').click();
    assert.equal(await toggle(subgroup).getAttribute('aria-expanded'),'false','clearing search restores branch state');
    const status = frame.locator('#status-chips');
    await status.locator('[data-status-filter="tone:running"]').click();
    await deepest.waitFor();
    assert.equal(await frame.locator('#step-list .plan-row:visible').count(), 1);
    assert.equal(await deepest.locator('.plan-number').innerText(),deepPath);
    assert.match(await subgroup.locator(':scope > .plan-branch-head .branch-meta').innerText(), /1\s*\/\s*2\s*项/);
    await capture(page,frame,mainRoot.locator(':scope > .plan-branch-head'),'running-path-390-dark');
    await status.locator('[data-status-filter="all"]').click();
    assert.equal(await toggle(subgroup).getAttribute('aria-expanded'), 'false');
    const otherPackage = frame.locator('#package-grid [data-package-filter="other"]');
    await otherPackage.click();
    assert.deepEqual(await frame.locator('#step-list .plan-row:visible').evaluateAll(items=>items.map(e=>e.dataset.stepId)), ['handover']);
    assert.equal(await leaf(frame,'handover',main).locator('.plan-number').innerText(),'2-1');
    assert.equal(await branchNumber(branch(frame,'other',main)),'2');
    await otherPackage.click();
    assert.equal(await toggle(subgroup).getAttribute('aria-expanded'), 'false');
    await expand(subgroup,true);
    await expand(peerRoot,true);
    const peerPackage=branch(frame,'release',peer);
    await expand(peerPackage,true);
    await expand(mainRoot,false);
    const peerLeaf=leaf(frame,'same',peer);
    assert(await peerLeaf.isVisible(),'main branch collapse must leave parallel same IDs visible');
    const peerNumber=await peerLeaf.locator('.plan-number').innerText();
    assert.equal(peerNumber,'3-1-1');
    await expand(peerRoot,false);
    assert.equal(await peerLeaf.isVisible(),false);
    await expand(mainRoot,true);
    assert(await deepest.isVisible());
    assert.equal(await deepest.locator('.plan-number').innerText(),deepPath);
    assert.equal(await peerLeaf.isVisible(),false);
    await expand(mainRoot,false);
    await expand(peerRoot,true);
    assert.equal(await peerLeaf.locator('.plan-number').innerText(),peerNumber);
    await peerLeaf.locator('.plan-title').click();assert.match(await frame.locator('#detail-name').innerText(),/独立复核包的签名来源/);
    assert.equal(await inspected.evaluate(e=>e.closest('[data-task-instance]').dataset.taskInstance),peer);
    assert.equal(await detailNumber(frame),peerNumber);
    await frame.locator('#step-detail-back').click();
    assert.equal(await toggle(mainRoot).getAttribute('aria-expanded'),'false');await capture(page,frame,frame.locator('#step-list'),'parallel-collapse-390-dark');
    cases.push('Six nested groups and the seventh-level leaf, long title, branch and leaf drill-down, persisted collapse across refresh, search, status and package filters, parallel identical group/leaf IDs.');
  } finally {await page.close();}
}
async function pagedPackage(host) {
  const ids=Array.from({length:103},(_,i)=>'item-'+String(i+1).padStart(3,'0'));
  const receipt=await host.rawRpc('init_task_network',{task_title:'核对 103 项交付清单',tasks:[task('bundle','发布清单'),...ids.map((id,i)=>task(id,'核对第 '+(i+1)+' 项交付材料','bundle'))]});
  const {session_id,task_instance_id:instance}=receipt.structuredContent;
  const snapshot=await host.rawRpc('get_progress_view',{session_id,include_history:false});
  const {page,frame}=await mount(host,snapshot);
  try {
    const group=branch(frame,'bundle',instance);
    await expand(group,true);
    const rows=group.locator('.plan-row:visible');
    assert.deepEqual(await rows.evaluateAll(items=>items.map(e=>e.dataset.stepId)),ids);
    assert.deepEqual(await rows.locator('.plan-number').allInnerTexts(),ids.map((_,i)=>'1-'+(i+1)));
    assert(!(await frame.locator('#step-page-index').isVisible()));
    const totals=await frame.locator('#step-page-range').innerText();
    assert.equal(totals,'根任务 1–1 / 1');
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 104');
    assert(!(await frame.locator('#step-page-next').isVisible()));
    assert(!(await frame.locator('#step-page-prev').isVisible()));
    const title=rows.nth(99).locator('.plan-title');
    await title.click();assert.equal(await detailNumber(frame),'1-100');
    await frame.locator('#step-detail-back').click();
    assert(await title.evaluate(e=>document.activeElement===e));
    await capture(page,frame,frame.locator('#step-page'),'one-root-103-leaves');
    await expand(group,false);
    assert.equal(await rows.count(),0);assert.equal(await frame.locator('#step-page-range').innerText(),totals);
    cases.push('One root keeps all 103 descendants on a single page, with 104 tree nodes, hierarchy paths through 1-103 and focus restored to item 1-100.');
  } finally {await page.close();}
}
async function manyRoots(host) {
  const roots=Array.from({length:11},(_,i)=>'pack-'+(i+1));
  const tasks=roots.flatMap((id,i)=>[task(id,'工作包 '+(i+1)),task(id+'-file','明细 '+(i+1),id)]);
  tasks.push(task('proof','最终校样','pack-11-file'));
  const start=await host.rawRpc('init_task_network',{task_title:'十一项交付与并行签收',tasks});
  const {session_id,task_instance_id:instance}=start.structuredContent;
  const route=await host.rawRpc('route_request',{session_id,task_instance_id:instance,request_id:'peer-page',request_text:'验证并行签收',task_title:'并行签收',mode:'parallel',reason:'验收签名',acceptance_criteria:['签名一致']});
  const peer=route.structuredContent.request_task_instance_id;
  await host.rawRpc('decompose_task',{session_id,task_instance_id:peer,task_id:'root',tasks:[task('proof','独立签收核对')]});
  const snapshot=await host.rawRpc('get_progress_view',{session_id,include_history:false});
  const {page,frame}=await mount(host,snapshot);
  try {
    const top=frame.locator('#step-list > [role=listitem]'),index=frame.locator('#step-page-index');
    await top.first().waitFor();
    assert.deepEqual(await top.evaluateAll(rows=>rows.map(r=>r.dataset.stepId)),roots.slice(0,10));
    assert.equal(await index.innerText(),'1 / 2');
    assert.equal(await frame.locator('#step-page-range').innerText(),'根任务 1–10 / 12');
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 25');
    await capture(page,frame,frame.locator('#step-page'),'root-page-one');
    await frame.locator('#step-page-next').click();
    assert.equal(await index.innerText(),'2 / 2');
    assert.deepEqual(await top.evaluateAll(rows=>rows.map(r=>[r.dataset.instance,r.dataset.stepId])),[[instance,'pack-11'],[peer,'root']]);
    const grandchild=leaf(frame,'proof',instance),peerLeaf=leaf(frame,'proof',peer);
    assert.equal(await grandchild.locator('.plan-number').innerText(),'11-1-1');assert.equal(await peerLeaf.locator('.plan-number').innerText(),'12-1');
    for(const row of [grandchild,peerLeaf]){
      const button=row.locator('.plan-title'),number=await row.locator('.plan-number').innerText();
      await button.click();assert.equal(await detailNumber(frame),number);await frame.locator('#step-detail-back').click();
      assert(await button.evaluate(e=>document.activeElement===e));assert.equal(await index.innerText(),'2 / 2');
    }
    await capture(page,frame,frame.locator('#step-page'),'root-page-two');
    await frame.locator('#step-page-prev').click();assert.equal(await index.innerText(),'1 / 2');
    const chosen=frame.locator('#package-grid [data-package-filter="pack-11"]');
    await chosen.click();assert(!(await index.isVisible()));assert.equal(await top.count(),1);
    assert.equal(await frame.locator('#step-page-range').innerText(),'匹配根任务 1–1 / 1');
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 25');
    assert.equal(await grandchild.locator('.plan-number').innerText(),'11-1-1');await chosen.click();
    assert.equal(await index.innerText(),'1 / 2');
    cases.push('Eleven main packages and one parallel root paginate 10 + 2; the grandchild 11-1-1 and duplicate-ID parallel leaf 12-1 stay with their root and restore second-page focus. Package filtering returns the whole selected path.');
  } finally {await page.close();}
}
async function atomicTask(host) {
  const receipt=await host.rawRpc('init_task_network',{task_title:'核对单份交付文件',atomic_reason:'仅有一个交付件需要核对。'});
  const {session_id,task_instance_id:instance}=receipt.structuredContent;
  const snapshot=await host.rawRpc('get_progress_view',{session_id,include_history:false});
  const {frame,page}=await mount(host,snapshot);
  try {
    const item=leaf(frame,'root',instance);
    await item.waitFor();
    assert.equal(await frame.locator('#step-list > .plan-row').count(),1);
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 1');
    assert.equal(await item.locator('.plan-number').innerText(),'1');
    assert(!(await frame.locator('#step-page-index').isVisible()));
    await item.locator('.plan-title').click();
    assert.equal(await detailNumber(frame),'1');
    await frame.locator('#step-detail-back').click();
    assert.equal(await item.locator('.plan-number').innerText(),'1');
    await capture(page,frame,item,'atomic-390-light');
    cases.push('A real atomic task has one visible root leaf numbered 1 in the tree and its detail.');
  } finally {await page.close();}
}
async function main(){
  if(out)fs.mkdirSync(out,{recursive:true});
  const app=path.join(__dirname,'../plugins/task-progress/app.html'),sourceHash=hash(fs.readFileSync(app)),host=await createHost();
  try {
    await deepAndParallel(host);await pagedPackage(host);await manyRoots(host);await atomicTask(host);assert.deepEqual(host.errors,[]);
    const report={sourceHash,servedHash:hash(host.html),host:'Isolated real Python server and production MCP Apps resource inside headless Chrome.',cases,metrics,photos};
    if(out)fs.writeFileSync(path.join(out,'tree-evidence.json'),JSON.stringify(report,null,2)+'\n');
    console.log('Task tree passed: '+cases.join(' ')+' '+sourceHash);
  }finally {await host.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
