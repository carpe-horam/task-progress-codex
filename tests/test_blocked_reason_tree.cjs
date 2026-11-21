/* User-visible waiting reasons must reflect actual dependency and main-session contention. */
'use strict';
const assert=require('node:assert/strict'),{createHost}=require('./browser_host.cjs');
(async()=>{const h=await createHost();try{
 const assignment={execution_mode:'main_session',self_execution_reason:'隔离验证等待原因'};
 const init=await h.rawRpc('init_task_network',{task_title:'等待原因测试',tasks:[{id:'serial',title:'准备展示',assignment},{id:'active',title:'核对来源数据',assignment},{id:'dependent',title:'验证结果',depends_on:['active'],assignment}]});
 const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
 await h.rawRpc('update_task',{...binding,task_id:'active',execution_status:'running',assignment});
 const page=await h.page();await page.setViewportSize({width:390,height:900});await page.addStyleTag({content:'html,body{margin:0}iframe{width:100%!important;height:100vh!important;border:0}'});
 const f=await h.mount(page,'main',await h.rawRpc('get_progress_view',binding),'fullscreen');
 const row=id=>f.locator('#step-list .plan-row[data-step-id="'+id+'"]');
 assert.equal(await row('active').locator('.execution-role').count(),0,'main session needs no role icon');
 assert.equal(await row('active').locator('.plan-context').count(),0,'main-only row has no empty role line');
 for(const [id,caption,kind] of [['dependent','等待前置任务：','dependency'],['serial','等待主会话先完成：','main_session']]) {
  const note=row(id).locator('.dependency-wait'),link=note.locator('.dependency-link');
  assert.equal(await note.locator(':scope > span').textContent(),caption);
  assert.equal(await link.textContent(),'2');
  assert.equal(await link.getAttribute('data-wait-kind'),kind);
  assert.match(await link.getAttribute('aria-label'),/核对来源数据/);
  assert.doesNotMatch(await note.innerText(),/核对来源数据/,'only the number appears in the waiting line');
 }
 assert.match(await row('serial').locator('.plan-status').innerText(),/等待执行/);
 assert.match(await row('dependent').locator('.plan-status').innerText(),/受阻/);
 assert.equal(await row('active').locator('.plan-wait-reason').count(),0);
 assert.equal(await f.locator('html').evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 assert.deepEqual(h.errors,[]);await page.screenshot({path:'/tmp/task-blocked-reasons.png'});console.log('Dependency and main-session waiting use correctly typed clickable numbers; running task has no warning.');await page.close();
 }finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
