/* Real backend, task rows and exported execution identities. No live sessions. */
const assert = require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHost} = require('./browser_host.cjs');
const assignment={execution_mode:'main_session',self_execution_reason:'Isolated lifecycle browser fixture'};
let host;
async function main(){
  host=await createHost();
  const init=await host.rawRpc('init_task_network',{task_title:'执行生命周期',tasks:[
    {id:'check',title:'执行验证',assignment},{id:'deliver',title:'交付',depends_on:['check'],assignment}
  ]});
  const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.task_instance_id,task_id:'check'};
  let attempt=(await host.rawRpc('update_task',{...binding,execution_status:'running',assignment})).structuredContent.attempt_id;
  const event=async(execution_id,status,extra={})=>host.rawRpc('record_task_execution',{
    ...binding,attempt_id:attempt,execution_id,provider:'dagu',status,summary:status==='running'?'本轮运行':'本轮 '+status,
    ...(status==='running'?{}:{evidence:['fixture://'+execution_id]}),...extra
  });
  await event('browser-run-1','running');
  await event('browser-run-1','failed',{exit_code:1});
  attempt=(await host.rawRpc('update_task',{...binding,execution_status:'running',retry:true,assignment})).structuredContent.attempt_id;
  await event('browser-run-2','running');
  await event('browser-run-2','succeeded',{exit_code:0});
  const page=await host.page();
  const frame=await host.mount(page,'lifecycle',await host.rawRpc('render_progress',{session_id:binding.session_id}),'fullscreen');
  const task=frame.locator('details[data-key="task:'+binding.task_instance_id+':epoch:0:check"]');
  await task.waitFor();
  assert.match(await task.locator(':scope > summary').textContent(),/第 2 轮执行/);
  assert.match(await task.locator(':scope > summary .step-right').textContent(),/执行已结束 · 待任务收尾/);
  await task.locator(':scope > summary').click();
  assert.match(await task.locator('.execution-receipt').first().textContent(),/browser-run-2/);
  await assert.rejects(()=>host.rawRpc('update_task',{...binding,attempt_id:'incorrect-attempt',execution_status:'failed',summary:'Wrong closeout'}));
  const refresh=async()=>{await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();};
  await refresh();
  assert.equal(await task.locator('.closeout-recovery-required').count(),1);
  await host.rawRpc('update_task',{...binding,attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'Validated result with correct identity'});
  await refresh();
  assert.equal(await task.locator('.closeout-recovery-required').count(),0);
  await frame.locator('#export-all').click();
  await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const files=await page.evaluate(()=>window.host.calls.find(c=>c.name==='save_export_files').result.structuredContent.files);
  const report=await host.browser.newPage();
  await report.goto('file://'+files.find(f=>f.name.endsWith('.html')).path);
  const html=await report.content();
  assert.ok(html.includes('browser-run-1'));
  assert.ok(html.includes('browser-run-2'));
  assert.ok(html.includes('执行失败'));
  const workbook=files.find(f=>f.name.endsWith('.xlsx')).path;
  const xml=execFileSync('python3',['-c','import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print("".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xml")))',workbook],{encoding:'utf8'});
  assert.ok(xml.includes('browser-run-1'));
  assert.ok(xml.includes('browser-run-2'));
  assert.deepEqual(host.errors,[]);
  await report.close();await page.close();
  console.log('Execution lifecycle: automatic closeout, retry badge, waiting closeout and offline receipts passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
