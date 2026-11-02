/* Real isolated RPC + browser UI + HTML/XLSX title audit and safe text insertion. */
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHost}=require('./browser_host.cjs');
let host;
async function main(){
  host=await createHost();
  const assignment={execution_mode:'main_session',self_execution_reason:'Isolated title fixture'};
  let result=await host.rpc('init_task_network',{task_title:'Original root',tasks:[
    {id:'a',title:'Original A',assignment},{id:'b',title:'Original B',assignment,depends_on:['a']}]});
  const sid=result.structuredContent.session_id,iid=result.structuredContent.task_instance_id;
  const rename=async(id,title,reason)=>{
    const p=(await host.rpc('get_progress',{session_id:sid,task_instance_id:iid})).structuredContent.progress;
    return host.rpc('revise_task_title',{session_id:sid,task_instance_id:iid,task_id:id,display_title:title,reason,expected_epoch:p.epoch,expected_revision:p.revision});
  };
  await host.rpc('snapshot_task_network',{session_id:sid,task_instance_id:iid,snapshot_id:'original',reason:'Keep original titles'});
  await rename('a','Attempt A','Pending clarification');
  result=await host.rpc('update_task',{session_id:sid,task_instance_id:iid,task_id:'a',execution_status:'running',assignment});
  const aid=result.structuredContent.progress.nodes.find(n=>n.id==='a').attempt_id;
  const title='Current A <img src=x onerror="window.titleInjected=1">';
  const reason='Clarify <script>window.reasonInjected=1</script> safely';
  await rename('a',title,reason);
  await rename('root','Current root','Root naming only');
  const compact=await host.rpc('get_progress_view',{session_id:sid,task_instance_id:iid});
  assert(!compact.structuredContent.progress.nodes.find(n=>n.id==='a').title_revisions);
  const page=await host.page(),frame=await host.mount(page,'titles',compact);
  assert.equal(await frame.locator('#task-title').textContent(),'Current root');
  assert((await frame.locator('#steps .task-title-text').allTextContents()).includes(title));
  const leaf=frame.locator('#steps .step-id').filter({hasText:/^a$/}).locator('xpath=ancestor::li[1]');
  await leaf.locator(':scope > details > summary').click();
  await leaf.locator(':scope > details > .execution-review > summary').click();
  await leaf.locator(':scope > details > .execution-review > .title-history > summary').click();
  await leaf.locator('.title-revision').first().waitFor();
  const audit=await leaf.locator('.title-history').textContent();
  assert(audit.includes('Original A')&&audit.includes('Pending clarification')&&audit.includes(reason));
  const b=frame.locator('#steps .step-id').filter({hasText:/^b$/}).locator('xpath=ancestor::li[1]');
  assert((await b.textContent()).includes(title+' (a)'));
  assert((await leaf.textContent()).includes('尝试时标题Attempt A'));
  await frame.locator('#history-title').click();
  await frame.locator('#history-list .history-item').first().waitFor();
  const history=await frame.locator('#history-list').textContent();
  assert(history.includes('Original root')&&history.includes('Original A')&&!history.includes(title));
  assert.equal(await frame.locator('img').count(),0);
  assert.equal(await frame.locator('body').evaluate(()=>window.titleInjected||window.reasonInjected||0),0);
  await page.close();
  await host.rpc('update_task',{session_id:sid,task_instance_id:iid,task_id:'a',execution_status:'finished',outcome:'resolved',summary:'Stable work',evidence:['fixture://proof'],attempt_id:aid});
  await rename('a',title+' terminal','Final name only');
  const current=await host.rpc('get_progress_view',{session_id:sid,task_instance_id:iid});
  const exported=await host.exportReport(current);
  assert.equal(await exported.report.locator('#rp-current [data-node-id="a"] > [data-task]').textContent(),title+' terminal');
  const htmlAudit=await exported.report.locator('#rp-current [data-node-id="a"] .rp-title-history').textContent();
  assert(htmlAudit.includes('Original A')&&htmlAudit.includes(reason)&&htmlAudit.includes('Final name only'));
  assert((await exported.report.locator('#rp-current [data-node-id="a"] .rp-attempt').textContent()).includes('尝试时标题：Attempt A'));
  assert.equal(await exported.report.locator('img').count(),0);
  assert.equal(await exported.report.evaluate(()=>window.titleInjected||window.reasonInjected||0),0);
  const call=await exported.hostPage.evaluate(()=>window.host.calls.findLast(c=>c.name==='save_export_files'));
  const workbook=call.result.structuredContent.files.find(f=>f.name.endsWith('.xlsx')).path;
  execFileSync('python3',['-c',`import sys,zipfile,xml.etree.ElementTree as E,json
ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with zipfile.ZipFile(sys.argv[1]) as z:
 names=[s.attrib['name'] for s in E.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns)]
 sheets={name:[[ ''.join(c.itertext()) for c in r.findall('s:c',ns)] for r in E.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')).findall('.//s:row',ns)] for i,name in enumerate(names,1)}
 tasks=sheets['任务清单']; a=next(dict(zip(tasks[0],r)) for r in tasks[1:] if dict(zip(tasks[0],r))['任务 ID']=='a')
 assert a['任务名称']==sys.argv[2] and a['原始标题']=='Original A',a
 attempts=sheets['执行尝试']; attempt=next(dict(zip(attempts[0],r)) for r in attempts[1:])
 assert attempt['任务名称']=='Attempt A' and attempt['当前显示标题']==sys.argv[2],attempt
 revisions=sheets['标题修订']; assert len(revisions)==5
 assert any(sys.argv[3] in r for r in revisions)
 raw=sheets['快照与修订']; records=[json.loads(r[-1]) for r in raw[1:] if r[3]=='标题修订']
 assert len(records)==4 and any(r['reason']==sys.argv[3] for r in records)
 assert all('original_title' in r for r in records)
`,workbook,title+' terminal',reason]);
  await exported.report.close();await exported.hostPage.close();
  const atomic=(await host.rpc('init_task_network',{task_title:'Atomic original',atomic_reason:'One operation'})).structuredContent.progress;
  await host.rpc('revise_task_title',{session_id:atomic.session_id,task_instance_id:atomic.task_instance_id,task_id:atomic.root_id,
    display_title:'Atomic display',reason:'Clarify atomic name',expected_epoch:atomic.epoch,expected_revision:atomic.revision});
  const atomicPage=await host.page(),atomicFrame=await host.mount(atomicPage,'atomic',await host.rpc('get_progress_view',{session_id:atomic.session_id}));
  assert.equal(await atomicFrame.locator('#task-title').textContent(),'Atomic display');
  assert.deepEqual(await atomicFrame.locator('#steps .task-title-text').allTextContents(),['Atomic display']);
  assert.equal(await atomicFrame.locator('#steps .children').count(),0);
  await atomicPage.close();
  assert.deepEqual(host.errors,[]);
  console.log('Display titles, original/attempt titles, lazy audit, immutable snapshot, dependencies and safe HTML/XLSX export verified.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>host?.close());
