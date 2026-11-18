'use strict';
const assert = require('node:assert/strict');
const {createHost} = require('./browser_host.cjs');
(async () => {
  const host = await createHost();
  try {
    const assignment = {execution_mode:'main_session', self_execution_reason:'Isolated filter fixture'};
    const result = await host.rpc('init_task_network', {task_title:'Filter test', tasks:[{id:'a',title:'Running leaf',assignment},{id:'b',title:'Pending leaf',assignment}]});
    const binding = {session_id:result.structuredContent.session_id, task_instance_id:result.structuredContent.progress.task_instance_id};
    const running = await host.rpc('update_task', {...binding,task_id:'a',execution_status:'running',assignment});
    const page = await host.page(), frame = await host.mount(page,'main',running,'fullscreen');
    await frame.locator('#status-filter').selectOption('running');
    assert(await frame.locator('li.step.running').first().isVisible());
    assert.equal(await frame.locator('li.step').filter({has:frame.locator('.task-title-text',{hasText:'Pending leaf'})}).last().isVisible(),false);
    await frame.locator('#refresh').click();
    await frame.locator('#refresh:not([disabled])').waitFor();
    assert.equal(await frame.locator('#status-filter').inputValue(),'running');
    await frame.locator('#status-filter').selectOption('review');
    assert(await frame.locator('#filter-empty').isVisible());
    await frame.locator('#status-filter').selectOption('all');
    assert(await frame.locator('li.step').filter({has:frame.locator('.task-title-text',{hasText:'Pending leaf'})}).last().isVisible());
    assert.deepEqual(host.errors,[]);
    console.log('Shared app status filtering passed');
  } finally { await host.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
