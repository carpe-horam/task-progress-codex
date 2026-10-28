/* Acceptance clarity and independent requests use a private server and browser. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto');
const sourceIdentity=()=>Object.fromEntries(['plugins/task-progress/app.html','plugins/task-progress/server.py','tests/test_request_queue_app.cjs'].map(file=>[file,createHash('sha256').update(fs.readFileSync(path.join(__dirname,'..',file))).digest('hex')]));
const {createHost}=require('./browser_host.cjs');
const clone=value=>JSON.parse(JSON.stringify(value));
const artifacts=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const mainAssignment={execution_mode:'main_session',self_execution_reason:'Isolated browser fixture'};
const subAssignment={execution_mode:'subagent',complexity:'complex',model:'fixture-model',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Produce the isolated fixture output and submit evidence.'};
let host;
async function push(page,snapshot){await page.evaluate(snapshot=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot},'*'),snapshot);}
async function capture(page,name){if(!artifacts)return;await page.emulateMedia({colorScheme:'dark'});fs.mkdirSync(artifacts,{recursive:true});await page.evaluate(()=>{const iframe=document.getElementById('main');iframe.style.height=iframe.contentDocument.documentElement.scrollHeight+'px';});await page.screenshot({path:path.join(artifacts,name+'.png'),fullPage:true});}
async function main(){
 const sources=sourceIdentity();
 host=await createHost();
 let base=await host.rpc('init_task_network',{task_title:'验收进度示例',tasks:[
   {id:'one',title:'计划任务 one',assignment:subAssignment},
   {id:'two',title:'计划任务 two',assignment:subAssignment},
   {id:'three',title:'计划任务 three',assignment:mainAssignment,depends_on:['one','two']},
 ]});
 const sid=base.structuredContent.session_id,iid=base.structuredContent.progress.task_instance_id;
 const binding={session_id:sid,task_instance_id:iid};
 for(const [id,label] of [['one','一'],['two','二']]) {
   base=await host.rpc('update_task',{...binding,task_id:id,execution_status:'running',assignment:{...subAssignment,agent_name:'planned-alias-'+id}});
   if(id==='one') {
     const pendingPage=await host.page(),pendingFrame=await host.mount(pendingPage,'main',base);
     assert.equal(await pendingFrame.locator('#steps .children > .step > details > summary .step-execution-meta').first().textContent(),'子智能体 · fixture-model · 思考 high');
     assert(!(await pendingFrame.locator('#steps .children > .step > details > summary').first().innerText()).includes('planned-alias'));
     assert.equal(await pendingFrame.locator('#steps .children > .step > details').nth(1).locator(':scope > summary .step-execution-meta').count(),0,'unstarted plan does not invent executor metadata');
     await pendingPage.close();
   }
   const attempt_id=base.structuredContent.progress.nodes.find(node=>node.id===id).attempt_id;
   await host.rpc('bind_task_agent',{...binding,task_id:id,attempt_id,agent_name:'fixture-'+id});
   base=await host.rpc('update_task',{...binding,task_id:id,attempt_id,execution_status:'finished',outcome:'resolved',summary:'已完成产出 '+label+'：'+'这是可按需展开查看的完整产出。'.repeat(12),evidence:['fixture://output-'+id]});
 }
 assert.equal(base.structuredContent.progress.pending_review_count,2);assert.equal(base.structuredContent.progress.ready_count,0);
 assert.equal(base.structuredContent.progress.executing_count,0);assert.equal(base.structuredContent.progress.nodes.find(node=>node.id==='three').status,'blocked');
 function snapshot(states){
   const result=clone(base),p=result.structuredContent.progress;
   p.nodes.forEach(node=>{if(node.parent_id)Object.assign(node,states[node.id]||{});});
   const leaves=p.nodes.filter(node=>node.parent_id);
   p.completed=leaves.filter(node=>['finished','failed','cancelled'].includes(node.execution_status)).length;
   p.percent=Math.round(p.completed/p.total*100);p.pending_review_count=leaves.filter(node=>node.review_status==='pending').length;
   p.executing_count=leaves.filter(node=>node.execution_status==='running').length;p.ready_count=0;p.ready_tasks=[];p.blocked_count=0;
   p.root_status='waiting_synthesis';p.status='awaiting_review';p.nodes.find(node=>!node.parent_id).status='waiting_synthesis';
   return result;
 }
 const accepted={execution_status:'finished',outcome:'resolved',status:'resolved',review_status:'accepted',summary:'验收通过的产出'};
 let r=clone(base);
 r.structuredContent.progress.nodes.find(node=>node.id==='one').summary='STALE_NODE_SUMMARY_NOT_PRIMARY';
 const page=await host.page();
 // Polls read the controlled snapshot while all lifecycle/export calls use real isolated handlers.
 await page.evaluate(r=>{const real=window.hostRpc;window.fixture=r;window.hostRpc=(name,args)=>name==='get_progress_view'?Promise.resolve(structuredClone(window.fixture)):real(name,args);},r);
 const frame=await host.mount(page,'main',r);
 assert.equal(await frame.locator('#percent').textContent(),'0');assert.match(await frame.locator('#fraction').textContent(),/0 \/ 3 项已解决 \/ 已验收完成/);
 assert.match(await frame.locator('#progress-context').textContent(),/2 待验收 · 0 执行中 · 1 未开始/);
 assert.equal(await frame.locator('#steps .awaiting_review > details > summary .step-icon').first().textContent(),'◷');
 assert.equal(await frame.locator('#steps .step-main .step-result, #steps .step-main .step-responsibility-meta').count(),0);
 const inline=frame.locator('#steps .children > .step > details > summary .step-execution-meta').first();
 assert.equal(await inline.textContent(),'子智能体 · fixture-one · fixture-model · 思考 high');
 assert(!(await inline.innerText()).includes('planned-alias'));assert(!(await inline.innerText()).includes('未由 Codex 核实'));
 assert(!(await frame.locator('#steps .children > .step > details > summary').first().innerText()).includes('已完成产出'));
 const output=frame.locator('#steps .children > .step > details').first();
 await output.locator(':scope > summary').click();
 const execution=output.locator(':scope > .task-details');
 const primarySummary=output.locator(':scope > .leaf-result-summary');
 assert.match(await primarySummary.innerText(),/已完成产出 一/);
 assert(!(await primarySummary.textContent()).includes('STALE_NODE_SUMMARY_NOT_PRIMARY'),'pending review prioritizes submission summary');
 assert.equal(await primarySummary.getAttribute('open'),null);
 assert.equal(await output.evaluate(element=>element.children[1].classList.contains('leaf-result-summary') && element.children[2].classList.contains('execution-review')),true,'result precedes metadata');
 assert.equal(await output.locator(':scope > .leaf-result-summary').isVisible(),true);
 assert.equal(await execution.getAttribute('open'),null);
 assert.equal(await output.locator(':scope > .task-details > summary').textContent(),'执行与验收详情');
 assert(!(await output.innerText()).includes('计划执行者'));
 assert(!(await execution.innerText()).includes('fixture-one'));
 assert(!(await output.innerText()).includes('未由 Codex 核实'));
 await capture(page,'leaf-open-records-collapsed');
 await primarySummary.locator(':scope > summary').click();
 assert.match(await primarySummary.innerText(),/这是可按需展开查看的完整产出/);
 assert.equal(await execution.getAttribute('open'),null);
 await capture(page,'summary-expanded-fields-collapsed');
 await execution.locator(':scope > summary').click();
 assert.match(await execution.innerText(),/未由 Codex 核实/);
 const responsibility=execution.locator(':scope > .responsibility-record');
 assert.match(await responsibility.innerText(),/计划执行者[\s\S]*子智能体[\s\S]*实际智能体 ID[\s\S]*fixture-one/);
 assert.equal(await responsibility.locator('.detail-label').filter({hasText:'提交摘要'}).count(),0,'same current summary appears only in primary result');
 assert(!(await responsibility.innerText()).includes('这是可按需展开查看的完整产出'));
 await capture(page,'output-and-responsibility-on-demand');
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 assert.equal(await execution.getAttribute('open'),'');
 assert.equal(await primarySummary.getAttribute('open'),'');
 await execution.locator(':scope > summary').click();
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 assert.equal(await execution.getAttribute('open'),null);
 assert.match(await output.locator(':scope > summary').innerText(),/待主会话验收/);
 await output.locator(':scope > summary').click();
 await capture(page,'default-compact-task-tree');
 assert.equal(await frame.locator('.root-metadata').getAttribute('open'),null);
 assert.equal(await frame.locator('#steps .children > .step > details > summary .step-id').allTextContents().then(x=>x.join(',')),'one,two,three');
 assert.match(await frame.locator('#network-meta').textContent(),/可执行 0 · 进行中 0 · 待主会话验收 2 · 阻塞 1/);
 assert.match(await frame.locator('#steps .children > .step').last().textContent(),/依赖阻塞/);
 if(artifacts){fs.mkdirSync(artifacts,{recursive:true});fs.writeFileSync(path.join(artifacts,'zero-accepted-two-pending-snapshot.json'),JSON.stringify(r,null,2));}
 await capture(page,'zero-accepted-two-pending');
 r=snapshot({one:accepted,two:{execution_status:'failed',status:'failed',outcome:null,review_status:null,submission:null,summary:'验证失败'},three:{execution_status:'cancelled',status:'cancelled',outcome:null}});r.structuredContent.state_revision+=1;r.structuredContent.progress.state_revision+=1;
 await push(page,r);await frame.locator('#percent').filter({hasText:'33'}).waitFor();assert.match(await frame.locator('#steps .children > .step > details > summary').first().innerText(),/已解决 \/ 已验收完成/);assert.match(await frame.locator('#progress-context').textContent(),/2 未解决（含失败 \/ 取消）/);await capture(page,'mixed-status');
 assert.match(await output.locator(':scope > .leaf-result-summary').textContent(),/验收通过的产出/);
 assert(!(await output.locator(':scope > .leaf-result-summary').textContent()).includes('已完成产出 一'),'accepted task prioritizes node summary');
 assert.match(await frame.locator('#steps .children > .step > details').nth(1).locator(':scope > .leaf-result-summary').textContent(),/验证失败/);
 assert.equal(await frame.locator('#steps .children > .step > details').nth(2).locator(':scope > .leaf-result-summary').count(),0,'missing result does not invent completion');
 r=snapshot({one:accepted,two:accepted,three:accepted});r.structuredContent.state_revision+=2;r.structuredContent.progress.state_revision+=2;
 await page.evaluate(r=>window.fixture=r,r);await push(page,r);await frame.locator('#percent').filter({hasText:'100'}).waitFor();assert.match(await frame.locator('#acceptance-status').textContent(),/叶任务 100% 仍需整体验收/);await capture(page,'leaves-complete-root-pending');
 // A legacy resolved node cannot manufacture acceptance, even with a recorded executor.
 r=snapshot({one:{...accepted,review_status:'legacy_unrecorded',responsibility_legacy:true},two:accepted,three:accepted});r.structuredContent.state_revision+=3;r.structuredContent.progress.state_revision+=3;
 await push(page,r);await frame.locator('#percent').filter({hasText:'67'}).waitFor();assert.match(await frame.locator('#progress-context').textContent(),/1 验收未记录/);
 const legacy=clone(r);const legacyNode=legacy.structuredContent.progress.nodes.find(node=>node.id==='one');
 delete legacyNode.dispatch;legacyNode.assignment={execution_mode:'subagent',agent_name:'legacy-recorded-agent'};
 legacy.structuredContent.state_revision+=1;legacy.structuredContent.progress.state_revision+=1;
 await page.evaluate(r=>window.fixture=r,legacy);await push(page,legacy);
 await frame.locator('#steps .children > .step > details > summary .step-execution-meta').first().filter({hasText:'legacy-recorded-agent'}).waitFor();
 assert.equal(await frame.locator('#steps .children > .step > details > summary .step-execution-meta').first().textContent(),'子智能体 · legacy-recorded-agent');
 await page.close();
 // Real request routing keeps main progress fixed and reads a parallel instance explicitly.
 const route=(request_id,mode)=>host.rpc('route_request',{session_id:sid,task_instance_id:iid,request_id,request_text:'保存 '+request_id,task_title:request_id,mode,reason:'独立范围',acceptance_criteria:['结果可复核']});
 await route('稍后处理','queue');r=await route('并行调查','parallel');
 const parallel=r.structuredContent.session_work.requests.find(item=>item.mode==='parallel');
 const requestPage=await host.page(),requestFrame=await host.mount(requestPage,'main',r);
 const context=requestFrame.locator('#session-work > details');
 assert.equal(await context.getAttribute('open'),null);
 assert.equal(await context.locator(':scope > summary').textContent(),'任务上下文 · 1 个排队请求');
 assert.equal(await requestFrame.locator('#session-work').innerText(),'任务上下文 · 1 个排队请求');
 assert.equal(await requestFrame.locator('#steps > .step[data-root-role="main"] > details').getAttribute('open'),'');
 if(artifacts)fs.writeFileSync(path.join(artifacts,'focus-ui-fixture.json'),JSON.stringify(r,null,2));
 await capture(requestPage,'context-collapsed-queue-visible');
 const queuedLeaf=requestFrame.locator('#steps > .step[data-root-role="main"] > details > .children > .step > details').first();
 await queuedLeaf.locator(':scope > summary').click();
 assert.equal(await queuedLeaf.locator(':scope > .task-details').getAttribute('open'),null);
 await capture(requestPage,'focus-after-leaf-open');
 await queuedLeaf.locator(':scope > .task-details > summary').click();
 await queuedLeaf.locator('.responsibility-record details').evaluateAll(elements=>elements.forEach(element=>element.open=true));
 assert.match(await queuedLeaf.innerText(),/这是可按需展开查看的完整产出[\s\S]*计划执行者[\s\S]*fixture-one/);
 await capture(requestPage,'focus-after-execution-review-open');
 await queuedLeaf.locator(':scope > .task-details > summary').click();
 await queuedLeaf.locator(':scope > summary').click();
 await context.locator(':scope > summary').click();
 assert.match(await requestFrame.locator('#session-work').textContent(),/排队 · 未启动/);assert.match(await requestFrame.locator('#session-work').textContent(),/请求已保存，尚未启动或执行/);
 assert.equal(await requestFrame.locator('#task-title').textContent(),'验收进度示例');assert.equal(await requestFrame.locator('#percent').textContent(),'0');
 const visibleRequestText=await requestFrame.locator('#session-work').innerText();assert(!visibleRequestText.includes(iid));assert(!visibleRequestText.includes('节点 root'));assert.match(visibleRequestText,/完成后返回「验收进度示例」/);
 assert.equal(await requestFrame.locator('#steps > .step[data-root-role="parallel"]').count(),1);
 assert.equal(await requestFrame.getByRole('button',{name:'只读查看',exact:true}).count(),0);
 assert.equal(await requestFrame.locator('#request-preview').count(),0);
 // All context disclosures are scoped to the foreground instance and survive
 // actual five-second polling as well as a changed real-server notification.
 const initialContextKeys=await context.locator('details').evaluateAll(elements=>elements.map(element=>element.dataset.key));
 assert(initialContextKeys.length>=3);assert(initialContextKeys.every(key=>key.startsWith('session-work:'+iid+':')));
 await context.locator('details > summary').evaluateAll(elements=>elements.forEach(element=>element.click()));
 const openedKeys=await context.locator('details[open]').evaluateAll(elements=>elements.map(element=>element.dataset.key));
 const reads=await requestPage.evaluate(()=>window.host.calls.filter(call=>call.name==='get_progress_view'&&call.completedAt).length);
 await requestPage.waitForFunction(reads=>window.host.calls.filter(call=>call.name==='get_progress_view'&&call.completedAt).length>reads,reads,{timeout:8500});
 assert.equal(await context.getAttribute('open'),'');
 assert.deepEqual(await context.locator('details[open]').evaluateAll(elements=>elements.map(element=>element.dataset.key)),openedKeys);
 r=await route('稍后处理二','queue');await push(requestPage,r);
 await context.locator(':scope > summary').filter({hasText:'2 个排队请求'}).waitFor();
 assert.equal(await context.getAttribute('open'),'');
 assert.deepEqual(await context.locator('details[open]').evaluateAll(elements=>elements.map(element=>element.dataset.key)),openedKeys);
 r=await host.rpc('cancel_request',{...binding,request_id:'稍后处理二',reason:'Isolated changed-refresh fixture complete'});await push(requestPage,r);
 await context.locator(':scope > summary').filter({hasText:'1 个排队请求'}).waitFor();
 await context.locator(':scope > summary').click();
 await requestFrame.locator('#refresh').click();await requestFrame.locator('#refresh:not([disabled])').waitFor();
 assert.equal(await context.getAttribute('open'),null);
 assert.deepEqual(await context.locator('details[open]').evaluateAll(elements=>elements.map(element=>element.dataset.key)),openedKeys);
 await context.locator(':scope > summary').click();
 assert(!(await requestPage.evaluate(()=>window.host.calls.map(call=>call.name))).some(name=>['activate_request','update_task','decompose_task'].includes(name)),'disclosure and refresh never schedule work');
 await capture(requestPage,'context-open-refresh-preserved');
 // Restore only request metadata to the compact preview for the source anchor check.
 await context.locator('details > summary').evaluateAll(elements=>elements.forEach(element=>{element.parentElement.open=false;}));
 await capture(requestPage,'queue-parallel-peer-root');
 // Forwarding the independent result must not replace the foreground card.
 const independent=await host.rpc('get_progress_view',{session_id:sid,task_instance_id:parallel.task_instance_id});await push(requestPage,independent);
 assert.equal(await requestFrame.locator('#task-title').textContent(),'验收进度示例');await capture(requestPage,'queue-parallel-foreground');
 // Explicit activation archives A. The old foreground is history, while the
 // parallel request keeps its source reference without promising a return to A.
 for(const id of ['one','two']) {
   const progress=(await host.rpc('get_progress_view',binding)).structuredContent.progress;
   const attempt_id=progress.nodes.find(node=>node.id===id).attempt_id;
   await host.rpc('review_task',{...binding,task_id:id,attempt_id,decision:'accept',summary:'已核验产出',evidence:['fixture://review-'+id],reviewer:mainAssignment});
 }
 let started=await host.rpc('update_task',{...binding,task_id:'three',execution_status:'running',assignment:mainAssignment});
 await host.rpc('update_task',{...binding,task_id:'three',attempt_id:started.structuredContent.progress.nodes.find(node=>node.id==='three').attempt_id,execution_status:'finished',outcome:'resolved',summary:'全部依赖已验收'});
 await host.rpc('update_task',{...binding,task_id:'root',execution_status:'finished',outcome:'resolved',assignment:mainAssignment,summary:'整体已核验',evidence:['fixture://root-review']});
 r=await host.rpc('activate_request',{...binding,request_id:'稍后处理'});await push(requestPage,r);
 await requestFrame.locator('#task-title').filter({hasText:'稍后处理'}).waitFor();
 assert.equal(await requestFrame.locator('.request-item').count(),0);
 assert.equal(await context.getAttribute('open'),null,'new foreground resets context disclosure');
 assert.equal(await context.locator(':scope > summary').textContent(),'任务上下文');
 assert.equal(await requestFrame.locator('#session-work').innerText(),'任务上下文');
 assert((await context.getAttribute('data-key')).includes(r.structuredContent.progress.task_instance_id));
 assert(!initialContextKeys.includes(await context.locator('details').first().getAttribute('data-key')));
 assert.equal(await context.locator('details[open]').count(),0);
 assert.equal(await requestFrame.locator('#steps > .step[data-root-role="parallel"]').count(),1);
 const afterActivation=await requestFrame.locator('#session-work').innerText();assert(!afterActivation.includes('完成后返回「验收进度示例」'));
 // The retained parallel request is explicitly settled open; it is unresolved,
 // but no longer counted as active work by either the server or card.
 const parallelBinding={session_id:sid,task_instance_id:parallel.task_instance_id};
 await host.rpc('decompose_task',{...parallelBinding,task_id:'root',tasks:[{id:'work',title:'并行产出',assignment:subAssignment}]});
 started=await host.rpc('update_task',{...parallelBinding,task_id:'work',execution_status:'running',assignment:subAssignment});
 const attempt_id=started.structuredContent.progress.nodes.find(node=>node.id==='work').attempt_id;
 await host.rpc('bind_task_agent',{...parallelBinding,task_id:'work',attempt_id,agent_name:'fixture-parallel'});
 await host.rpc('update_task',{...parallelBinding,task_id:'work',attempt_id,execution_status:'finished',outcome:'open',summary:'调查后仍有未解决问题',evidence:['fixture://open']});
 await host.rpc('review_task',{...parallelBinding,task_id:'work',attempt_id,decision:'accept',summary:'接受未解决报告',evidence:['fixture://open-review'],reviewer:mainAssignment});
 await host.rpc('update_task',{...parallelBinding,task_id:'root',execution_status:'finished',outcome:'open',assignment:mainAssignment,summary:'已如实收尾，问题未解决',evidence:['fixture://open-root']});
 r=await host.rpc('get_progress_view',{session_id:sid});await push(requestPage,r);
 await requestFrame.locator('#steps > .step[data-root-role="parallel"]').filter({hasText:'已如实收尾，问题未解决'}).waitFor();
 assert.equal(await requestFrame.locator('#steps > .step').count(),2);
 await capture(requestPage,'activated-queue-parallel-open');
 assert.equal(await requestFrame.getByRole('button',{name:'只读查看',exact:true}).count(),0);
 await requestPage.close();assert.deepEqual(host.errors,[]);assert.deepEqual(sourceIdentity(),sources,'App/server/test source changed during browser verification');
 if(artifacts)fs.writeFileSync(path.join(artifacts,'source-identities.json'),JSON.stringify({capturedAt:new Date().toISOString(),renderer:'isolated headless Chrome via Playwright',sources},null,2));
 console.log('Acceptance clarity, on-demand output/responsibility, collapsed context, polling/changed-refresh state, instance isolation, plan order, legacy unknown acceptance, root synthesis, request queue, parallel peer roots and foreground protection passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>host?.close());
