/* Verify foreground leaf progress independently from overall synthesis. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');
const out = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const owner = {execution_mode:'main_session', self_execution_reason:'Isolated UI verification'};
const failure = '附件引用缺失，需补齐后重新核对。';
const bind = r => ({session_id:r.structuredContent.session_id, task_instance_id:r.structuredContent.task_instance_id});
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

async function scenes(host) {
  const create = async input => bind(await host.rawRpc('init_task_network',input));
  const view = binding => host.rawRpc('get_progress_view',binding);
  const start = (binding,task_id) => host.rawRpc('update_task',{...binding, task_id, execution_status:'running', assignment:owner});
  const finish = (binding,task_id,attempt_id) => host.rawRpc('update_task',{...binding, task_id, attempt_id,
    execution_status:'finished', outcome:'resolved', summary:'所需文件均已核对。', evidence:['fixture://checked/'+task_id]});
  const unplanned = await create({task_title:'准备拆解交付计划'});
  const atomic = await create({task_title:'核对单项交付', atomic_reason:'仅需核对一个交付文件'});
  const attempt = await start(atomic,'root');
  const running = await view(atomic);
  await finish(atomic,'root',attempt.structuredContent.attempt_id);
  const accepted = await view(atomic);
  const group = await create({task_title:'审核整份交付材料', tasks:[
    {id:'files',title:'校验交付文件',assignment:owner},{id:'proof',title:'核对签收证据',assignment:owner}]});
  for (const id of ['files','proof']) {
    const active = await start(group,id);
    await finish(group,id,active.structuredContent.attempt_id);
  }
  const pending = await view(group);
  await host.rawRpc('update_task',{...group, task_id:'root', execution_status:'failed', summary:failure, assignment:owner});
  return [
    {name:'unplanned',snapshot:await view(unplanned),fraction:'暂无叶任务',percent:0},
    {name:'atomic-running',snapshot:running,fraction:'0 / 1 项已验收',percent:0},
    {name:'atomic-accepted',snapshot:accepted,fraction:'1 / 1 项已验收',percent:100},
    {name:'synthesis-pending',snapshot:pending,fraction:'2 / 2 项已验收',percent:100},
    {name:'synthesis-failed',snapshot:await view(group),fraction:'2 / 2 项已验收',percent:100}
  ];
}

(async () => {
  const host=await createHost();
  const shared = fs.readFileSync(path.resolve(__dirname,'../plugins/task-progress/app.html'),'utf8');
  const version = JSON.parse(fs.readFileSync(path.resolve(__dirname,'../plugins/task-progress/.codex-plugin/plugin.json'),'utf8')).version;
  assert.equal(hash(shared.replaceAll('__TASK_PROGRESS_VERSION__',version)),hash(host.html));
  const facts={sharedSha256:hash(shared),renderer:'Real backend transitions, production MCP resource in headless Chrome', htmlSha256:hash(host.html), checks:[]};
  try {
    if(out) fs.mkdirSync(out,{recursive:true});
    for(const scene of await scenes(host)) {
      const progress = scene.snapshot.structuredContent.progress;
      for(const [width,theme] of [[320,'light'],[390,'dark']]) {
        const page = await host.page();
        await page.setViewportSize({width,height:700});
        await page.emulateMedia({colorScheme:theme});
        await page.addStyleTag({content:'html,body{margin:0}iframe{width:100%!important;height:100vh!important;border:0!important}'});
        // Keep the saved running and pending snapshots stable after later real transitions.
        await page.evaluate(snapshot => {
          const rpc = window.hostRpc;
          window.hostRpc = (name,args) => name==='get_progress_view' ? Promise.resolve(snapshot) : rpc(name,args);
        }, scene.snapshot);
        const frame = await host.mount(page,'main',scene.snapshot);
        await frame.locator('html[data-theme="'+theme+'"]').waitFor();
        await frame.locator('#leaf-progress').waitFor();
        const fraction=await frame.locator('#fraction').innerText();
        const percent=Number(await frame.locator('#progressbar').getAttribute('aria-valuenow'));
        const badge=await frame.locator('#task-status-label').innerText();
        const visibleFocus=await frame.locator('.package-focus').count()>0;
        assert.equal(visibleFocus,false,'focus section removed');
        const layout=await frame.locator('html').evaluate(el=>({inner:innerWidth,width:el.scrollWidth}));
        assert.deepEqual(layout,{inner:width,width});
        assert.equal(fraction,scene.fraction,scene.name+' counts');
        assert.equal(percent,scene.percent,scene.name+' percent');
        assert.equal(await frame.locator('#progressbar').getAttribute('aria-valuetext'),fraction);
        assert.equal(await frame.locator('#task-info, #root-review, #progress-context').count(),0);
        if(scene.name==='unplanned') {
          assert.equal(progress.planning_status,'required');
          assert.equal(badge,'待规划');
          assert.equal(await frame.locator('#steps-section').isVisible(),true);
          assert.match(await frame.locator('#step-page-total').innerText(),/全部任务 0/);
          assert.equal(await frame.locator('#fill').evaluate(e=>parseFloat(e.style.width)),0);
          assert.equal(await frame.locator('#package-grid .package-cell:visible').count(),0);
        } else if(scene.name==='synthesis-pending') {
          assert.equal(visibleFocus,false);
          assert.match(badge,/待整体验收/);
          assert.equal(await frame.locator('#package-focus-open').count(),0);
        } else if(scene.name==='synthesis-failed') {
          assert.match(badge,/失败/);
          assert.equal(visibleFocus,false);
          assert.equal(await frame.locator('#package-focus-open').count(),0);
        } else {
          assert(await frame.locator('#step-list .plan-title').isVisible());
          if(scene.name==='atomic-accepted') {
            assert.match(badge,/已验收/);
            assert.equal(visibleFocus,false);
          } else {
            assert.match(badge,/进行中|执行中/);
          }
        }
        const item={scene:scene.name,width,theme,backendTotal:progress.total,backendAccepted:progress.accepted_resolved_leaf_count,
          planningStatus:progress.planning_status,fraction,percent,badge,visibleFocus};
        if(out) {
          item.file=scene.name+'-'+width+'-'+theme+'.png';
          item.sha256=hash(await page.screenshot({path:path.join(out,item.file)}));
        }
        if(scene.name.startsWith('atomic')) {
          const task=frame.locator('#steps > li > details');
          await frame.locator('#step-list .plan-title').click();
          assert(await task.evaluate(e=>e.open));
          if(scene.name==='atomic-accepted') assert.match(await task.innerText(),/所需文件均已核对/);
        }
        facts.checks.push(item);
        await page.close();
      }
    }
    assert.deepEqual(host.errors,[]);
    if(out) fs.writeFileSync(path.join(out,'states.json'),JSON.stringify(facts,null,2)+'\n');
    console.log('Ten root-state views passed: unplanned, atomic running and accepted, grouped pending synthesis and failed synthesis.');
  } finally { await host.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
