/* Retired UI fields disappear while original plan snapshots remain intact. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHost} = require('./browser_host.cjs');
let host;
async function main() {
  host = await createHost();
  const assignment = {execution_mode:'main_session', self_execution_reason:'Isolated dependency fixture'};
  const init = (await host.rpc('init_task_network', {task_title:'Dependency fixture', tasks:[
    {id:'a', title:'Ready A', assignment},
    {id:'b', title:'Blocked B', assignment, depends_on:['a']},
  ]})).structuredContent.progress;
  const binding = {session_id:init.session_id, task_instance_id:init.task_instance_id};
  // Reopen a genuine old-format task, including its preserved plan snapshot.
  const sessionPath = path.join(host.temp, 'sessions', init.session_id + '.json');
  const legacy = JSON.parse(fs.readFileSync(sessionPath, 'utf8'));
  for (const [id, status] of [['a', 'blocked'], ['b', 'satisfied']]) {
    legacy.nodes[id].external_prerequisite_status = status;
    legacy.nodes[id].external_prerequisite_reviews = [{status, reason:'Historical manual record', evidence:['fixture://old']}];
  }
  fs.writeFileSync(sessionPath, JSON.stringify(legacy));
  await host.rpc('snapshot_task_network', {...binding, snapshot_id:'old-records', reason:'Legacy compatibility'});
  const page = await host.page();
  const frame = await host.mount(page, 'dependencies', await host.rawRpc('get_progress_view', binding));
  const leaf = id => frame.locator('#steps .step-id').filter({hasText:new RegExp('^' + id + '$')}).locator('xpath=ancestor::li[1]');
  const right = id => leaf(id).locator(':scope > details > summary .step-right');
  assert.match(await right('a').textContent(), /依赖就绪/);
  assert.match(await right('b').textContent(), /依赖阻塞/);
  await leaf('a').locator(':scope > details > summary').click();
  await leaf('a').locator(':scope > details > .execution-review > summary').click();
  assert(!/外部前置|仍须现场判断|Historical manual record/.test(await frame.locator('body').textContent()));
  const started = await host.rpc('update_task', {...binding, task_id:'a', execution_status:'running', assignment});
  await host.rpc('update_task', {...binding, task_id:'a', execution_status:'finished', outcome:'resolved',
    summary:'Dependency completed', attempt_id:started.structuredContent.attempt_id});
  await frame.locator('#refresh').click();
  await frame.locator('#refresh:not([disabled])').waitFor();
  assert.match(await right('b').textContent(), /依赖就绪/);
  await page.close();

  const exported = await host.exportReport(await host.rawRpc('get_progress_view', binding));
  const reportText = await exported.report.locator('body').textContent();
  assert(!/外部前置|仍须现场判断/.test(reportText));
  assert(!(await exported.report.locator('#rp-current').textContent()).includes('Historical manual record'));
  assert((await exported.report.locator('#rp-plan-operations').textContent()).includes('Historical manual record'));
  const call = await exported.hostPage.evaluate(() => window.host.calls.findLast(c => c.name === 'save_export_files'));
  const workbook = call.result.structuredContent.files.find(f => f.name.endsWith('.xlsx')).path;
  execFileSync('python3', ['-c', `import sys,zipfile,xml.etree.ElementTree as E
ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
with zipfile.ZipFile(sys.argv[1]) as z:
 names=[s.attrib['name'] for s in E.fromstring(z.read('xl/workbook.xml')).find('s:sheets',ns)]
 assert '外部前置人工记录' not in names,names
 sheets={name:[[ ''.join(c.itertext()) for c in r.findall('s:c',ns)] for r in E.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')).findall('.//s:row',ns)] for i,name in enumerate(names,1)}
 rows=sheets['任务清单']
 assert not any('外部前置' in c or c=='现场判断' for c in rows[0]),rows[0]
 current=[dict(zip(rows[0],r)) for r in rows[1:] if dict(zip(rows[0],r))['任务 ID']=='b']
 assert any(r['任务依赖满足（graph_ready）']=='是' for r in current),current
 assert 'Historical manual record' not in str(rows)
 assert 'Historical manual record' in str(sheets['快照与修订'])
`, workbook]);
  await exported.report.close();
  await exported.hostPage.close();
  assert.deepEqual(host.errors, []);
  console.log('Removed prerequisites: legacy sessions, dependency gates, UI and HTML/XLSX exports passed in Chrome.');
}
main().catch(e => {console.error(e); process.exitCode=1;}).finally(async () => host?.close());
