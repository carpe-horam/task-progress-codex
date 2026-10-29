/* Real isolated backend + app UI + exported audit evidence, no live sessions. */
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHost}=require('./browser_host.cjs');
const MAIN={execution_mode:'main_session',self_execution_reason:'Bounded closure fixture'};
const SUB={execution_mode:'subagent',model:'fixture',reasoning_effort:'medium',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Produce isolated evidence'};
let host;
async function main(){
 host=await createHost();
 let result=await host.rpc('init_task_network',{task_title:'Foreground recovery',tasks:[{id:'target',title:'Runtime recovery',assignment:MAIN},{id:'later',title:'Later work',assignment:MAIN}]});
 const foreground={session_id:result.structuredContent.session_id,task_instance_id:result.structuredContent.progress.task_instance_id};
 const call=(name,args={})=>host.rpc(name,{...foreground,...args});
 const target=(await call('update_task',{task_id:'target',execution_status:'running',assignment:MAIN})).structuredContent.attempt_id;
 async function peer(rid,terminalRoot){
  const r=await call('route_request',{request_id:rid,request_text:'Source diagnostic work',task_title:rid,mode:'parallel',reason:'Independent diagnosis',acceptance_criteria:['Evidence retained']});
  const scope={session_id:foreground.session_id,task_instance_id:r.structuredContent.request_task_instance_id};
  await host.rpc('decompose_task',{...scope,task_id:'root',tasks:[{id:'diagnose',title:'Diagnosis',assignment:SUB},{id:'verify',title:'Diagnosis validation',assignment:SUB},{id:'cancelled',title:'Integration moved',assignment:SUB}]});
  for(const id of ['diagnose','verify']){
   const attempt=(await host.rpc('update_task',{...scope,task_id:id,execution_status:'running',assignment:SUB})).structuredContent.attempt_id;
   await host.rpc('bind_task_agent',{...scope,task_id:id,attempt_id:attempt,agent_name:rid+'-agent'});
   await host.rpc('update_task',{...scope,task_id:id,attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'Diagnosis complete',evidence:['fixture://diagnosis']});
   await host.rpc('review_task',{...scope,task_id:id,attempt_id:attempt,decision:'accept',reviewer:MAIN,summary:'Diagnosis accepted',evidence:['fixture://diagnosis-reviewed']});
  }
  await host.rpc('update_task',{...scope,task_id:'cancelled',execution_status:'cancelled',summary:'Transferred before dispatch'});
  if(terminalRoot)await host.rpc('update_task',{...scope,task_id:'root',execution_status:'finished',outcome:'open',assignment:MAIN,summary:'Diagnosis ended, recovery transferred',evidence:['fixture://original-open']});
  return scope;
 }
 const source=await peer('Handoff source',true);
 const page=await host.page(),frame=await host.mount(page,'main',await call('get_progress_view'));
 async function refresh(){await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();}
 const sourceRoot=frame.locator('#steps > .step[data-task-instance="'+source.task_instance_id+'"]');
 assert.match(await sourceRoot.innerText(),/待汇总验收/);
 const binding=(await host.rpc('get_progress',{...source})).structuredContent.progress.closure_review_binding;
 const registration=await call('handoff_request',{request_id:'Handoff source',handoff_id:'handoff-browser',source_task_instance_id:source.task_instance_id,...binding,mappings:[{source_task_id:'cancelled',source_attempt_id:null,target_task_instance_id:foreground.task_instance_id,target_epoch:0,target_task_id:'target',target_attempt_id:target}],summary:'Recovery explicitly assigned to foreground',evidence:['fixture://handoff-browser'],reviewer:MAIN});
 assert.equal(registration.structuredContent.handoff_id,'handoff-browser');
 await refresh();assert.equal(await sourceRoot.count(),1);
 await call('update_task',{task_id:'target',attempt_id:target,execution_status:'finished',outcome:'resolved',summary:'Runtime online and heartbeat verified',evidence:['fixture://runtime-heartbeat-accepted']});
 await refresh();await sourceRoot.waitFor({state:'detached'});
 await frame.locator('#task-history-title').click();
 const archives=frame.locator('#task-history-list > .history-item > details');await archives.first().waitFor();
 await archives.first().locator(':scope > summary').click();
 assert.match(await archives.first().innerText(),/移交已验收/);
 assert.match(await archives.first().innerText(),/2\/3/);
 const proof=archives.first().locator('.closure-audit').filter({hasText:'移交已验收'});await proof.locator(':scope > summary').click();
 assert.match(await proof.innerText(),/fixture:\/\/runtime-heartbeat-accepted/);
 assert.match(await proof.innerText(),/target_attempt_id/);
 const closureSource=await peer('Reviewed cancellation',false);
 await refresh();const pendingRoot=frame.locator('#steps > .step[data-task-instance="'+closureSource.task_instance_id+'"]');
 assert.match(await pendingRoot.innerText(),/待汇总验收/);
 const cb=(await host.rpc('get_progress',closureSource)).structuredContent.progress.closure_review_binding;
 await host.rpc('review_task_closure',{...closureSource,task_id:'root',review_id:'browser-closure',...cb,decision:'accept',summary:'Cancellation accepted; work will not continue',evidence:['fixture://cancelled-reviewed'],reviewer:MAIN});
 await refresh();await pendingRoot.waitFor({state:'detached'});
 await frame.locator('#export-all').click();await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
 const files=await page.evaluate(()=>window.host.calls.findLast(c=>c.name==='save_export_files').result.structuredContent.files);
 const report=await host.browser.newPage();await report.goto('file://'+files.find(f=>f.name.endsWith('.html')).path);
 const audit=await report.locator('#rp-closure-audit').textContent();
 for(const text of ['fixture://runtime-heartbeat-accepted','fixture://cancelled-reviewed','handoff-browser','browser-closure','target_attempt_id'])assert(audit.includes(text),text);
 const xml=execFileSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(''.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xml')))",files.find(f=>f.name.endsWith('.xlsx')).path],{encoding:'utf8'});
 for(const text of ['移交与收尾','fixture://runtime-heartbeat-accepted','fixture://cancelled-reviewed','source_attempt_id','target_attempt_id'])assert(xml.includes(text),text);
 const full=(await call('get_progress_view',{include_history:true})).structuredContent.progress;
 const old=full.task_history.find(t=>t.task_instance_id===source.task_instance_id);
 assert.equal(old.nodes.find(n=>n.id==='cancelled').execution_status,'cancelled');
 assert.equal(old.nodes.find(n=>n.id==='cancelled').attempt_id,null);
 assert.equal(old.nodes.find(n=>n.id==='root').outcome,'open');
 assert.deepEqual(host.errors,[]);
 await report.close();await page.close();
 console.log('Handoff and cancellation closure UI/history plus full HTML/XLSX audit evidence passed.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>host?.close());
