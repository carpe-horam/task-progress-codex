'use strict';
const assert=require('node:assert/strict');
const {createHost}=require('./browser_host.cjs');
(async()=>{const h=await createHost();try{
 let sid;
 for(let i=0;i<14;i++){
   const args={task_title:`历史任务 ${i+1}`,steps:[`历史步骤 ${i+1}`],...(sid?{session_id:sid,task_relationship:'independent',new_task_reason:'隔离测试'}:{})};
   const result=await h.rawRpc('init_progress',args);sid=result.structuredContent.session_id;
 }
 await h.rawRpc('init_task_network',{session_id:sid,task_title:'网络历史任务',atomic_reason:'单步验证',task_relationship:'independent',new_task_reason:'隔离测试'});
 await h.rawRpc('init_task_network',{session_id:sid,task_title:'当前任务',atomic_reason:'单步验证',task_relationship:'independent',new_task_reason:'隔离测试'});
 const page=await h.page();await page.setViewportSize({width:390,height:900});
 const frame=await h.mount(page,'main',await h.rawRpc('get_progress_view',{session_id:sid}),'fullscreen');
 await page.locator('#main').evaluate(el=>{el.style.width='370px';el.style.height='850px'});
 await frame.locator('#workspace-tab-archives').click();
 const list=frame.locator('#task-history-list');await list.locator('li.history-item').first().waitFor();
 if(process.env.HISTORY_UX_SCREENSHOT)await page.screenshot({path:process.env.HISTORY_UX_SCREENSHOT});
 assert.equal(await list.locator(':scope > li.history-item').count(),15);
 assert.equal(await list.locator(':scope > li.history-item > details[open]').count(),0);
 const first=list.locator(':scope > li.history-item').filter({hasText:'历史任务 14'});
 assert(!(await first.locator('summary.task-history-summary').innerText()).includes('已验收 0/1'));
 await first.locator('summary.task-history-summary').click();
 const step=first.locator('.archive-task-content .step > details > summary').first();await step.click();
 await frame.locator('#step-dialog[open]').waitFor();
 assert((await frame.locator('#detail-name').innerText()).includes('历史步骤'));
 assert.equal(await first.locator('.step > details').first().getAttribute('open'),null,'history step opens dialog, not inline details');
 await frame.locator('#step-detail-back').click();assert.equal(await frame.locator('#step-dialog').getAttribute('open'),null);
 const network=list.locator(':scope > li.history-item').filter({hasText:'网络历史任务'});
 await network.locator('summary.task-history-summary').click();await network.locator('.step > details > summary').first().click();
 await frame.locator('#step-dialog[open]').waitFor();assert((await frame.locator('#detail-name').innerText()).includes('网络历史任务'));await frame.locator('#step-detail-back').click();
 assert.deepEqual(h.errors,[]);await page.close();console.log('History UX: 14 compact archives, narrow viewport, preserved status, and step detail dialog passed.');
}finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
