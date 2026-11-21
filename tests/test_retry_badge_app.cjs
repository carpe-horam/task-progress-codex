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
  const work=frame.locator('#step-list [data-instance="'+binding.task_instance_id+'"][data-step-id="work"]');
  const first=frame.locator('#step-list [data-instance="'+binding.task_instance_id+'"][data-step-id="first"]');
  await work.waitFor();
  assert.equal(await work.locator('.retry-badge').count(),0);
  assert.equal(await first.locator('.retry-badge').count(),0);
  started=await host.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',retry:true,assignment});
  attempt=started.structuredContent.attempt_id;
  const refresh=async()=>{await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();};
  await refresh();
  const badge=work.locator('.retry-badge');
  assert.equal(await badge.textContent(),'第 2 次执行');
  assert.equal(await badge.locator('svg').count(),0,'retry uses text');
  await badge.hover();
  assert.equal(await badge.evaluate(e=>getComputedStyle(e,'::after').display),'block','hover reveals the execution count');
  assert.match(await badge.getAttribute('data-tip'),/第 2 次执行/);
  assert.equal(await frame.locator('#step-detail-back').isVisible(),false);
  assert.match(await work.locator('.plan-status').textContent(),/进行中|执行中/);
  await host.rawRpc('update_task',{...binding,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'Resolved'});
  await refresh();
  assert.equal(await badge.textContent(),'第 2 次执行');
  assert.match(await work.locator('.plan-status').textContent(),/已验收/);
  started=await host.rawRpc('update_task',{...binding,task_id:'work',execution_status:'running',retry:true,assignment});
  await refresh();
  assert.equal(await badge.textContent(),'第 3 次执行');
  await page.setViewportSize({width:320,height:800});
  await page.addStyleTag({content:'html,body{margin:0}iframe{width:100%!important;border:0!important}'});
  await frame.locator('html').evaluate(()=>new Promise(requestAnimationFrame));
  assert.equal(await frame.locator('html').evaluate(()=>innerWidth),320);
  assert.equal(await badge.isVisible(),true);
  assert.equal(await frame.locator('body').evaluate(e=>e.scrollWidth <= e.clientWidth),true);
  if(process.env.TASK_PROGRESS_MARKER_SCREENSHOT) { await badge.hover();await page.waitForTimeout(150);await page.screenshot({path:process.env.TASK_PROGRESS_MARKER_SCREENSHOT}); }
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
  assert.equal(await frame2.locator('#steps .retry-badge').textContent(),'第 2 次执行');
  assert.deepEqual(host.errors,[]);
  await page2.close();
  console.log('Retry badge: first attempt, repeated retries, resolved retry, linear step and compact viewport passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
