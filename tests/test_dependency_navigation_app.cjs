/* Real task states; dependency selection is strictly local viewing state. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHost} = require('./browser_host.cjs');
const assignment = {execution_mode:'main_session',self_execution_reason:'Isolated dependency navigation fixture'};
const task = (id, extra={}) => ({id,title:'Task '+id,assignment,...extra});
let host;
async function main() {
  host=await createHost();
  const init=await host.rpc('init_task_network',{task_title:'Dependency navigation',tasks:[
    task('first'),task('group'),task('a',{parent_id:'group'}),task('b',{parent_id:'group'}),task('consumer',{depends_on:['a','b']}),task('serial')]});
  const binding={session_id:init.structuredContent.session_id,task_instance_id:init.structuredContent.progress.task_instance_id};
  const view=()=>host.rawRpc('get_progress_view',{session_id:binding.session_id,include_history:true});
  const page=await host.page({hasTouch:true});
  const frame=await host.mount(page,'main',await view(),'fullscreen');
  if(process.env.TASK_PROGRESS_DEPENDENCY_CINDY) {
    const html=fs.readFileSync(path.join(__dirname,'../plugins/task-progress/cindy/core/app.html'),'utf8').replace('<html lang="zh-CN">','<html lang="zh-CN" data-navigation-adapter="cindy">')
      // srcdoc has no URL query; supply the same source query as the real panel URL.
      .replace("new URLSearchParams(location.search).get('source')", "new URLSearchParams('source=panel').get('source')");
    await page.locator('#main').evaluate((el,html)=>el.srcdoc=html,html);
    await page.waitForFunction(()=>document.getElementById('main').contentDocument.documentElement.dataset.navigationAdapter==='cindy');
    await frame.locator('.dependency-link').first().waitFor();
  }
  const rows=(id,instance=binding.task_instance_id)=>frame.locator('#step-list [data-step-id="'+id+'"][data-instance="'+instance+'"]').locator(':scope.plan-row, :scope > .plan-branch-head');
  const consumer=rows('consumer'),group=rows('group'),a=rows('a'),b=rows('b');
  const buttons=consumer.locator('.dependency-link');
  const settleLayout=()=>frame.locator('body').evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  async function assertEdge(id, label) {
    await settleLayout();
    assert.equal(await frame.locator('.dependency-target').count(),1,label+' one target');
    assert.equal(await frame.locator('.dependency-link[aria-pressed="true"]').count(),1,label+' one pressed button');
    const geometry=await frame.locator('.dependency-wire-flow').evaluate((wire,id)=>{
      const button=document.querySelector('.dependency-link[aria-pressed="true"]');
      const target=document.querySelector('.dependency-target');
      const icon=target.querySelector('.step-icon');
      const b=button.getBoundingClientRect(),t=icon.getBoundingClientRect(),matrix=wire.getScreenCTM();
      const start=wire.getPointAtLength(0).matrixTransform(matrix),end=wire.getPointAtLength(wire.getTotalLength()).matrixTransform(matrix);
      return {start:[start.x,start.y],expectedStart:[b.left+b.width/2,b.bottom],end:[end.x,end.y],expectedEnd:[t.left-3,t.top+t.height/2],cardScroll:document.querySelector('.card').scrollTop,cardHeight:document.querySelector('.card').scrollHeight,svgRect:wire.ownerSVGElement.getBoundingClientRect().toJSON(),viewBox:wire.ownerSVGElement.getAttribute('viewBox'),id:target.dataset.dependencyKey.split(':').pop(),startError:Math.hypot(start.x-(b.left+b.width/2),start.y-b.bottom),endError:Math.hypot(end.x-(t.left-3),end.y-(t.top+t.height/2)),visible:!!wire.getClientRects().length};
    },id);
    assert.equal(geometry.id,id,label+' target identity');
    assert(geometry.visible && geometry.startError<3 && geometry.endError<3,label+' correct line endpoints: '+JSON.stringify(geometry));
  }
  async function waitForRead(after) {
    await page.waitForFunction(after=>window.host.calls.some((c,i)=>i>=after&&c.name==='get_progress_view'&&c.completedAt),after,{timeout:12000});
    await settleLayout();
  }
  async function manualRefresh() {
    const count=await page.evaluate(()=>window.host.calls.length);
    await frame.locator('#refresh').click();await waitForRead(count);
  }
  assert.deepEqual(await buttons.allTextContents(),['2-1','2-2']);
  assert.match(await buttons.first().getAttribute('aria-label'),/Task a/);
  await group.locator('.branch-toggle').click();
  assert.equal(await a.isVisible(),false,'collapsed branch hides target');
  const sourceOpen=await frame.locator('#step-dialog').getAttribute('open');
  const callsBefore=await page.evaluate(()=>window.host.calls.length);
  await buttons.first().click();
  await frame.locator('.dependency-target').waitFor();
  assert.equal(await a.isVisible(),true,'click reveals collapsed target branch');
  assert.equal(await frame.locator('#step-dialog').getAttribute('open'),sourceOpen,'button must not toggle its enclosing summary');
  assert.equal(await a.evaluate(el=>el.classList.contains('dependency-target')),true);
  assert.equal(await frame.locator('.dependency-connections').isVisible(),true);
  assert.match(await frame.locator('.dependency-wire-flow').getAttribute('d'),/^M /);
  assert.equal(await buttons.first().getAttribute('aria-pressed'),'true');
  assert.equal(await page.evaluate(()=>window.host.calls.length),callsBefore,'navigation issues no task mutation/read');
  await assertEdge('a','initial selection');
  await buttons.nth(1).focus();await page.keyboard.press('Enter');
  assert.equal(await b.evaluate(el=>el.classList.contains('dependency-target')),true);
  assert.equal(await frame.locator('.dependency-target').count(),1);
  await assertEdge('b','switch to second dependency');
  await manualRefresh();await assertEdge('b','manual refresh retains second dependency');
  const autoReadStart=await page.evaluate(()=>window.host.calls.length);
  await waitForRead(autoReadStart);await assertEdge('b','actual timer refresh retains second dependency');
  await buttons.nth(1).click();
  assert.equal(await frame.locator('.dependency-connections').isVisible(),false);
  await buttons.first().click();await page.keyboard.press('Escape');
  assert.equal(await frame.locator('.dependency-target').count(),0);
  // A refresh must retain this viewing state, but an edge that no longer exists must disappear.
  await buttons.first().click();
  let delivery=0;
  const push=async snapshot=>{
    await page.evaluate(({snapshot,delivery})=>new Promise(resolve=>{
      const app=document.getElementById('main').contentWindow;
      const received=event=>{if(event.data?.testDelivery!==delivery)return;app.removeEventListener('message',received);app.requestAnimationFrame(()=>app.requestAnimationFrame(()=>resolve()));};
      app.addEventListener('message',received);app.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot,testDelivery:delivery},'*');
    }),{snapshot,delivery:++delivery});
  };
  await push(await view());
  assert.equal(await buttons.first().getAttribute('aria-pressed'),'true');
  assert.equal(await a.evaluate(el=>el.classList.contains('dependency-target')),true);
  await assertEdge('a','notification refresh retains geometry');
  // Filtering a target out should not leave a dangling line.
  await frame.locator('[data-status-filter="tone:blocked"]').click();
  await frame.locator('.dependency-connections').waitFor({state:'hidden'});
  await frame.locator('[data-status-filter="all"]').click();
  await buttons.first().click();
  const desktopPath=await frame.locator('.dependency-wire-flow').getAttribute('d');
  for(const width of [320,360,430]) {
    await page.setViewportSize({width,height:1000});
    await page.locator('#main').evaluate(el=>el.style.width='100%');
    const expectedWidth=await page.locator('#main').evaluate(el=>el.clientWidth);
    await page.waitForFunction(width=>document.getElementById('main').contentWindow.innerWidth===width,expectedWidth);
    await assertEdge('a',width+'px resize preserves geometry');
    assert(await frame.locator('html').evaluate(el=>el.scrollWidth<=innerWidth),width+'px no horizontal overflow');
    await buttons.nth(1).tap();await assertEdge('b',width+'px touch switches dependency');
    await manualRefresh();await assertEdge('b',width+'px refresh preserves touched dependency');
    await buttons.first().tap();await assertEdge('a',width+'px touch switches back');
    await buttons.first().tap();
    assert.equal(await frame.locator('.dependency-connections').isVisible(),false,width+'px repeat tap clears');
    await buttons.first().tap();await assertEdge('a',width+'px touch reopens');
  }
  assert.notEqual(await frame.locator('.dependency-wire-flow').getAttribute('d'),desktopPath,'resize recomputes actual path');
  await page.emulateMedia({reducedMotion:'reduce',colorScheme:'light'});
  assert.equal(await frame.locator('.dependency-wire-flow').evaluate(el=>getComputedStyle(el).animationName),'none');
  assert.equal(await frame.locator('html').getAttribute('data-theme'),'light');
  const artifacts=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
  if(artifacts){fs.mkdirSync(artifacts,{recursive:true});await page.locator('#main').evaluate(el=>el.style.height=el.contentDocument.documentElement.scrollHeight+'px');await page.waitForTimeout(100);await frame.locator('.card').screenshot({path:path.join(artifacts,'dependency-mobile-light.png')});}
  await page.setViewportSize({width:1000,height:1100});
  await page.emulateMedia({reducedMotion:'no-preference',colorScheme:'dark'});
  await assertEdge('a','desktop layout restores after narrow resize');
  if(artifacts){await page.locator('#main').evaluate(el=>el.style.height=el.contentDocument.documentElement.scrollHeight+'px');await page.waitForTimeout(100);await frame.locator('.card').screenshot({path:path.join(artifacts,'dependency-desktop-dark.png')});}
  const beforeTitle=(await view()).structuredContent.progress;
  await host.rpc('revise_task_title',{...binding,task_id:'a',display_title:'Task a · '+ '刷新后的长任务标题与换行定位验证 '.repeat(18),reason:'Exercise wrapped trace text after real backend refresh',expected_epoch:beforeTitle.epoch,expected_revision:beforeTitle.revision});
  await manualRefresh();await assertEdge('a','changed title and wrapped trace after backend refresh');
  const started=await host.rpc('update_task',{...binding,task_id:'a',execution_status:'running',assignment});
  // Main-session serialization is not a graph dependency. Use real running
  // attempts to verify its separate caption, references and line identity.
  await push(await view());
  const serial=rows('serial'), serialButton=serial.locator('.dependency-link');
  const serialState=(await view()).structuredContent.progress.nodes.find(n=>n.id==='serial');
  assert.equal(serialState.graph_ready,true);
  assert.deepEqual(serialState.depends_on,[]);
  assert.deepEqual(serialState.blocked_by,[]);
  assert.deepEqual(serialState.main_session_waiting_on,['a']);
  assert.equal(await serialButton.textContent(),'2-1');
  assert.equal(await serialButton.getAttribute('data-wait-kind'),'main_session');
  assert.match(await serial.locator('.dependency-wait').innerText(),/^等待主会话先完成：/);
  assert.doesNotMatch(await serial.locator('.dependency-wait').innerText(),/Task a/);
  await page.keyboard.press('Escape');
  await group.locator('.branch-toggle').click();
  assert.equal(await a.isVisible(),false);
  const first=rows('first'), firstButton=first.locator('.dependency-link');
  await firstButton.click();await assertEdge('a','serial source before its occupant');
  assert((await first.boundingBox()).y < (await a.boundingBox()).y,'serial line also points downward to a later row');
  await serialButton.click();await assertEdge('a','serial wait reveals running task');
  assert.match(await frame.locator('.dependency-trace').innerText(),/主会话当前任务 2-1/);
  await manualRefresh();await assertEdge('a','serial wait refresh retains selection');
  for(const width of [320,430]) {
    await page.setViewportSize({width,height:1000});
    const expectedWidth=await page.locator('#main').evaluate(el=>el.clientWidth);
    await page.waitForFunction(w=>document.getElementById('main').contentWindow.innerWidth===w,expectedWidth);
    await assertEdge('a',width+'px serial wait geometry');
    await serialButton.tap();assert.equal(await frame.locator('.dependency-connections').isVisible(),false);
    await serialButton.tap();await assertEdge('a',width+'px serial wait touch');
  }
  await page.setViewportSize({width:1000,height:1100});
  await host.rpc('update_task',{...binding,task_id:'a',attempt_id:started.structuredContent.attempt_id,execution_status:'finished',outcome:'resolved',summary:'fixture',evidence:['fixture://a']});
  await push(await view());
  assert.deepEqual(await buttons.allTextContents(),['2-2']);
  assert.equal(await frame.locator('.dependency-target').count(),0,'resolved edge clears selection');
  assert.equal(await frame.locator('.dependency-connections').isVisible(),false);
  assert.equal(await serial.locator('.dependency-wait').count(),0,'serialization releases when the main attempt ends');
  const next=await host.rpc('update_task',{...binding,task_id:'b',execution_status:'running',assignment});
  await push(await view());
  assert.equal(await serialButton.textContent(),'2-2','new running task updates the displayed number');
  await serialButton.click();await assertEdge('b','serial wait follows next main-session attempt');
  assert.match(await frame.locator('.dependency-trace').innerText(),/主会话当前任务 2-2/);
  assert.equal(await buttons.first().getAttribute('data-wait-kind'),'dependency','true graph blocking keeps its separate type');
  await host.rpc('update_task',{...binding,task_id:'b',attempt_id:next.structuredContent.attempt_id,execution_status:'finished',outcome:'open',summary:'Fixture prerequisite remains unresolved',failure_reason:'Isolated test keeps the graph prerequisite open',evidence:['fixture://serial-release']});
  await push(await view());
  assert.equal(await serial.locator('.dependency-wait').count(),0);
  assert.equal(await frame.locator('.dependency-connections').isVisible(),false,'ended occupant clears serial trace without resolving its result');
  // Snapshot and current tree reuse task IDs; references must stay in their own scope.
  await host.rawRpc('snapshot_task_network',{...binding,snapshot_id:'dependency-snapshot',reason:'Isolated history navigation fixture'});
  await push(await view());
  await buttons.first().click();
  await assertEdge('b','snapshot refresh remains bound to current visible task');
  assert.equal(await frame.locator('#history-list .dependency-target').count(),0);
  const routed=await host.rpc('route_request',{...binding,request_id:'dependency-peer',request_text:'Isolated peer navigation',task_title:'Peer navigation',mode:'parallel',reason:'Same task IDs must remain isolated',acceptance_criteria:['No cross-instance edge']});
  const peerId=routed.structuredContent.session_work.requests.find(r=>r.request_id==='dependency-peer').task_instance_id;
  const sub={execution_mode:'subagent'};
  await host.rpc('decompose_task',{session_id:binding.session_id,task_instance_id:peerId,task_id:'root',tasks:[
    task('a',{assignment:sub}),task('group',{depends_on:['a']}),task('consumer',{parent_id:'group',assignment:sub})]});
  await push(await view());
  // Open only the peer group; clicking a descendant's inherited prerequisite
  // must resolve to peer a, despite a matching main-session task ID.
  const peerButton=rows('consumer',peerId).locator('.dependency-link');
  assert.equal(await peerButton.textContent(),'5-1');
  await peerButton.click();
  assert.equal(await rows('a',peerId).evaluate(el=>el.classList.contains('dependency-target')),true);
  assert.equal(await a.evaluate(el=>el.classList.contains('dependency-target')),false);
  await buttons.first().click();
  assert.equal(await b.evaluate(el=>el.classList.contains('dependency-target')),true);
  assert.equal(await rows('a',peerId).evaluate(el=>el.classList.contains('dependency-target')),false);
  // A partial/corrupt snapshot may refer to a node not present in its tree.
  // This synthetic negative control must not invent a number or cross-link history.
  const partial=await view();
  const node=partial.structuredContent.progress.nodes.find(n=>n.id==='consumer');
  node.depends_on=['not-loaded'];node.blocked_by=['not-loaded'];
  const missingOccupant=partial.structuredContent.progress.nodes.find(n=>n.id==='first');
  missingOccupant.main_session_waiting_on=['not-loaded'];missingOccupant.graph_ready=true;missingOccupant.ready=false;
  await push(partial);
  assert.deepEqual(await buttons.allTextContents(),['前置任务未载入']);
  assert.equal(await buttons.first().isDisabled(),true);
  assert.equal(await firstButton.textContent(),'执行任务未载入');
  assert.equal(await firstButton.isDisabled(),true);
  assert.equal(await frame.locator('.dependency-connections').isVisible(),false);
  await push(await view());
  await buttons.first().click();
  await page.evaluate(()=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',id:'navigation-teardown',method:'ui/resource-teardown'},'*'));
  await frame.locator('.dependency-connections').waitFor({state:'hidden'});
  assert.deepEqual(host.errors,[]);
  console.log(JSON.stringify({pass:true,adapter:process.env.TASK_PROGRESS_DEPENDENCY_CINDY?'cindy':'shared',checks:['visible tree numbers','collapsed target reveal','summary does not toggle','multiple dependencies','keyboard and Escape','no RPC navigation','notification/manual/actual timer refresh and changed long title preserve selected edge and geometry','filter clears hidden edge','320/360/430px touch switching, repeat tap, refresh and endpoint geometry','light/dark and reduced motion','resolved edge clears trace','history scope isolation','parallel instance isolation and inherited prerequisites','main-session serial wait, branch reveal, touch, refresh and occupant switch','graph readiness unchanged by serial navigation','missing target disabled','teardown clears trace']}));
}
main().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(host)await host.close()});
