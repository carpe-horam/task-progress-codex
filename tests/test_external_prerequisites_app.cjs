/* Real isolated RPC and Chrome UI/export regression for independent prerequisite claims. */
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {createHost}=require('./browser_host.cjs');
let host;
async function main(){
  host=await createHost();
  const assignment={execution_mode:'main_session',self_execution_reason:'Isolated external prerequisite fixture'};
  const init=(await host.rpc('init_task_network',{task_title:'External prerequisite fixture',tasks:[
    {id:'a',title:'Ready A',assignment},{id:'b',title:'Blocked B',assignment,depends_on:['a']}]})).structuredContent.progress;
  const binding={session_id:init.session_id,task_instance_id:init.task_instance_id};
  const reviewer={execution_mode:'main_session',agent_name:'Reviewer <img src=x onerror="window.externalInjected=1">'};
  const reason='Inspection <script>window.externalInjected=2</script>';
  const evidence=['fixture://proof <img src=x onerror="window.externalInjected=3">'];
  const record=async(status,extra={})=>{
    const p=(await host.rpc('get_progress',binding)).structuredContent.progress;
    return host.rpc('set_external_prerequisite',{...binding,task_id:'a',status,reason,evidence,reviewer,expected_epoch:p.epoch,expected_revision:p.revision,...extra});
  };
  const page=await host.page(),frame=await host.mount(page,'external',await host.rawRpc('get_progress_view',binding));
  const leaf=frame.locator('#steps .step-id').filter({hasText:/^a$/}).locator('xpath=ancestor::li[1]');
  const right=leaf.locator(':scope > details > summary .step-right');
  assert.match(await right.textContent(),/依赖就绪/);assert.match(await right.textContent(),/外部前置未知/);assert(!/可执行/.test(await right.textContent()));
  const refresh=async()=>{await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();};
  await record('blocked');await refresh();assert.match(await right.textContent(),/依赖就绪.*外部前置受阻/s);
  await record('satisfied');await refresh();assert.match(await right.textContent(),/外部前置已确认/);
  await leaf.locator(':scope > details > summary').click();
  await leaf.locator(':scope > details > .execution-review > summary').click();
  const external=leaf.locator('.external-prerequisite');
  assert((await external.textContent()).includes(reason));assert((await external.textContent()).includes(evidence[0]));
  assert.match(await external.textContent(),/仍须现场判断/);
  await external.locator('.external-prerequisite-history > summary').click();
  await external.locator('.external-prerequisite-record').first().waitFor();
  assert.equal(await external.locator('.external-prerequisite-record').count(),2);
  assert((await external.textContent()).includes(reviewer.agent_name));
  assert.equal(await frame.locator('img').count(),0);assert.equal(await frame.locator('body').evaluate(()=>window.externalInjected||0),0);
  let result=await host.rpc('update_task',{...binding,task_id:'a',execution_status:'running',assignment});
  const aid=result.structuredContent.attempt_id;
  await record('satisfied',{source_attempt_id:aid});
  await host.rpc('update_task',{...binding,task_id:'a',execution_status:'finished',outcome:'open',summary:'Attempt remains unresolved',attempt_id:aid});
  await host.rpc('update_task',{...binding,task_id:'a',execution_status:'running',retry:true,assignment});
  await refresh();assert.match(await right.textContent(),/外部前置未知.*stale/);
  await page.close();
  const current=await host.rawRpc('get_progress_view',binding);
  const exported=await host.exportReport(current);
  const html=await exported.report.locator('#rp-current [data-node-id="a"] .rp-external-prerequisite').textContent();
  assert(html.includes('外部前置未知 · stale'));assert(html.includes('仍须现场判断'));assert(html.includes(reason));assert(html.includes(reviewer.agent_name));assert(html.includes(aid));
  assert.equal(await exported.report.locator('img').count(),0);assert.equal(await exported.report.evaluate(()=>window.externalInjected||0),0);
  const call=await exported.hostPage.evaluate(()=>window.host.calls.findLast(c=>c.name==='save_export_files'));
  const workbook=call.result.structuredContent.files.find(f=>f.name.endsWith('.xlsx')).path;
  execFileSync('python3',['-c',`import sys,zipfile,xml.etree.ElementTree as E
ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with zipfile.ZipFile(sys.argv[1]) as z:
 names=[s.attrib['name'] for s in E.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns)]
 sheets={name:[[ ''.join(c.itertext()) for c in r.findall('s:c',ns)] for r in E.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')).findall('.//s:row',ns)] for i,name in enumerate(names,1)}
 rows=sheets['任务清单']; a=next(dict(zip(rows[0],r)) for r in rows[1:] if dict(zip(rows[0],r))['任务 ID']=='a')
 assert a['外部前置状态']=='unknown' and 'stale' in a['外部前置标签'] and a['现场判断']=='仍须现场判断',a
 assert a['最近人工判断原因']==sys.argv[2] and a['前置来源 Attempt ID']==sys.argv[3],a
 reviews=sheets['外部前置人工记录']; assert len(reviews)==4,reviews
 assert any(sys.argv[2] in r for r in reviews)
`,workbook,reason,aid]);
  await exported.report.close();await exported.hostPage.close();
  assert.deepEqual(host.errors,[]);
  console.log('External prerequisites: independent ready labels, lazy manual history, safe text, retry stale, HTML/XLSX exports passed in Chrome.');
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>host?.close());
