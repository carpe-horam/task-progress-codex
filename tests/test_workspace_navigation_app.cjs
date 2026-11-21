/* Production workspace navigation backed by isolated, real task records. */
'use strict';
const assert = require('node:assert/strict');
const {createHost} = require('./browser_host.cjs');
const assignment = {execution_mode:'main_session',self_execution_reason:'Isolated workspace UX acceptance'};
const task = (id,title,extra={}) => ({id,title,assignment,...extra});

(async () => {
  const host = await createHost();
  try {
    const initial = await host.rpc('init_task_network',{task_title:'经营复盘工作区',tasks:[
      task('source','准备底稿'), task('review','核对指标',{depends_on:['source']})]});
    const binding = {session_id:initial.structuredContent.session_id,task_instance_id:initial.structuredContent.task_instance_id};
    const page = await host.page();
    const frame = await host.mount(page,'main',await host.rawRpc('get_progress_view',binding),'fullscreen');
    const tab = id => frame.locator('#workspace-tab-'+id);
    assert.equal(await frame.locator('.workspace-tabs [role=tab]').count(),3);
    assert.equal(await tab('plan').getAttribute('aria-selected'),'true');
    assert.equal(await frame.locator('#task-info, #progress-info, #root-review, #network-meta, #acceptance-status, #progress-context').count(),0);
    assert(await frame.locator('#leaf-progress').isVisible());
    assert.equal(await frame.locator('#percent').innerText(),'0');
    assert.equal(await frame.locator('#progressbar').getAttribute('aria-valuenow'),'0');
    assert.match(await frame.locator('#fraction').innerText(),/0 \/ 2/);

    await tab('revisions').click();
    assert.equal(await frame.locator('#package-overview').isVisible(),false,'history gives the viewport to its own content');
    assert.equal(await frame.locator('#leaf-progress').isVisible(),false);
    assert(await frame.locator('#plan-history-empty').isVisible());
    assert.equal(await frame.locator('#steps-section').isVisible(),false);
    await tab('archives').click();
    assert.equal(await frame.locator('#package-overview').isVisible(),false);
    assert(await frame.locator('#task-history-empty').isVisible());
    await tab('archives').press('Home');
    assert.equal(await tab('plan').getAttribute('aria-selected'),'true');
    assert(await frame.locator('#package-overview').isVisible(),'returning restores the working package overview');
    assert(await tab('plan').evaluate(e=>e===document.activeElement));

    const blocked = frame.locator('#status-chips [data-status-filter="tone:blocked"]');
    assert.equal(await blocked.textContent(),'受阻 1');
    await blocked.click();
    assert.equal(await blocked.getAttribute('aria-pressed'),'true');
    assert.equal(await frame.locator('#step-list .plan-row').count(),1);
    assert(await frame.locator('#step-list [data-step-id="review"]').isVisible());
    assert.match(await frame.locator('#step-list [data-step-id="review"] .plan-wait-reason').innerText(),/等待前置任务：准备底稿/,'blocked task names its real prerequisite in the tree');
    await blocked.click();
    assert.equal(await frame.locator('#status-chips [data-status-filter="all"]').getAttribute('aria-pressed'),'true');
    assert.equal(await frame.locator('#step-list .plan-row').count(),2);

    await host.rawRpc('snapshot_task_network',{...binding,snapshot_id:'before-change',reason:'保留核对口径调整前的计划'});
    const current = await host.rpc('get_progress',binding);
    await host.rawRpc('revise_task_plan',{...binding,task_id:'review',reason:'底稿已取得独立来源，改为独立核对',
      expected_epoch:current.structuredContent.progress.epoch,expected_revision:current.structuredContent.progress.revision,depends_on:[]});
    await frame.locator('#refresh').click();
    await tab('revisions').filter({hasText:'2'}).waitFor();
    const fullReads = () => page.evaluate(()=>window.host.calls.filter(c=>c.name==='get_progress_view'&&c.args.include_history===true).length);
    const beforeReads = await fullReads();
    await tab('revisions').click();
    const revision = frame.locator('#history-list .history-item').filter({hasText:'原位修订'});
    await revision.waitFor();
    assert((await fullReads())>beforeReads,'history tab loads actual full records on demand');
    assert.equal(await frame.locator('#plan-history-empty').isVisible(),false);
    assert.equal(await frame.locator('#steps-section').isVisible(),false);
    await revision.locator('summary').first().click();
    assert.match(await revision.locator('summary').first().textContent(),/原位修订 · 核对指标/);
    assert.deepEqual(await revision.locator('.revision-field').allTextContents(),['前置任务']);
    assert.equal(await revision.locator('.revision-value.before pre').textContent(),'准备底稿');
    assert.equal(await revision.locator('.revision-value.after pre').textContent(),'无');
    await revision.locator('.revision-raw > summary').click();
    assert.deepEqual(JSON.parse(await revision.locator('.revision-raw pre').textContent()).before.depends_on,['source']);
    await frame.locator('#refresh').click();
    assert.equal(await tab('revisions').getAttribute('aria-selected'),'true','refresh preserves the reader view');
    assert(await revision.locator('.revision-raw pre').isVisible(),'expanded raw revision survives refresh');
    await tab('plan').click();
    assert(await frame.locator('#steps-section').isVisible());
    assert(await tab('plan').evaluate(e=>e===document.activeElement),'switching back keeps focus on the selected plan tab');

    const next = await host.rpc('init_task_network',{session_id:binding.session_id,task_title:'下一轮独立复盘',
      atomic_reason:'检查独立归档入口',task_relationship:'independent',new_task_reason:'归档上一任务并验证历史阅览'});
    await page.evaluate(next=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:next},'*'),next);
    await frame.locator('#task-title').filter({hasText:'下一轮独立复盘'}).waitFor();
    await tab('archives').click();
    await frame.locator('#task-history-list .task-history-name').filter({hasText:'经营复盘工作区'}).waitFor();
    assert.equal(await frame.locator('#task-history-empty').isVisible(),false);
    assert.equal(await frame.locator('#plan-history-panel').isVisible(),false);
    await tab('plan').click();
    assert.equal(await tab('plan').getAttribute('aria-selected'),'true');
    assert(await frame.locator('#step-list .plan-title').isVisible(),'atomic tasks retain a visible execution row');
    assert.deepEqual(host.errors,[]);
    console.log('Workspace navigation passed: three real tabs, empty states, keyboard/focus return, lazy snapshot/revision/raw records, retained view on refresh, exact leaf-status chips, visible leaf progress, archive and atomic compatibility.');
  } finally { await host.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
