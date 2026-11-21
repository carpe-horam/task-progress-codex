/* Exercise the production task rows with a fixed clock range like a saved session. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');

const owner = {execution_mode:'main_session', self_execution_reason:'Isolated task row layout fixture'};
const tasks = [
  {id:'inventory', title:'盘点真实功能和验证入口'},
  {id:'review', title:'审查真实面板与宿主视觉场景，核对长标题换行时的状态和耗时'},
  {id:'group', title:'整理交付与复核记录'},
  {id:'child', title:'核对工作包中的长标题、执行证据和最终验收记录', parent_id:'group'}
];
const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

async function fixture(host) {
  const created = await host.rawRpc('init_task_network', {task_title:'任务进度与交付验收',
    tasks:tasks.map(task => ({...task, assignment:owner}))});
  const {session_id, task_instance_id} = created.structuredContent;
  const binding = {session_id, task_instance_id};
  for (const task_id of ['inventory','review','child']) {
    const start = await host.rawRpc('update_task', {...binding, task_id, execution_status:'running', assignment:owner});
    await host.rawRpc('update_task', {...binding, task_id, attempt_id:start.structuredContent.attempt_id,
      execution_status:'finished', outcome:'resolved', summary:'核对完成。', evidence:['fixture://row/'+task_id]});
  }
  await host.rawRpc('update_task', {...binding, task_id:'group', assignment:owner,
    execution_status:'finished', outcome:'resolved', summary:'已汇总工作包验收记录。', evidence:['fixture://row/group']});
  const snapshot = await host.rawRpc('get_progress_view', binding);
  // Only the time boundaries are synthetic. The accepted status and tree come from the backend.
  for (const [i,task] of tasks.entries()) {
    const node = snapshot.structuredContent.progress.nodes.find(node => node.id === task.id);
    node.started_at = '2026-11-19T09:00:00Z';
    node.finished_at = '2026-11-19T09:12:0'+(3+i)+'Z';
    delete node.time_boundary_sources;
  }
  return snapshot;
}

async function checkRow(frame, id, width) {
  const row = id==='group' ? frame.locator('#step-list .plan-branch[data-step-id="group"] > .plan-branch-head') : frame.locator('#step-list .plan-row[data-step-id="'+id+'"]');
  await row.waitFor({state:'visible'});
  const facts = await row.evaluate(element => {
    const box = e => e.getBoundingClientRect();
    const textBox = element => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return range.getBoundingClientRect();
    };
    const title = element.querySelector('.plan-subject, .branch-subject');
    const side = element.querySelector('.plan-status, .step-right');
    const elapsed = side.querySelector('.step-elapsed');
    const label = Array.from(side.querySelectorAll('span')).find(e => e.textContent==='已验收');
    const labelRect = textBox(label), elapsedRect = textBox(elapsed), titleText = textBox(title.querySelector('.plan-title, .branch-title'));
    return {status:label.textContent, elapsed:elapsed.textContent, statusRight:labelRect.right, timeRight:elapsedRect.right,
      timeTop:elapsedRect.top, statusBottom:labelRect.bottom, titleLines:Math.round(titleText.height/parseFloat(getComputedStyle(title).lineHeight)),
      titleRight:box(title).right, sideLeft:box(side).left, rowRight:box(element).right - parseFloat(getComputedStyle(element).paddingRight), pageWidth:document.documentElement.scrollWidth};
  });
  assert(facts.rowRight>0 && facts.timeRight>0 && facts.titleLines>0,'visible text boxes for '+id);
  assert.match(facts.elapsed,/耗时 12分[3-6]秒/);
  assert(Math.abs(facts.statusRight - facts.timeRight) <= 1, id+' status and elapsed must share a right edge');
  assert(Math.abs(facts.rowRight - facts.timeRight) <= 1, id+' timing ends at the row edge');
  assert(facts.timeTop >= facts.statusBottom - 1, id+' time follows the status');
  assert(facts.titleRight <= facts.sideLeft && facts.pageWidth === width, id+' title and status fit without overlap');
  return facts;
}

(async () => {
  const source = fs.readFileSync(path.resolve(__dirname,'../plugins/task-progress/app.html'),'utf8');
  const host = await createHost();
  try {
    const version = JSON.parse(fs.readFileSync(path.resolve(__dirname,'../plugins/task-progress/.codex-plugin/plugin.json'),'utf8')).version;
    assert.equal(hash(host.html),hash(source.replaceAll('__TASK_PROGRESS_VERSION__',version)));
    const snapshot = await fixture(host);
    if (artifacts) fs.mkdirSync(artifacts,{recursive:true});
    const checks = [];
    for (const width of [320,390,1000]) for (const theme of ['light','dark']) {
      const page = await host.page();
      await page.setViewportSize({width, height:640});
      await page.emulateMedia({colorScheme:theme});
      await page.addStyleTag({content:'html,body{margin:0}iframe{width:100%!important;height:100vh!important;border:0!important}'});
      await page.evaluate(snapshot => {
        const rpc = window.hostRpc;
        window.hostRpc = (name,args) => name === 'get_progress_view' ? Promise.resolve(snapshot) : rpc(name,args);
      }, snapshot);
      const frame = await host.mount(page, 'rows', snapshot);
      await frame.locator('html[data-theme="'+theme+'"]').waitFor();
      assert.equal(await frame.locator('html').evaluate(() => innerWidth), width);
      const details = frame.locator('#steps [data-focus-node="group"] > details');
      const child = frame.locator('#step-list .plan-row[data-step-id="child"]');
      const groupLink=frame.locator('#step-list .plan-branch[data-step-id="group"] > .plan-branch-head .branch-title');
      await frame.locator('#step-list').evaluate(el => el.scrollIntoView({block:'center'}));
      const rows = {};
      for (const id of ['inventory','review','child']) rows[id] = await checkRow(frame,id,width);
      if (width<=390) assert(rows.review.titleLines>=2,'long title wraps on phone '+JSON.stringify({width,row:rows.review}));
      const check = {width, theme, rows};
      if (artifacts) {
        check.screenshot='rows-'+width+'-'+theme+'.png';
        const bytes=await page.screenshot({path:path.join(artifacts,check.screenshot)});
        check.screenshotSha256=hash(bytes);
      }
      await groupLink.click();
      assert.equal(await details.evaluate(el=>el.open),true);
      rows.group = await checkRow(frame,'group',width);
      await frame.locator('#step-detail-back').click();
      assert(await groupLink.evaluate(el=>el===document.activeElement));
      checks.push(check);
      await page.close();
    }
    assert.deepEqual(host.errors,[]);
    if (artifacts) fs.writeFileSync(path.join(artifacts,'measurements.json'),JSON.stringify({
      htmlSha256:hash(host.html), sharedSha256:hash(source),
      source:'Isolated real backend with fixed display timestamps, production HTML in iframe', checks
    },null,2)+'\n');
    console.log('Task-row alignment passed for 24 parent and leaf rows in 320, 390, 1000 px light and dark viewports, including expanding and collapsing the group.');
  } finally { await host.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
