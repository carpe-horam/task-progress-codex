/* Instance-bound record caches reject delayed peer replies and retry failures. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {createHost}=require('./browser_host.cjs');
const mainAssignment={execution_mode:'main_session',self_execution_reason:'Isolated record cache fixture'};
const assignment={execution_mode:'subagent',model:'record-fixture',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Produce isolated peer evidence.'};
let host;
async function main(){
 host=await createHost();
 const r=await host.rawRpc('init_task_network',{task_title:'Foreground stays foreground'});
 const binding={session_id:r.structuredContent.session_id,task_instance_id:r.structuredContent.task_instance_id};
 await host.rawRpc('decompose_task',{...binding,task_id:'root',tasks:[{id:'work',title:'Foreground leaf',assignment:mainAssignment}]});
 const routed=await host.rawRpc('route_request',{...binding,request_id:'peer',request_text:'Independent peer record fixture',task_title:'Peer record root',mode:'parallel',reason:'Independent acceptance scope',acceptance_criteria:['Peer records stay instance-bound']});
 const peer={session_id:binding.session_id,task_instance_id:routed.structuredContent.request_task_instance_id};
 await host.rawRpc('decompose_task',{...peer,task_id:'root',tasks:[{id:'work',title:'Peer leaf',assignment}]});
 const start=await host.rawRpc('update_task',{...peer,task_id:'work',execution_status:'running',assignment});
 const attempt=start.structuredContent.attempt_id;
 await host.rawRpc('bind_task_agent',{...peer,task_id:'work',attempt_id:attempt,agent_name:'/fixture/peer'});
 await host.rawRpc('update_task',{...peer,task_id:'work',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'PEER_PENDING_SENTINEL',evidence:['fixture://peer-submission']});
 const page=await host.page();
 await page.evaluate(()=>{const raw=window.hostRpc;window.records={hold:true,release:null,fail:false};window.hostRpc=async(name,args)=>{
   if(name==='get_progress_view'&&args.include_history){
     if(window.records.fail)throw new Error('Injected peer record failure');
     const result=await raw(name,args);
     if(window.records.hold){window.records.hold=false;await new Promise(resolve=>window.records.release=resolve);}
     return result;
   }
   return raw(name,args);
 };});
 const frame=await host.mount(page,'main',await host.rawRpc('render_progress',{session_id:binding.session_id}),'fullscreen');
 await frame.locator('#task-title').filter({hasText:'Foreground stays foreground'}).waitFor();
 const peerRoot=frame.locator('#steps > .step[data-task-instance="'+peer.task_instance_id+'"]');
 const leaf=peerRoot.locator(':scope > details > .children > .step');
 await leaf.locator(':scope > details > summary').click();
 await leaf.locator('.execution-review > summary').click();
 await leaf.locator('.attempt-history > summary').click();
 await page.waitForFunction(()=>!!window.records.release);
 await host.rawRpc('review_task',{...peer,task_id:'work',attempt_id:attempt,decision:'accept',summary:'PEER_ACCEPTED_SENTINEL',evidence:['fixture://peer-review'],reviewer:mainAssignment});
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 await leaf.locator('.review-history').filter({hasText:'PEER_ACCEPTED_SENTINEL'}).waitFor();
 await page.evaluate(()=>window.records.release());
 await page.waitForFunction(()=>window.host.calls.filter(call=>call.name==='get_progress_view'&&call.args.include_history).every(call=>call.completedAt));
 assert.equal(await frame.locator('#task-title').textContent(),'Foreground stays foreground');
 assert.deepEqual(await frame.locator('#steps > .step').evaluateAll(items=>items.map(item=>item.dataset.taskInstance)),[binding.task_instance_id,peer.task_instance_id]);
 assert.match(await leaf.locator('.review-history').textContent(),/PEER_ACCEPTED_SENTINEL/);
 // A new attempt invalidates the full-record cache. Failure must not auto-loop.
 await leaf.locator('.attempt-history > summary').click();
 await page.evaluate(()=>{window.records.fail=true;});
 await host.rawRpc('update_task',{...peer,task_id:'work',execution_status:'running',retry:true,assignment});
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 await leaf.locator('.attempt-history > summary').click();
 await leaf.locator('.attempt-history .record-load-message').filter({hasText:'Injected peer record failure'}).waitFor();
 const count=await page.evaluate(()=>window.host.calls.filter(call=>call.name==='get_progress_view'&&call.args.include_history).length);
 await page.evaluate(()=>{window.records.fail=false;});
 await leaf.locator('.attempt-history > summary').click();await leaf.locator('.attempt-history > summary').click();
 await leaf.locator('.attempt-history .attempt-record').first().waitFor();
 assert.equal(await page.evaluate(()=>window.host.calls.filter(call=>call.name==='get_progress_view'&&call.args.include_history).length),count+1);
 assert.equal(await frame.locator('#task-title').textContent(),'Foreground stays foreground');
 assert.equal(await leaf.locator('.attempt-history .attempt-record').count(),2);
 const result={passed:['A delayed peer record reply cannot overwrite the newer peer revision or foreground identity.','New attempts invalidate the instance cache; failed record reads retry once when reopened.'],verification:'Isolated Python + Chrome, simulated MCP Apps host'};
 if(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS){const directory=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;fs.mkdirSync(directory,{recursive:true});await page.evaluate(()=>{const iframe=document.getElementById('main');iframe.style.height=iframe.contentDocument.documentElement.scrollHeight+'px';});await page.screenshot({path:path.join(directory,'peer-records.png'),fullPage:true});fs.writeFileSync(path.join(directory,'results.json'),JSON.stringify(result,null,2));}
 assert.deepEqual(host.errors,[]);await page.close();console.log(JSON.stringify(result,null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
