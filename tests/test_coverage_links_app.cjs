/* Isolated backend, real browser tree and HTML/XLSX coverage audit export. */
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHost}=require('./browser_host.cjs');
const MAIN={execution_mode:'main_session',self_execution_reason:'Bounded coverage fixture'};
let host;
async function main(){
 host=await createHost();
 const init=await host.rpc('init_task_network',{task_title:'Endpoint diagnosis',tasks:[
  {id:'T3',title:'Full integration',assignment:MAIN},{id:'T4',title:'Health endpoint proof',assignment:MAIN},
  {id:'rest',title:'Other endpoints still pending',assignment:MAIN},
  {id:'after',title:'Dependent integration',assignment:MAIN,depends_on:['T3']}]});
 const scope={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.progress.task_instance_id};
 const call=(name,args={})=>host.rpc(name,{...scope,...args});
 async function finish(task_id,execution_status='finished',retry=false){
  const started=await call('update_task',{task_id,execution_status:'running',assignment:MAIN,retry});
  const attempt_id=started.structuredContent.attempt_id;
  await call('update_task',{task_id,execution_status,...(execution_status==='finished'?{outcome:'resolved'}:{}),attempt_id,summary:'Endpoint result',evidence:['fixture://endpoint-proof']});
  return attempt_id;
 }
 const target=await finish('T3','failed'),source=await finish('T4');
 const unsafe='<img src=x onerror="window.coverageInjected=true">';
 await call('record_coverage',{coverage_id:'browser-health',source_task_id:'T4',source_attempt_id:source,
  target_task_instance_id:scope.task_instance_id,target_epoch:0,target_task_id:'T3',target_attempt_id:target,
  scope:'GET /health only; remaining endpoints unverified '+unsafe,extent:'partial',reason:'Narrow endpoint diagnosis',evidence:['fixture://health-diagnostic'],reviewer:MAIN});
 const page=await host.page(),frame=await host.mount(page,'main',await call('get_progress_view'));
 const t3=frame.locator('#step-list .plan-row[data-step-id="T3"]');
 assert.match(await t3.innerText(),/失败/);
 await t3.locator('.plan-title').click();await frame.locator('#detail-tab-raw').click();
 const coverage=frame.locator('#detail-raw-fields details[data-key$=":audit"]');
 await coverage.locator(':scope > summary').click();
 assert.match(await coverage.textContent(),/GET \/health only/);
 assert.match(await coverage.textContent(),/Narrow endpoint diagnosis/);
 assert.equal(await coverage.locator('img').count(),0);
 assert.match(await coverage.textContent(),new RegExp(target));
 await frame.locator('#step-detail-back').click();
 const full=(await call('get_progress_view',{include_history:true})).structuredContent.progress;
 assert.equal(full.nodes.find(n=>n.id==='after').ready,false);
 assert.equal(full.nodes.find(n=>n.id==='rest').execution_status,'pending');
 assert.equal(full.nodes.find(n=>n.id==='root').outcome,null);
 await finish('T4','finished',true);
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 assert.match(await t3.innerText(),/失败/);
 await t3.locator('.plan-title').click();await frame.locator('#detail-tab-raw').click();
 const history=frame.locator('#detail-raw-fields details[data-key$=":audit"]');
 await history.locator(':scope > summary').click();
 assert.match(await history.textContent(),/来源任务已开始新的尝试/);
 assert.match(await history.textContent(),new RegExp(source));
 assert.match(await history.textContent(),new RegExp(target));
 await frame.locator('#step-detail-back').click();
 await frame.locator('#export-all').click();await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
 const files=await page.evaluate(()=>window.host.calls.findLast(c=>c.name==='save_export_files').result.structuredContent.files);
 const report=await host.browser.newPage();await report.goto('file://'+files.find(f=>f.name.endsWith('.html')).path);
 const audit=await report.locator('#rp-closure-audit').textContent();
 for(const value of ['browser-health','stale','source_attempt_changed','GET /health only','Narrow endpoint diagnosis','fixture://health-diagnostic',source,target])assert(audit.includes(value),value);
 assert.equal(await report.locator('#rp-closure-audit img').count(),0);
 const xml=execFileSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(''.join(z.read(n).decode() for n in z.namelist() if n.endswith('.xml')))",files.find(f=>f.name.endsWith('.xlsx')).path],{encoding:'utf8'});
 for(const value of ['browser-health','source_attempt_changed','target_attempt_id','fixture://health-diagnostic',source,target])assert(xml.includes(value),value);
 assert.deepEqual(host.errors,[]);
 await report.close();await page.close();
 console.log('Coverage scope, unchanged failure/dependency/root, stale retry history and safe HTML/XLSX export passed.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>host?.close());
