/* Same-list peer roots against real isolated server state and browser polling. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const {createHost}=require('./browser_host.cjs');
const sources=()=>Object.fromEntries(['plugins/task-progress/app.html','plugins/task-progress/server.py','tests/test_parallel_roots_app.cjs'].map(file=>[file,createHash('sha256').update(fs.readFileSync(path.join(__dirname,'..',file))).digest('hex')]));
const artifacts=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const mainAssignment={execution_mode:'main_session',self_execution_reason:'Isolated peer-root browser fixture'};
const subAssignment={execution_mode:'subagent',model:'fixture-model',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Submit the isolated peer-root output and evidence.'};
let host;
async function push(page,snapshot){await page.evaluate(snapshot=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot},'*'),snapshot);}
async function capture(page,name,snapshot){if(!artifacts)return;fs.mkdirSync(artifacts,{recursive:true});await page.evaluate(()=>{const iframe=document.getElementById('main');iframe.style.height=iframe.contentDocument.documentElement.scrollHeight+'px';});await page.screenshot({path:path.join(artifacts,name+'.png'),fullPage:true});fs.writeFileSync(path.join(artifacts,name+'.json'),JSON.stringify(snapshot,null,2));}
async function main(){
 const sourceIdentity=sources();host=await createHost();
 let r=await host.rpc('init_task_network',{task_title:'主任务 A · 同名节点隔离'});
 const sid=r.structuredContent.session_id,aid=r.structuredContent.progress.task_instance_id,binding={session_id:sid,task_instance_id:aid};
 await host.rpc('decompose_task',{...binding,task_id:'root',tasks:[{id:'work',title:'相同子任务 ID · 主任务',assignment:mainAssignment}]});
 async function route(id,mode,title){return host.rpc('route_request',{...binding,request_id:id,request_text:'真实隔离服务示例 '+id,task_title:title,mode,reason:'独立结果范围',acceptance_criteria:['根任务各自保留验收状态']});}
 await route('queue','queue','排队任务 Q');
 r=await route('p1','parallel','并行任务 P1');
 r=await route('p2','parallel','并行任务 P2');
 const requests=r.structuredContent.session_work.requests;
 const p1=requests.find(item=>item.request_id==='p1').task_instance_id,p2=requests.find(item=>item.request_id==='p2').task_instance_id;
 for(const iid of [p1,p2])await host.rpc('decompose_task',{session_id:sid,task_instance_id:iid,task_id:'root',tasks:[{id:'work',title:'相同子任务 ID · '+(iid===p1?'P1':'P2'),assignment:subAssignment}]});
 const b1={session_id:sid,task_instance_id:p1},b2={session_id:sid,task_instance_id:p2};
 r=await host.rpc('update_task',{...b1,task_id:'work',execution_status:'running',assignment:subAssignment});
 const attempt=r.structuredContent.progress.nodes.find(n=>n.id==='work').attempt_id;
 await host.rpc('bind_task_agent',{...b1,task_id:'work',attempt_id:attempt,agent_name:'fixture-p1'});
 await host.rpc('update_task',{...b1,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'P1 产出已提交，等主会话验收',evidence:['fixture://p1-output']});
 async function completeMain(b){const start=await host.rpc('update_task',{...b,task_id:'work',execution_status:'running',assignment:mainAssignment});await host.rpc('update_task',{...b,task_id:'work',attempt_id:start.structuredContent.progress.nodes.find(n=>n.id==='work').attempt_id,execution_status:'finished',outcome:'resolved',summary:'独立产出已核验'});await host.rpc('update_task',{...b,task_id:'root',execution_status:'finished',outcome:'resolved',assignment:mainAssignment,summary:'本根任务综合验收完成',evidence:['fixture://root-acceptance']});}
 r=await host.rpc('update_task',{...b2,task_id:'work',execution_status:'running',assignment:subAssignment});
 const p2Attempt=r.structuredContent.progress.nodes.find(n=>n.id==='work').attempt_id;
 await host.rpc('bind_task_agent',{...b2,task_id:'work',attempt_id:p2Attempt,agent_name:'fixture-p2'});
 await host.rpc('update_task',{...b2,task_id:'work',attempt_id:p2Attempt,execution_status:'finished',outcome:'resolved',summary:'P2 独立产出完成',evidence:['fixture://p2-output']});
 await host.rpc('review_task',{...b2,task_id:'work',attempt_id:p2Attempt,decision:'accept',summary:'P2 独立产出已核验',evidence:['fixture://p2-review'],reviewer:mainAssignment});
 r=await host.rpc('get_progress_view',{session_id:sid});
 const page=await host.page(),frame=await host.mount(page,'main',r);
 const roots=frame.locator('#steps > .step');
 assert.deepEqual(await roots.evaluateAll(elements=>elements.map(e=>e.dataset.taskInstance)),[aid,p1,p2]);
 assert.deepEqual(await frame.locator('#steps > .step > details > summary .step-id').allTextContents(),['root','root','root']);
 assert.equal(await frame.locator('#steps > .step > details > .children > .step').count(),3);
 assert.equal(await frame.locator('#steps > .step[data-root-role="main"]').count(),1);
 assert.equal(await frame.locator('#steps > .step[data-root-role="parallel"]').count(),2);
 assert.equal(await frame.locator('#steps-title').textContent(),'任务网络');
 assert.equal(await frame.locator('#percent').textContent(),'0');
 assert.match(await frame.locator('#fraction').textContent(),/主任务/);
 assert.match(await frame.locator('#acceptance-status').textContent(),/主任务综合验收：待主会话综合验收/);
 assert.match(await frame.locator('#step-count').textContent(),/3 个根任务 · 6 个节点 · 3 个叶子任务/);
 assert.equal(await frame.locator('#session-work .request-item').count(),1);
 assert.equal(await frame.getByRole('button',{name:'只读查看',exact:true}).count(),0);
 const p1Root=frame.locator('#steps > .step[data-task-instance="'+p1+'"]'),p2Root=frame.locator('#steps > .step[data-task-instance="'+p2+'"]');
 assert.match(await p1Root.innerText(),/待主会话验收/);assert.match(await p2Root.innerText(),/待汇总验收/);
 assert.deepEqual(await frame.locator('#steps > .step > details').evaluateAll(elements=>elements.map(element=>element.open)),[true,true,true]);
 await capture(page,'01-peer-roots-pending-accepted',r);
 // Preserve root expansion, leaf expansion, DOM identity and unchanged selected text.
 await p1Root.locator(':scope > details > .children > .step > details > summary').click();
 await p1Root.locator(':scope > details > .children > .step > details > .task-details > summary').click();
 await p2Root.locator(':scope > details > summary').click();
 await p1Root.locator('.leaf-result-summary .detail-value').filter({hasText:'P1 产出已提交'}).first().evaluate(element=>{window.peerSelected=element.firstChild;const range=document.createRange();range.selectNodeContents(element);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);window.peerText=selection.toString();});
 const selection=await p1Root.locator('.leaf-result-summary .detail-value').filter({hasText:'P1 产出已提交'}).first().evaluate(()=>window.peerText);
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 assert.equal(await p1Root.locator('.leaf-result-summary .detail-value').filter({hasText:'P1 产出已提交'}).first().evaluate(element=>element.firstChild===window.peerSelected),true);
 assert.equal(await p1Root.locator('.leaf-result-summary .detail-value').filter({hasText:'P1 产出已提交'}).first().evaluate(()=>getSelection().toString()),selection);
 assert.equal(await p1Root.locator(':scope > details > .children > .step > details').getAttribute('open'),'');
 assert.equal(await p2Root.locator(':scope > details').getAttribute('open'),null);
 await host.rpc('review_task',{...b1,task_id:'work',attempt_id:attempt,decision:'accept',summary:'P1 产出已核验',evidence:['fixture://p1-review'],reviewer:mainAssignment});
 await host.rpc('update_task',{...b1,task_id:'root',execution_status:'finished',outcome:'resolved',assignment:mainAssignment,summary:'P1 综合验收通过',evidence:['fixture://p1-root']});
 await host.rpc('update_task',{...b2,task_id:'root',execution_status:'finished',outcome:'resolved',assignment:mainAssignment,summary:'P2 综合验收通过',evidence:['fixture://p2-root']});
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 assert.equal(await p1Root.count(),0);assert.equal(await p2Root.count(),0);
 assert.match(await frame.locator('#task-history-title').textContent(),/2 项/);
 assert.equal(await frame.locator('#percent').textContent(),'0');
 await completeMain(binding);r=await host.rpc('activate_request',{...binding,request_id:'queue'});await push(page,r);
 await frame.locator('#task-title').filter({hasText:'排队任务 Q'}).waitFor();
 assert.deepEqual(await roots.evaluateAll(elements=>elements.map(e=>e.dataset.taskInstance)),[r.structuredContent.progress.task_instance_id]);
 await frame.locator('#task-history-section > summary').click();await frame.locator('#task-history-list').filter({hasText:'主任务 A'}).waitFor();
 assert.equal(await frame.locator('#steps > .step[data-task-instance="'+aid+'"]').count(),0);
 assert.equal(await frame.locator('#percent').textContent(),'0');
 r=await host.rpc('get_progress_view',{session_id:sid,include_history:true});
 await capture(page,'02-activated-queue-archives-completed-peer-roots',r);
 await page.close();
 // An old cache has named roots but no details: do not invent empty child trees.
 const old=structuredClone(r);delete old.structuredContent.progress.parallel_tasks;
 for(const request of old.structuredContent.session_work.requests)if(request.mode==='parallel')request.status='active';
 old.structuredContent.progress.session_work=old.structuredContent.session_work;
 const oldPage=await host.page();await oldPage.evaluate(old=>{const real=window.hostRpc;window.hostRpc=(name,args)=>name==='get_progress_view'?Promise.resolve(structuredClone(old)):real(name,args);},old);
 const oldFrame=await host.mount(oldPage,'main',old);
 assert.equal(await oldFrame.locator('#steps > .parallel-placeholder').count(),2);assert.equal(await oldFrame.locator('#steps > .parallel-placeholder .children').count(),0);
 await push(oldPage,r);await oldFrame.locator('#steps > .parallel-placeholder').waitFor({state:'detached'});
 assert.equal(await oldFrame.locator('#steps > .step').count(),1);assert.equal(await oldFrame.locator('#steps > .step > details > .children > .step').count(),0);
 await oldPage.close();
 // Unstarted cancellation remains a visible peer root without ready work.
 const currentBinding={session_id:sid,task_instance_id:r.structuredContent.progress.task_instance_id};
 let cancelled=await host.rpc('route_request',{...currentBinding,request_id:'cancelled',request_text:'Cancelled isolated fixture',task_title:'并行任务 P3 · 已取消',mode:'parallel',reason:'独立取消边界',acceptance_criteria:['取消后不显示可执行']});
 const cancelledId=cancelled.structuredContent.session_work.requests.find(item=>item.request_id==='cancelled').task_instance_id;
 await host.rpc('decompose_task',{session_id:sid,task_instance_id:cancelledId,task_id:'root',tasks:[{id:'work',title:'尚未执行的并行任务',assignment:subAssignment}]});
 cancelled=await host.rpc('cancel_request',{...currentBinding,request_id:'cancelled',reason:'User withdrew before dispatch'});
 const cancelledPage=await host.page(),cancelledFrame=await host.mount(cancelledPage,'main',cancelled);
 const cancelledRoot=cancelledFrame.locator('#steps > .step[data-task-instance="'+cancelledId+'"]');
 assert.match(await cancelledRoot.innerText(),/未解决 · 请求已取消/);assert.match(await cancelledRoot.innerText(),/未执行 · 请求已取消/);assert(!/可执行/.test(await cancelledRoot.innerText()));
 assert.equal(await cancelledRoot.locator('.ready').count(),0);
 await capture(cancelledPage,'03-cancelled-peer-root',cancelled);
 assert.deepEqual(host.errors,[]);assert.deepEqual(sources(),sourceIdentity,'Source changed during peer-root verification');
 if(artifacts)fs.writeFileSync(path.join(artifacts,'peer-roots-source-identities.json'),JSON.stringify({capturedAt:new Date().toISOString(),renderer:'Headless Chrome via Playwright, real isolated Python server fixtures',sources:sourceIdentity},null,2));
 console.log('Peer-root order, same-ID isolation, foreground progress scope, pending acceptance refresh, DOM selection/expansion, queue activation/history and old-cache compatibility passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>host?.close());
