/* Current/historical numbering in real Chrome; synthetic isolated snapshots. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {createHost} = require('./browser_host.cjs');
const assignment = {execution_mode:'main_session',self_execution_reason:'Isolated numbering fixture'};
const sub = {execution_mode:'subagent',model:'fixture',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Isolated numbering fixture'};
const task = (id, depends_on=[])=>({id,title:'Task '+id,depends_on,assignment});
const artifacts = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
let host;
async function main() {
  host = await createHost();
  const init = await host.rpc('init_task_network',{task_title:'Numbering fixture',tasks:[task('late',['early']),task('group'),task('early')]});
  const sid=init.structuredContent.session_id, iid=init.structuredContent.progress.task_instance_id;
  const binding={session_id:sid,task_instance_id:iid};
  await host.rpc('decompose_task',{...binding,task_id:'group',tasks:[task('g2',['g1']),task('g1')]});
  await host.rpc('decompose_task',{...binding,task_id:'g1',tasks:[task('deep')]});
  let parallel = await host.rpc('route_request',{...binding,request_id:'peer',request_text:'Independent numbering fixture',task_title:'Numbering peer',mode:'parallel',reason:'Same IDs remain isolated',acceptance_criteria:['Independent numbering']});
  const pid=parallel.structuredContent.session_work.requests.find(r=>r.request_id==='peer').task_instance_id;
  const pb={session_id:sid,task_instance_id:pid};
  await host.rpc('decompose_task',{...pb,task_id:'root',tasks:[{...task('early'),assignment:sub},task('group')]});
  const started=await host.rpc('update_task',{...pb,task_id:'early',execution_status:'running',assignment:sub});
  const attempt=started.structuredContent.progress.nodes.find(n=>n.id==='early').attempt_id;
  await host.rpc('bind_task_agent',{...pb,task_id:'early',attempt_id:attempt,agent_name:'fixture'});
  await host.rpc('update_task',{...pb,task_id:'early',attempt_id:attempt,execution_status:'finished',outcome:'resolved',summary:'Synthetic submission',evidence:['fixture://numbering']});
  let snapshot=await host.rawRpc('get_progress_view',{session_id:sid,include_history:true});
  const page=await host.page();
  await page.evaluate(snapshot=>{
    window.numberingSnapshot=snapshot;
    const real=window.hostRpc;
    window.hostRpc=(name,args)=>name==='get_progress_view'?Promise.resolve(structuredClone(window.numberingSnapshot)):real(name,args);
  },snapshot);
  const frame=await host.mount(page,'main',snapshot);
  const rows=frame.locator('#steps details[data-key]');
  const row=(id,instance=iid)=>frame.locator('#steps details[data-key="task:'+instance+':epoch:0:'+id+'"]');
  const number=async(id,instance=iid)=>row(id,instance).locator(':scope > summary > .step-title > .task-number').textContent();
  const cases=[];
  assert.equal(await number('root'),'1');assert.equal(await number('group'),'1-1');
  assert.equal(await number('g1'),'1-1-1');assert.equal(await number('deep'),'1-1-1-1');assert.equal(await number('g2'),'1-1-2');
  assert.equal(await number('early'),'1-2');assert.equal(await number('late'),'1-3');
  assert.equal(await number('root',pid),'2');assert.equal(await number('early',pid),'2-1');assert.equal(await number('group',pid),'2-2');
  assert.equal(await row('early',pid).locator(':scope > summary .waiting-clock').count(),1);
  assert.equal(await row('early',pid).locator(':scope > summary .waiting-clock').evaluate(e=>getComputedStyle(e).width),'20px');
  cases.push('Multi-level numbering, reverse sibling dependencies, independent peer IDs and waiting SVG');

  await row('g2').locator(':scope > summary').click();
  await row('g2').locator(':scope > summary').focus();
  await row('g2').locator(':scope > summary .task-title-text').evaluate(e=>{
    window.keptTitle=e;window.keptDetails=e.closest('details');
    const r=document.createRange();r.selectNodeContents(e);getSelection().removeAllRanges();getSelection().addRange(r);
  });
  const selected=await row('g2').evaluate(()=>getSelection().toString());
  async function show(next) {
    snapshot=next;
    await page.evaluate(next=>{
      window.numberingSnapshot=next;
      document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:next},'*');
    },next);
    await frame.locator('#task-title').waitFor();
    await page.waitForTimeout(80);
  }
  const revise=()=>{
    const next=structuredClone(snapshot);next.structuredContent.state_revision++;
    next.structuredContent.progress.state_revision=next.structuredContent.state_revision;
    next.structuredContent.view_sequence+=10;return next;
  };
  let next=revise(), nodes=next.structuredContent.progress.nodes;
  nodes.find(n=>n.id==='group').children=['g2','g1'];nodes.find(n=>n.id==='g2').depends_on=[];
  await show(next);
  assert.equal(await number('g2'),'1-1-1');assert.equal(await number('deep'),'1-1-2-1');
  assert.equal(await row('g2').getAttribute('open'),'');
  assert.equal(await row('g2').evaluate(e=>e===window.keptDetails),true);
  assert.equal(await row('g2').locator(':scope > summary .task-title-text').evaluate(e=>e===window.keptTitle),true);
  assert.equal(await row('g2').evaluate(()=>getSelection().toString()),selected);
  assert.equal(await row('g2').evaluate(e=>e.querySelector('summary')===document.activeElement),true);
  assert.equal(await number('early',pid),'2-1');
  cases.push('Changed child plan/dependencies renumber without changing task IDs, DOM, expansion, selection or focus');

  await frame.locator('body').evaluate(()=>{window.savedMoveBefore=Element.prototype.moveBefore;Element.prototype.moveBefore=undefined;});
  next=revise();next.structuredContent.progress.nodes.find(n=>n.id==='g2').depends_on=['g1'];await show(next);
  next=revise();next.structuredContent.progress.nodes.find(n=>n.id==='g2').depends_on=[];await show(next);
  assert.equal(await row('g2').evaluate(()=>getSelection().toString()),selected);
  assert.equal(await row('g2').evaluate(e=>e.querySelector('summary')===document.activeElement),true);
  await frame.locator('body').evaluate(()=>{Element.prototype.moveBefore=window.savedMoveBefore;});
  next=revise();nodes=next.structuredContent.progress.nodes;
  nodes.find(n=>n.id==='g1').children=[];nodes.find(n=>n.id==='deep').parent_id='group';nodes.find(n=>n.id==='group').children.push('deep');
  await show(next);assert.equal(await number('deep'),'1-1-3');
  next=revise();nodes=next.structuredContent.progress.nodes;
  nodes.find(n=>n.id==='g1').children=['deep'];nodes.find(n=>n.id==='deep').parent_id='g1';nodes.find(n=>n.id==='group').children=['g2','g1'];
  await show(next);assert.equal(await number('deep'),'1-1-2-1');
  cases.push('Older-browser move fallback retains focus/selection; hierarchy changes recompute depth');

  // A newly appended prerequisite can move an earlier consumer. These are
  // explicit synthetic incoming snapshots; the backend has no reorder command.
  next=revise();nodes=next.structuredContent.progress.nodes;
  nodes.push({...structuredClone(nodes.find(n=>n.id==='early')),id:'inserted',title:'Task inserted',children:[],depends_on:[],parent_id:'root'});
  nodes.find(n=>n.id==='root').children.push('inserted');
  nodes.find(n=>n.id==='group').depends_on=['inserted'];
  await show(next);
  assert.equal(await number('inserted'),'1-3');assert.equal(await number('group'),'1-4');assert.equal(await number('deep'),'1-4-2-1');
  cases.push('Appended prerequisite and inherited group constraint reorder the whole branch');

  next=revise();nodes=next.structuredContent.progress.nodes;
  nodes.find(n=>n.id==='group').depends_on=[];
  nodes.find(n=>n.id==='g2').depends_on=['early'];
  await show(next);
  assert.equal(await number('early'),'1-1');assert.equal(await number('group'),'1-3');
  cases.push('Cross-branch prerequisite moves the nearest sibling branches');
  next=revise();nodes=next.structuredContent.progress.nodes;
  nodes.find(n=>n.id==='early').depends_on=['g1'];
  // g2 depends early, early depends g1: valid interleaving, projected group/early cycle.
  await show(next);
  assert.equal(await number('group'),'1-1');assert.equal(await number('early'),'1-2');
  cases.push('Interleaving branches retain stable plan order without changing execution state');

  // Actual backend rebuild provides a historical snapshot; old numbering must
  // use that plan, independent from current synthetic ordering.
  await page.close();
  await host.rpc('rebuild_task_network',{...binding,reason:'Isolated numbering replan'});
  let current=await host.rpc('get_progress',{...binding});
  const rootId=current.structuredContent.progress.root_id;
  await host.rpc('decompose_task',{...binding,task_id:rootId,tasks:[task('new-second',['new-first']),task('new-first')]});
  current=await host.rawRpc('get_progress_view',{session_id:sid,include_history:true});
  const historyPage=await host.page(),historyFrame=await host.mount(historyPage,'main',current);
  await historyFrame.locator('#history-section > summary').click();
  await historyFrame.locator('#history-list > li > details > summary').click();
  const historyRows=historyFrame.locator('#history-list .task-title-text');
  assert.equal(await historyRows.count(),7);
  const historical=await historyFrame.locator('#history-list .step-title').evaluateAll(es=>Object.fromEntries(es.map(e=>[e.querySelector('.step-id')?.textContent,e.querySelector('.task-number')?.textContent])));
  assert.equal(historical.group,'1-1');assert.equal(historical.deep,'1-1-1-1');assert.equal(historical.late,'1-3');
  const currentNumbers=await historyFrame.locator('#steps > .step[data-root-role="main"] .step-title').evaluateAll(es=>Object.fromEntries(es.map(e=>[e.querySelector('.step-id')?.textContent,e.querySelector('.task-number')?.textContent])));
  assert.equal(currentNumbers['new-first'],'1-1');assert.equal(currentNumbers['new-second'],'1-2');
  cases.push('Actual replan recomputes current numbering while historical snapshot keeps its own order');
  assert.deepEqual(host.errors,[]);
  if(artifacts){
    fs.mkdirSync(artifacts,{recursive:true});
    await historyFrame.locator('#history-list .step > details').evaluateAll(es=>es.forEach(e=>{e.open=true;}));
    await historyPage.evaluate(()=>{const f=document.getElementById('main');f.style.height=f.contentDocument.documentElement.scrollHeight+'px';});
    await historyPage.screenshot({path:path.join(artifacts,'numbering-history.png'),fullPage:true});
    fs.writeFileSync(path.join(artifacts,'numbering-result.json'),JSON.stringify({host:'Real Chrome, simulated MCP Apps parent and isolated backend; incoming reorder snapshots synthetic',cases,sourceHash:createHash('sha256').update(fs.readFileSync(path.join(__dirname,'../plugins/task-progress/app.html'))).digest('hex')},null,2));
  }
  console.log(JSON.stringify({cases},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>host?.close());
