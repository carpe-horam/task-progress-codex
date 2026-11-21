/* Explicit, inherited and unrecorded agent settings stay distinguishable in the live task tree. */
'use strict';
const assert=require('node:assert/strict'),{createHost}=require('./browser_host.cjs');
(async()=>{const h=await createHost();try{
 const base={execution_mode:'subagent',tool_identifier:'collaboration.spawn_agent',dispatch_text:'核对交付记录。'};
 const assignments={known:{...base,model:'gpt-6.2-astra',model_source:'explicit',reasoning_effort:'ultra',reasoning_effort_source:'explicit'},inherited:{...base,model_source:'inherited',reasoning_effort_source:'inherited'},unknown:{...base,model_source:'unknown',reasoning_effort_source:'unknown'},partial:{...base,model:'gpt-6.2-astra',model_source:'explicit',reasoning_effort_source:'unknown'},mixed:{...base,model_source:'inherited',reasoning_effort_source:'unknown'}};
 const init=await h.rawRpc('init_task_network',{task_title:'核对模型与强度',tasks:Object.entries(assignments).map(([id,assignment])=>({id,title:'核对 '+id,assignment}))});
 const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id};
 for(const [id,assignment] of Object.entries(assignments)) await h.rawRpc('update_task',{...binding,task_id:id,execution_status:'running',assignment});
 const page=await h.page();await page.setViewportSize({width:390,height:900});await page.addStyleTag({content:'html,body{margin:0}iframe{width:100%!important;height:100vh!important;border:0}'});
 const f=await h.mount(page,'main',await h.rawRpc('get_progress_view',binding),'fullscreen');
 const owner=id=>f.locator('#step-list .plan-row[data-step-id="'+id+'"] .plan-owner');
 assert.match(await owner('known').innerText(),/gpt-6\.2-astra · 思考 ultra/);
 for(const id of Object.keys(assignments)) {
  const icon=owner(id).locator('.execution-role');
  assert.equal(await icon.getAttribute('aria-label'),'子智能体');
  assert.equal(await icon.locator('svg').count(),1);
 }
 await owner('known').locator('.execution-role').hover();
 assert.equal(await owner('known').locator('.execution-role').evaluate(e=>getComputedStyle(e,'::after').display),'block');
 for(const id of ['inherited','unknown','partial','mixed']) assert.match(await owner(id).innerText(),/配置未记录/);
 assert.match(await owner('partial').innerText(),/gpt-6\.2-astra/);
 const hint=id=>owner(id).locator('.config-missing');
 assert.match(await hint('unknown').getAttribute('aria-label'),/模型未记录；思维强度未记录/);
 assert.match(await hint('inherited').getAttribute('aria-label'),/模型继承配置，实际值未记录；思维强度继承配置，实际值未记录/);
 assert.match(await hint('partial').getAttribute('aria-label'),/思维强度未记录/);
 assert.doesNotMatch(await hint('partial').getAttribute('aria-label'),/模型未记录/);
 assert.match(await hint('mixed').getAttribute('aria-label'),/模型继承配置，实际值未记录；思维强度未记录/);
 await hint('unknown').hover();assert(await hint('unknown').locator('.config-missing-detail').isVisible());
 await hint('unknown').focus();assert(await hint('unknown').locator('.config-missing-detail').isVisible());
 await hint('unknown').click();assert.equal(await f.locator('#step-dialog').isVisible(),false,'inspecting the hint does not open task details');
 assert.equal(await f.locator('html').evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await page.screenshot({path:'/tmp/task-agent-model-display.png'});assert.deepEqual(h.errors,[]);console.log('Agent settings: explicit, inherited and unknown displayed without inventing values; narrow viewport fits.');await page.close();
 }finally{await h.close()}})().catch(e=>{console.error(e);process.exitCode=1});
