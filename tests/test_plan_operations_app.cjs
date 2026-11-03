/* Actual isolated server + simulated host: lazy snapshot/revision UI. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHost} = require('./browser_host.cjs');
let host;
async function main() {
  host = await createHost();
  const main = {execution_mode:'main_session', self_execution_reason:'Isolated browser fixture'};
  const init = await host.rpc('init_task_network', {task_title:'Snapshot fixture', tasks:[
    {id:'a', title:'A', assignment:main}, {id:'b', title:'B', assignment:main, depends_on:['a']}]});
  const sid=init.structuredContent.session_id, iid=init.structuredContent.task_instance_id;
  await host.rpc('snapshot_task_network',{session_id:sid, task_instance_id:iid, snapshot_id:'before', reason:'Capture without stopping'});
  const overview=await host.rpc('get_progress',{session_id:sid, task_instance_id:iid});
  await host.rpc('revise_task_plan',{session_id:sid, task_instance_id:iid, task_id:'b', reason:'Independent B',
    expected_epoch:overview.structuredContent.progress.epoch, expected_revision:overview.structuredContent.progress.revision, depends_on:[]});
  const compact=await host.rpc('get_progress_view',{session_id:sid});
  const page=await host.page(), frame=await host.mount(page,'main',compact);
  assert((await frame.locator('#history-title').textContent()).includes('快照 1 · 修订 1'));
  await frame.locator('#history-title').click();
  await frame.locator('#history-list').filter({hasText:'计划快照'}).waitFor();
  const history=await frame.locator('#history-list').textContent();
  assert(history.includes('Capture without stopping') && history.includes('Independent B'));
  const revision=frame.locator('#history-list .history-item').filter({hasText:'原位修订'});
  await revision.locator('summary').first().click();
  assert.equal(await revision.locator('.revision-plan').count(), 2);
  const planKeys=[];
  for(const side of ['before','after']) {
    const plan=revision.locator('.revision-plan.'+side);
    assert.equal(await plan.evaluate(el=>el.open), false);
    planKeys.push(await plan.getAttribute('data-key'));
    await plan.locator('summary').first().click();
    assert.equal(await plan.evaluate(el=>el.open), true);
    assert.equal(await plan.locator('.task-tree .step-id').count(), 3);
    assert((await plan.locator('.task-tree').textContent()).includes('Snapshot fixture'));
  }
  assert.notEqual(planKeys[0],planKeys[1]);
  assert((await revision.locator('.revision-plan.before .task-tree').textContent()).includes('依赖任务'));
  assert(!(await revision.locator('.revision-plan.after .task-tree').textContent()).includes('依赖任务'));
  assert.deepEqual(await revision.locator('.revision-field').allTextContents(), ['depends_on']);
  assert((await revision.locator('.revision-value.before pre').textContent()).includes('"a"'));
  assert.equal(await revision.locator('.revision-value.after pre').textContent(), '[]');
  assert.equal(await revision.locator('.revision-raw').evaluate(el=>el.open), false);
  await revision.locator('.revision-raw summary').click();
  assert((await revision.locator('.revision-raw pre').textContent()).includes('node_order'));
  const raw=JSON.parse(await revision.locator('.revision-raw pre').textContent());
  assert.deepEqual(raw.before_plan.nodes.b.depends_on,['a']);
  assert.deepEqual(raw.after_plan.nodes.b.depends_on,[]);
  await page.close();
  const legacy=structuredClone(await host.rpc('get_progress_view',{session_id:sid,include_history:true}));
  for(const record of legacy.structuredContent.progress.plan_revisions) {delete record.before_plan; delete record.after_plan;}
  const legacyPage=await host.page(), legacyFrame=await host.mount(legacyPage,'legacy',legacy);
  await legacyFrame.locator('#history-title').click();
  const legacyRevision=legacyFrame.locator('#history-list .history-item').filter({hasText:'原位修订'});
  await legacyRevision.locator('summary').first().click();
  assert.equal(await legacyRevision.locator('.revision-plan').count(),0);
  assert((await legacyRevision.textContent()).includes('旧修订记录未保存完整任务树'));
  assert.deepEqual(await legacyRevision.locator('.revision-field').allTextContents(),['depends_on']);
  await legacyRevision.locator('.revision-raw summary').click();
  assert.deepEqual(JSON.parse(await legacyRevision.locator('.revision-raw pre').textContent()).before.depends_on,['a']);
  await legacyPage.close();
  const longReason='x'.repeat(29999)+'😀</script>TAIL';
  await host.rpc('snapshot_task_network',{session_id:sid,task_instance_id:iid,snapshot_id:'long',reason:longReason});
  const next=await host.rpc('init_task_network',{session_id:sid,task_title:'Independent fixture',atomic_reason:'One fixture',
    task_relationship:'independent',new_task_reason:'Independent test target'});
  const nextId=next.structuredContent.task_instance_id;
  await host.rpc('snapshot_task_network',{session_id:sid,task_instance_id:nextId,snapshot_id:'current',reason:'Current snapshot'});
  const peer=await host.rpc('route_request',{session_id:sid,task_instance_id:nextId,request_id:'peer',request_text:'Parallel fixture',
    task_title:'Peer fixture',mode:'parallel',reason:'Isolated peer',acceptance_criteria:['Delivery']});
  const peerId=peer.structuredContent.request_task_instance_id;
  await host.rpc('snapshot_task_network',{session_id:sid,task_instance_id:peerId,snapshot_id:'peer-snapshot',reason:'Parallel snapshot'});
  const current=await host.rpc('get_progress_view',{session_id:sid});
  const exp=await host.exportReport(current);
  const html=await exp.report.locator('#rp-plan-operations').textContent();
  assert(html.includes('历史任务') && html.includes('当前任务') && html.includes('并行任务') && html.includes('peer-snapshot') && html.includes('原位修订') && html.includes('😀</script>TAIL'));
  const save=await exp.hostPage.evaluate(()=>window.host.calls.findLast(c=>c.name==='save_export_files'));
  const workbook=save.result.structuredContent.files.find(f=>f.name.endsWith('.xlsx')).path;
  execFileSync('python3',['-c',`import json,sys,zipfile,xml.etree.ElementTree as E
ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
with zipfile.ZipFile(sys.argv[1]) as z:
 w=E.fromstring(z.read('xl/workbook.xml')); sheets=w.find('s:sheets',ns); index=next(i for i,s in enumerate(sheets,1) if s.attrib['name']=='快照与修订')
 rows=E.fromstring(z.read(f'xl/worksheets/sheet{index}.xml')).findall('.//s:row',ns)
 groups={}
 for row in rows[1:]:
  vals={''.join(c.attrib['r'].rstrip('0123456789')):''.join(c.itertext()) for c in row.findall('s:c',ns)}
  groups.setdefault(vals['E'],[]).append((int(vals['I']),vals['J']))
 stored=json.load(open(sys.argv[2])); expected={x['snapshot_id']:x for t in stored['task_history'] for x in t.get('plan_snapshots',[])}
 expected.update({x['revision_id']:x for t in stored['task_history'] for x in t.get('plan_revisions',[])})
 expected.update({x['snapshot_id']:x for x in stored['plan_snapshots']})
 for peer in stored.get('session_work_state',{}).get('instances',{}).values(): expected.update({x['snapshot_id']:x for x in peer.get('plan_snapshots',[])})
 assert set(groups)==set(expected),(groups.keys(),expected.keys())
 def unproject(value):
  if isinstance(value,dict): return {k:unproject(v) for k,v in value.items() if k not in {'ended_leaf_count','ended_percent','accepted_resolved_leaf_count','accepted_resolved_percent','accepted_resolved','coverage_records','coverage_summary','current_coverage','coverage_count','coverage_record_count','stale_coverage_count','external_prerequisite_review_count','external_prerequisite_stale','external_prerequisite_review','actionable'}}
  if isinstance(value,list): return [unproject(v) for v in value]
  return value
 for key,parts in groups.items():
  actual=unproject(json.loads(''.join(v for _,v in sorted(parts))))
  assert actual==expected[key],key
 assert len(groups['long'])>1
`,workbook,path.join(host.temp,'sessions',sid+'.json')]);
  await exp.report.close(); await exp.hostPage.close();
  assert.deepEqual(host.errors,[]);
  console.log('Snapshot and revision displayed and exported, including archived records, exact JSON and long Unicode chunks.');
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
