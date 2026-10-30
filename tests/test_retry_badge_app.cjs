/* Retry visibility in compact task rows, using the real isolated backend and app. */
const assert = require('node:assert/strict');
const {createHost} = require('./browser_host.cjs');
const assignment = {execution_mode:'main_session', self_execution_reason:'Retry badge fixture'};
let host;
async function main() {
  host = await createHost();
  const init = await host.rawRpc('init_task_network', {task_title:'Retry fixture', tasks:[
    {id:'work',title:'需要重试的任务',assignment}, {id:'first',title:'首次执行',assignment}
  ]});
  const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
  let started=await host.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',assignment});
  let attempt=started.structuredContent.attempt_id;
  await host.rawRpc('update_task',{...binding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'open',summary:'First attempt'});
  const page=await host.page();
  const frame=await host.mount(page,'main',await host.rawRpc('render_progress',{session_id:binding.session_id}),'fullscreen');
  const work=frame.locator('details[data-key="task:'+binding.task_instance_id+':epoch:0:work"]');
  const first=frame.locator('details[data-key="task:'+binding.task_instance_id+':epoch:0:first"]');
  await work.waitFor();
  assert.equal(await work.locator(':scope > summary .retry-badge').count(),0);
  assert.equal(await first.locator(':scope > summary .retry-badge').count(),0);
  started=await host.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',retry:true,assignment});
  attempt=started.structuredContent.attempt_id;
  const refresh=async()=>{await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();};
  await refresh();
  const badge=work.locator(':scope > summary .retry-badge');
  assert.equal(await badge.textContent(),'↻ 已重试 1 次');
  assert.equal(await work.getAttribute('open'),null);
  assert.match(await work.locator(':scope > summary .step-right').textContent(),/进行中|执行中/);
  await host.rawRpc('update_task',{...binding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'Resolved'});
  await refresh();
  assert.equal(await badge.textContent(),'↻ 已重试 1 次');
  assert.match(await work.locator(':scope > summary .step-right').textContent(),/已解决/);
  started=await host.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',retry:true,assignment});
  await refresh();
  assert.equal(await badge.textContent(),'↻ 已重试 2 次');
  await page.setViewportSize({width:320,height:800});
  assert.equal(await badge.isVisible(),true);
  assert.equal(await frame.locator('body').evaluate(e=>e.scrollWidth <= e.clientWidth),true);
  assert.deepEqual(host.errors,[]);
  await page.close();

  const linear=await host.rawRpc('init_progress',{task_title:'Linear fixture',steps:['线性步骤']});
  const lb={session_id:linear.structuredContent.session_id,task_instance_id:linear.structuredContent.task_instance_id,step_index:0};
  const firstRun=await host.rawRpc('update_step',{...lb,status:'in_progress',assignment});
  await host.rawRpc('update_step',{...lb,status:'completed',attempt_id:firstRun.structuredContent.attempt_id});
  await host.rawRpc('update_step',{...lb,status:'in_progress',attempt_id:firstRun.structuredContent.attempt_id,assignment});
  const page2=await host.page();
  const frame2=await host.mount(page2,'linear',await host.rawRpc('render_progress',{session_id:lb.session_id}),'fullscreen');
  await frame2.locator('#steps .retry-badge').waitFor();
  assert.equal(await frame2.locator('#steps .retry-badge').textContent(),'↻ 已重试 1 次');
  assert.deepEqual(host.errors,[]);
  await page2.close();
  console.log('Retry badge: first attempt, repeated retries, resolved retry, linear step and compact viewport passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
