/* Test keyboard, focus and session isolation against real task snapshots in Chrome. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');
const plugin = path.resolve(__dirname,'../plugins/task-progress');
const out = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const hash = bytes=>createHash('sha256').update(bytes).digest('hex');
const assignment = {execution_mode:'main_session',self_execution_reason:'逐项核对交付材料。'};
const facts = [];
const notify = (page,method,params)=>page.evaluate(({method,params})=>{
  document.querySelector('iframe').contentWindow.postMessage({jsonrpc:'2.0',method,params},'*');
},{method,params});
const send = (page,snapshot)=>notify(page,'ui/notifications/tool-result',snapshot);
const active = button=>button.evaluate(e=>document.activeElement===e);
const repaint = frame=>frame.locator('body').evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
const read = (host,id)=>host.rawRpc('get_progress_view',{session_id:id,include_history:false});
async function fixture(host,name) {
  const tasks=[{id:'pkg',title:'交付工作包',assignment}];
  for (let i=1;i<=23;i++) tasks.push({id:'row-'+i,title:name+'清单 第'+i+'份',parent_id:'pkg',assignment});
  const r=await host.rawRpc('init_task_network',{task_title:name,tasks});
  return {session_id:r.structuredContent.session_id,task_instance_id:r.structuredContent.task_instance_id};
}
async function tabCycle(page,dialog) {
  for (const direction of ['Tab','Shift+Tab']) {
    const visited=new Set();
    for(let i=0;i<12;i++) {
      await page.keyboard.press(direction);
      const key=await dialog.evaluate(e=>{
        const n=document.activeElement;
        return e.contains(n) ? [n.tagName,n.id,n.className,n.textContent?.slice(0,60)].join('/') : null;
      });
      assert(key,direction+' escaped '+await dialog.getAttribute('id')+' after '+Array.from(visited).join(' -> '));
      visited.add(key);
    }
    assert(visited.size>1,direction+' reaches multiple dialog controls');
  }
}
async function backdrop(page,dialog){
  const iframe=await page.locator('iframe').boundingBox();
  const box=await dialog.boundingBox();
  const spot={x:iframe.x+2,y:iframe.y+2};
  assert(spot.x<box.x || spot.y<box.y,'test point lies in backdrop');
  await page.mouse.click(spot.x,spot.y);
}
async function interact(host,session,foreign){
  const page=await host.page();
  try {
    await page.setViewportSize({width:390,height:780});
    await page.addStyleTag({content:'html,body{margin:0}iframe{width:100vw!important;height:100vh!important;border:0!important}'});
    const initial=await read(host,session.session_id);
    const frame=await host.mount(page,'main',initial,'fullscreen');
    const search=frame.locator('#step-search-dialog'), opener=frame.locator('#step-search-open');
    const input=frame.locator('#step-search'), detail=frame.locator('#step-dialog');
    const rows=frame.locator('#step-list .plan-row');
    const button=frame.locator('#step-list [data-step-id="row-15"] .plan-title');
    await opener.click();await search.waitFor();
    assert(await active(input));
    await tabCycle(page,search);
    await input.fill('没有这份材料');assert.match(await frame.locator('#search-dialog-result').innerText(),/没有|0 项/);
    await frame.locator('#search-dialog-done').click();
    await frame.locator('#filter-empty').waitFor();
    assert.equal(await frame.locator('#step-page-range').innerText(),'匹配根任务 0');
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 24');
    assert(await active(opener));
    await opener.click();
    assert.equal(await input.inputValue(),'没有这份材料');
    await frame.locator('#search-query-clear').click();
    assert(await active(input));assert.equal(await rows.count(),23);
    await input.fill('清单');await send(page,initial);await repaint(frame);
    assert(await active(input));assert.equal(await input.inputValue(),'清单');
    await page.keyboard.press('Escape');
    assert.equal(await search.isVisible(),false);assert(await active(opener));
    await opener.click();await backdrop(page,search);assert(await active(opener));
    await opener.click();await frame.locator('#search-dialog-close').click();assert(await active(opener));
    await opener.click();await input.press('Enter');assert.equal(await search.isVisible(),false);
    const packageButton=frame.locator('#package-grid [data-package-filter="pkg"]');
    await packageButton.click();
    assert(!(await frame.locator('#step-page-next').isVisible()));
    assert.equal(await frame.locator('#step-page-range').innerText(),'匹配根任务 1–1 / 1');
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 24');
    for(const method of ['button','escape','backdrop']){
      await button.evaluate(e=>e.scrollIntoView({block:'center'}));
      const original=await button.evaluate(()=>window.scrollY);
      assert(original>0);
      await button.click();
      assert(await detail.evaluate(e=>e.matches(':modal')));
      assert(await active(frame.locator('#step-detail-back')));
      assert.match(await detail.innerText(),/1-15/);
      if(method==='button'){
        await tabCycle(page,detail);
        const oldSnapshot=await read(host,session.session_id);
        const p=oldSnapshot.structuredContent.progress;
        await host.rawRpc('revise_task_title',{...session,task_id:'row-15',display_title:'交付清单 第15份 已同步',reason:'验证打开时的服务更新',expected_epoch:p.epoch,expected_revision:p.revision});
        await send(page,await read(host,session.session_id));
        await detail.locator('#detail-name').filter({hasText:'第15份 已同步'}).waitFor();
        await send(page,oldSnapshot);await send(page,await read(host,foreign.session_id));await repaint(frame);
        assert.match(await detail.innerText(),/第15份 已同步/);
        await detail.locator('#step-detail-path .step-detail-crumb').last().click();
        assert.match(await detail.locator('#detail-name').innerText(),/^1 .*工作包/);
        await frame.locator('#step-detail-back').click();
      } else if(method==='escape') await page.keyboard.press('Escape');
      else await backdrop(page,detail);
      await detail.waitFor({state:'hidden'});
      assert(await active(button),method+' restores original title focus');
      assert.equal(await frame.locator('#step-page-range').innerText(),'匹配根任务 1–1 / 1');
    assert.equal(await frame.locator('#step-page-total').innerText(),'全部任务 24');
      assert.equal(await input.inputValue(),'清单');
      assert.equal(await packageButton.getAttribute('aria-pressed'),'true');
      const current=await button.evaluate(()=>window.scrollY);
      assert(Math.abs(current-original)<=1,method+' background scroll '+original+' -> '+current);
    }
    facts.push('The entire 23-step package on one page, package filter, search text, source number 1-15, scroll and trigger focus are preserved on button, Escape and backdrop close. Search and detail trap forward and backward Tab. Live title refresh, older revision and foreign session tested while detail stayed open.');
  } finally {await page.close();}
}
async function rebind(host,stage,a,b){
  const page=await host.page();
  try{
    await page.route('http://dialog.test/**',route=>{
      const file=new URL(route.request().url()).pathname.slice(1);
      return route.fulfill({path:path.join(stage,'core',file),contentType:file.endsWith('.js')?'text/javascript':'text/html'});
    });
    await page.evaluate(()=>{
      window.host.modes.panel='fullscreen';
      window.host.snapshots.panel={};
      const frame=document.createElement('iframe');
      frame.id='panel';frame.style='width:510px;height:680px';
      frame.src='http://dialog.test/panel-app.html?source=panel';document.body.append(frame);
    });
    await page.waitForFunction(()=>host.initializations.length>0);
    const frame=page.frameLocator('#panel');
    const bind=s=>notify(page,'ui/notifications/tool-input',{arguments:{session_id:s.session_id}});
    for(const mode of ['detail','search']){
      await bind(a);
      await frame.locator('#task-title').filter({hasText:'来源甲'}).waitFor();
      const modal=frame.locator(mode==='detail'?'#step-dialog':'#step-search-dialog');
      if(mode==='detail')await frame.locator('#step-list [data-step-id="row-1"] .plan-title').click();
      else{await frame.locator('#step-search-open').click();await frame.locator('#step-search').fill('来源甲清单');}
      await modal.waitFor();
      // The next genuine five-second poll sends A's request while the modal is open.
      await page.evaluate(()=>window.host.holdExecution={frame:'panel',name:'get_progress_view'});
      await page.waitForFunction(()=>window.host.heldExecution.length===1,null,{timeout:12000});
      await bind(b);await modal.waitFor({state:'hidden'});
      await frame.locator('#task-title').filter({hasText:'来源乙'}).waitFor();
      await page.evaluate(()=>window.host.heldExecution.shift()());
      await page.waitForFunction(()=>host.calls.filter(c=>c.heldAt).every(c=>c.completedAt||c.error));
      await send(page,await read(host,a.session_id));await repaint(frame);
      assert.equal(await frame.locator('#task-title').innerText(),'来源乙');
      assert.equal(await modal.isVisible(),false);
      await frame.locator('#step-search-open').click();
      assert.equal(await frame.locator('#step-search').inputValue(),'');
      await frame.locator('#search-dialog-close').click();
      const trigger=frame.locator('#step-list [data-step-id="row-1"] .plan-title');
      await trigger.click();
      assert.match(await frame.locator('#detail-name').innerText(),/来源乙清单/);
      await frame.locator('#step-detail-back').click();
    }
    facts.push('On generated Cindy panel, rebinding with either modal open closes it, clears the old search, and displays the new session; late genuine poll and old host notification cannot reopen or overwrite details.');
  }finally{await page.close();}
}
async function main(){
  const host=await createHost(), tmp=fs.mkdtempSync(path.join(os.tmpdir(),'task-dialog-'));
  try{
    const a=await fixture(host,'来源甲'),b=await fixture(host,'来源乙');
    await interact(host,a,b);
    const stage=path.join(tmp,'stage');
    execFileSync('python3',[path.join(plugin,'cindy/prepare.py'),'--stage',stage]);
    await rebind(host,stage,a,b);
    assert.deepEqual(host.errors,[]);
    const source=hash(fs.readFileSync(path.join(plugin,'app.html')));
    const report={source,served:hash(host.html),panel:hash(fs.readFileSync(path.join(stage,'core/panel-app.js'))),facts};
    if(out){fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'dialog-facts.json'),JSON.stringify(report,null,2)+'\n');}
    console.log('Modal interaction and session isolation passed. '+JSON.stringify(report));
  }finally{await host.close();fs.rmSync(tmp,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
