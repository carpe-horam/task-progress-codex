'use strict';
const assert = require('node:assert/strict');
const {createHost} = require('./browser_host.cjs');

(async () => {
  const host = await createHost();
  try {
    const assignment={execution_mode:'main_session',self_execution_reason:'Isolated filter fixture'};
    const task=(id,title,parent_id)=>({id,title,assignment,...(parent_id?{parent_id}:{})});
    const result=await host.rpc('init_task_network',{task_title:'Filter test',tasks:[
      task('group','First package'),task('a','Running leaf','group'),task('b','Pending leaf','group'),
      task('other','Other package'),task('c','Another pending leaf','other')
    ]});
    const {session_id,task_instance_id}=result.structuredContent.progress;
    const running=await host.rpc('update_task',{session_id,task_instance_id,task_id:'a',execution_status:'running',assignment});
    const page=await host.page(),frame=await host.mount(page,'main',running,'fullscreen');
    const chip=tone=>frame.locator('[data-status-filter="'+(tone==='all'?tone:'tone:'+tone)+'"]');
    const visible=()=>frame.locator('#step-list .plan-row').evaluateAll(rows=>rows.map(r=>r.dataset.stepId));
    const pressed=tone=>chip(tone).getAttribute('aria-pressed');
    assert.equal(await frame.locator('#status-filter,#package-filter').count(),0);
    await chip('running').focus();await page.keyboard.press('Enter');
    assert.deepEqual(await visible(),['a']);
    await chip('pending').click();
    assert.deepEqual(await visible(),['a','b','c']);assert.equal(await pressed('all'),'false');
    await frame.locator('#package-grid [data-package-filter=group]').click();
    assert.deepEqual(await visible(),['a','b']);
    await frame.locator('#refresh').click();await frame.locator('#refresh:not(:disabled)').waitFor();
    for (const tone of ['running','pending']) assert.equal(await pressed(tone),'true');
    await frame.locator('#step-search-open').click();await frame.locator('#step-search').fill('Running leaf');
    assert.match(await frame.locator('#search-dialog-context').innerText(),/First package/);
    assert.match(await frame.locator('#search-dialog-result').innerText(),/1 项/);
    await frame.locator('#search-dialog-done').click();
    await chip('running').click();assert.deepEqual(await visible(),[]);
    assert(await frame.locator('#clear-plan-filters').isVisible());
    await frame.locator('#clear-plan-filters').click();
    assert.equal(await pressed('all'),'true');assert.deepEqual(await visible(),['a','b','c']);
    assert(await frame.locator('#step-search-open').evaluate(el=>document.activeElement===el));
    await chip('pending').click();await chip('all').click();assert.equal(await pressed('pending'),'false');
    assert.deepEqual(host.errors,[]);console.log('Status union, package and text intersection, reset, focus and refresh passed.');
  } finally {await host.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
