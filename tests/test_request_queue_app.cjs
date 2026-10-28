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
async function capture(page,name){if(!artifacts)return;fs.mkdirSync(artifacts,{recursive:true});await page.evaluate(()=>{const iframe=document.getElementById('main');iframe.style.height=iframe.contentDocument.documentElement.scrollHeight+'px';});await page.screenshot({path:path.join(artifacts,name+'.png'),fullPage:true});}
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
   base=await host.rpc('update_task',{...binding,task_id:id,execution_status:'running',assignment:subAssignment});
   const attempt_id=base.structuredContent.progress.nodes.find(node=>node.id===id).attempt_id;
   await host.rpc('bind_task_agent',{...binding,task_id:id,attempt_id,agent_name:'fixture-'+id});
   base=await host.rpc('update_task',{...binding,task_id:id,attempt_id,execution_status:'finished',outcome:'resolved',summary:'已完成产出 '+label,evidence:['fixture://output-'+id]});
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
 const page=await host.page();
 // Polls read the controlled snapshot while all lifecycle/export calls use real isolated handlers.
 await page.evaluate(r=>{const real=window.hostRpc;window.fixture=r;window.hostRpc=(name,args)=>name==='get_progress'?Promise.resolve(structuredClone(window.fixture)):real(name,args);},r);
 const frame=await host.mount(page,'main',r);
 assert.equal(await frame.locator('#percent').textContent(),'0');assert.match(await frame.locator('#fraction').textContent(),/0 \/ 3 项已解决 \/ 已验收完成/);
 assert.match(await frame.locator('#progress-context').textContent(),/2 待验收 · 0 执行中 · 1 未开始/);
 assert.equal(await frame.locator('#steps .awaiting_review > details > summary .step-icon').first().textContent(),'◷');
 assert(await frame.locator('#steps .step-result').filter({hasText:'已完成产出 一'}).isVisible());
 assert.equal(await frame.locator('.root-metadata').getAttribute('open'),null);
 assert.equal(await frame.locator('#steps .children > .step > details > summary .step-id').allTextContents().then(x=>x.join(',')),'one,two,three');
 assert.match(await frame.locator('#network-meta').textContent(),/可执行 0 · 进行中 0 · 待主会话验收 2 · 阻塞 1/);
 assert.match(await frame.locator('#steps .children > .step').last().textContent(),/依赖阻塞/);
 if(artifacts){fs.mkdirSync(artifacts,{recursive:true});fs.writeFileSync(path.join(artifacts,'zero-accepted-two-pending-snapshot.json'),JSON.stringify(r,null,2));}
 await capture(page,'zero-accepted-two-pending');
 r=snapshot({one:accepted,two:{execution_status:'failed',status:'failed',outcome:null,review_status:null,submission:null,summary:'验证失败'},three:{execution_status:'cancelled',status:'cancelled',outcome:null}});r.structuredContent.state_revision+=1;r.structuredContent.progress.state_revision+=1;
 await push(page,r);await frame.locator('#percent').filter({hasText:'33'}).waitFor();assert.match(await frame.locator('#progress-context').textContent(),/2 未解决（含失败 \/ 取消）/);await capture(page,'mixed-status');
 r=snapshot({one:accepted,two:accepted,three:accepted});r.structuredContent.state_revision+=2;r.structuredContent.progress.state_revision+=2;
 await page.evaluate(r=>window.fixture=r,r);await push(page,r);await frame.locator('#percent').filter({hasText:'100'}).waitFor();assert.match(await frame.locator('#acceptance-status').textContent(),/叶任务 100% 仍需整体验收/);await capture(page,'leaves-complete-root-pending');
 // A legacy resolved node cannot manufacture acceptance, even with a recorded executor.
 r=snapshot({one:{...accepted,review_status:'legacy_unrecorded',responsibility_legacy:true},two:accepted,three:accepted});r.structuredContent.state_revision+=3;r.structuredContent.progress.state_revision+=3;
 await push(page,r);await frame.locator('#percent').filter({hasText:'67'}).waitFor();assert.match(await frame.locator('#progress-context').textContent(),/1 验收未记录/);
 await page.close();
 // Real request routing keeps main progress fixed and reads a parallel instance explicitly.
 const route=(request_id,mode)=>host.rpc('route_request',{session_id:sid,task_instance_id:iid,request_id,request_text:'保存 '+request_id,task_title:request_id,mode,reason:'独立范围',acceptance_criteria:['结果可复核']});
 await route('稍后处理','queue');r=await route('并行调查','parallel');
 const parallel=r.structuredContent.session_work.requests.find(item=>item.mode==='parallel');
 const requestPage=await host.page(),requestFrame=await host.mount(requestPage,'main',r);
 assert.match(await requestFrame.locator('#session-work').textContent(),/排队 · 未启动/);assert.match(await requestFrame.locator('#session-work').textContent(),/请求已保存，尚未启动或执行/);
 assert.equal(await requestFrame.locator('#task-title').textContent(),'验收进度示例');assert.equal(await requestFrame.locator('#percent').textContent(),'0');
 const visibleRequestText=await requestFrame.locator('#session-work').innerText();assert(!visibleRequestText.includes(iid));assert(!visibleRequestText.includes('节点 root'));assert.match(visibleRequestText,/完成后返回「验收进度示例」/);
 assert.equal(await requestFrame.locator('#steps > .step[data-root-role="parallel"]').count(),1);
 assert.equal(await requestFrame.getByRole('button',{name:'只读查看',exact:true}).count(),0);
 assert.equal(await requestFrame.locator('#request-preview').count(),0);
 await capture(requestPage,'queue-parallel-peer-root');
 // Forwarding the independent result must not replace the foreground card.
 const independent=await host.rpc('get_progress',{session_id:sid,task_instance_id:parallel.task_instance_id});await push(requestPage,independent);
 assert.equal(await requestFrame.locator('#task-title').textContent(),'验收进度示例');await capture(requestPage,'queue-parallel-foreground');
 // Explicit activation archives A. The old foreground is history, while the
 // parallel request keeps its source reference without promising a return to A.
 for(const id of ['one','two']) {
   const progress=(await host.rpc('get_progress',binding)).structuredContent.progress;
   const attempt_id=progress.nodes.find(node=>node.id===id).attempt_id;
   await host.rpc('review_task',{...binding,task_id:id,attempt_id,decision:'accept',summary:'已核验产出',evidence:['fixture://review-'+id],reviewer:mainAssignment});
 }
 let started=await host.rpc('update_task',{...binding,task_id:'three',execution_status:'running',assignment:mainAssignment});
 await host.rpc('update_task',{...binding,task_id:'three',attempt_id:started.structuredContent.progress.nodes.find(node=>node.id==='three').attempt_id,execution_status:'finished',outcome:'resolved',summary:'全部依赖已验收'});
 await host.rpc('update_task',{...binding,task_id:'root',execution_status:'finished',outcome:'resolved',assignment:mainAssignment,summary:'整体已核验',evidence:['fixture://root-review']});
 r=await host.rpc('activate_request',{...binding,request_id:'稍后处理'});await push(requestPage,r);
 await requestFrame.locator('#task-title').filter({hasText:'稍后处理'}).waitFor();
 assert.equal(await requestFrame.locator('.request-item').count(),0);
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
 r=await host.rpc('get_progress',{session_id:sid});await push(requestPage,r);
 await requestFrame.locator('#steps > .step[data-root-role="parallel"]').filter({hasText:'已如实收尾，问题未解决'}).waitFor();
 assert.equal(await requestFrame.locator('#steps > .step').count(),2);
 await capture(requestPage,'activated-queue-parallel-open');
 assert.equal(await requestFrame.getByRole('button',{name:'只读查看',exact:true}).count(),0);
 await requestPage.close();assert.deepEqual(host.errors,[]);assert.deepEqual(sourceIdentity(),sources,'App/server/test source changed during browser verification');
 if(artifacts)fs.writeFileSync(path.join(artifacts,'source-identities.json'),JSON.stringify({capturedAt:new Date().toISOString(),renderer:'isolated headless Chrome via Playwright',sources},null,2));
 console.log('Acceptance clarity, pending output previews, plan order, legacy unknown acceptance, root synthesis, request queue, parallel peer roots and foreground protection passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>host?.close());
