/* Offline river/table contract through the real packaged app and isolated save handler. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto');
const {createHost}=require('./browser_host.cjs');
const MAIN={execution_mode:'main_session',self_execution_reason:'Short isolated export fixture'};
const SUB={execution_mode:'subagent',model:'fixture',reasoning_effort:'high',tool_identifier:'collaboration.spawn_agent',dispatch_text:'Produce isolated export evidence'};
const hostile='literal </script><script>globalThis.exportInjected=1</script><img src=x onerror="globalThis.exportInjected=2"> & "quoted"';
const clone=value=>JSON.parse(JSON.stringify(value));
const longTitle='核对 architecture/change/codecheck/clean-code/domain 的完整执行证据与长标题任务层级，保留每项验收和原始记录';
const p=result=>result.structuredContent.progress;
const binding=result=>({session_id:result.structuredContent.session_id,task_instance_id:p(result).task_instance_id});
let host;const errors=[],results=[];
async function saved(snapshot){
 const exported=await host.exportReport(snapshot);
 const files=await exported.hostPage.evaluate(()=>window.host.calls.findLast(call=>call.name==='save_export_files').result.structuredContent.files);
 return {...exported,files};
}
async function close(exported){await exported.report.close();await exported.hostPage.close();}
async function timeColumnsFixture(source,firstClosedAt='2026-10-25T08:00:00Z'){
 const value=clone(source),data=p(value),sample=data.nodes.find(n=>n.id==='worker');
 const node=(id,start,end,status='finished')=>({...clone(sample),id,title:'Time case '+id,parent_id:id==='root'?null:'root',children:[],started_at:start,finished_at:end,execution_status:status,outcome:status==='finished'?'resolved':null,summary:'RECORDED_SUMMARY_'+id,depends_on:[],attempts:[],review:null,submission:null,review_status:null});
 data.nodes=[node('root',null,null,'pending'),node('early','2026-10-25T01:00:00Z','2026-10-25T03:00:00Z'),node('late','2026-10-25T05:00:00Z','2026-10-25T07:00:00Z'),node('running','2026-10-25T03:00:00Z',null,'running'),node('end-only',null,'2026-10-25T04:00:00Z'),node('start-only','2026-10-25T02:00:00Z',null),node('reverse','2026-10-25T07:00:00Z','2026-10-25T01:00:00Z'),node('invalid','2026-02-30T01:00:00Z','2026-10-25T01:00:00Z'),node('future-start','2026-10-25T10:00:00Z',null,'running'),node('equal','2026-10-25T04:00:00Z','2026-10-25T04:00:00Z')];
 data.nodes[0].children=data.nodes.slice(1).map(n=>n.id);
 data.updated_at='2026-10-25T09:00:00Z';data.created_at='2026-10-25T00:00:00Z';data.root_id='root';data.epoch=2;data.task_title='Isolated factual time columns';delete data.closed_at;delete data.reason;
 const old=(epoch,time,reason)=>({epoch,root_id:'root',task_title:'Time round '+epoch,nodes:clone(data.nodes),closed_at:time,reason});
 data.history=[old(0,firstClosedAt,'FIRST_REPLAN_REASON'),old(1,null,'SECOND_REPLAN_UNKNOWN_TIME')];
 Object.assign(data.history[0].nodes.find(n=>n.id==='early'),{started_at:'2026-10-25T00:00:00Z',finished_at:'2026-10-25T00:15:00Z'});
 data.nodes.find(n=>n.id==='late').attempts=[{attempt_id:'old-try',kind:'execution',execution_status:'finished',started_at:'2026-10-25T00:30:00Z',finished_at:'2026-10-25T00:45:00Z'}];
 const archive=clone(data);delete archive.task_history;archive.task_instance_id='time-independent-archive';archive.archive_key='independent-time';archive.task_title='Independent same IDs';archive.epoch=0;archive.history=[];archive.archived_at='2026-10-25T09:00:00Z';archive.closed_at='2026-10-25T09:00:00Z';archive.reason='ARCHIVE_CLOSE_IS_NOT_REPLAN';
 data.task_history=[archive];
 for(const snapshot of [data,...data.history,archive])snapshot.nodes=Object.fromEntries(snapshot.nodes.map(n=>[n.id,n]));
 fs.writeFileSync(path.join(host.temp,'sessions',value.structuredContent.session_id+'.json'),JSON.stringify(data));
 return host.rpc('get_progress_view',{...binding(source),include_history:true});
}
async function richFixture(){
 let result=await host.rpc('init_task_network',{task_title:'River formal export '+hostile,tasks:[{id:'phase',title:'Main phase',assignment:MAIN},{id:'peer',title:'Parallel main task',assignment:MAIN},...['failed','blocked','running','rejected','open','cancelled','unknown'].map(id=>({id,title:'Attention '+id,assignment:MAIN,...(id==='blocked'?{depends_on:['failed']}:{})}))]});
 result=await host.rpc('decompose_task',{...binding(result),task_id:'phase',tasks:[{id:'nested',title:'Nested implementation',assignment:MAIN},{id:'sibling',title:'Sibling work',assignment:MAIN}]});
 result=await host.rpc('decompose_task',{...binding(result),task_id:'nested',tasks:[{id:'worker',title:'Worker '+hostile,assignment:SUB},{id:'deep-peer',title:'Deep peer awaiting review',assignment:SUB}]});
 // Seven levels exercise the indent cap instead of only a typical two-level tree.
 let parent='sibling';
 for(let depth=3;depth<=7;depth++){
  const id='depth-'+depth;
  result=await host.rpc('decompose_task',{...binding(result),task_id:parent,tasks:[{id,title:depth===7?longTitle:'Depth '+depth,assignment:MAIN},{id:id+'-peer',title:'Peer at depth '+depth,assignment:MAIN}]});
  parent=id;
 }
 for(const [index,decision] of ['reject','accept'].entries()){
  result=await host.rpc('update_task',{...binding(result),task_id:'worker',execution_status:'running',retry:index>0,assignment:SUB});
  const attempt_id=p(result).nodes.find(node=>node.id==='worker').attempt_id;
  result=await host.rpc('bind_task_agent',{...binding(result),task_id:'worker',attempt_id,agent_name:'river-fixture-agent'});
  result=await host.rpc('update_task',{...binding(result),task_id:'worker',attempt_id,execution_status:'finished',outcome:'resolved',summary:'SUBMISSION_'+index+' '+hostile,evidence:['fixture://submission-'+index]});
  result=await host.rpc('review_task',{...binding(result),task_id:'worker',attempt_id,decision,reviewer:MAIN,summary:'REVIEW_'+decision+' '+hostile,evidence:['fixture://review-'+decision]});
 }
 result=await host.rpc('update_task',{...binding(result),task_id:'deep-peer',execution_status:'running',assignment:SUB});
 const pendingAttempt=p(result).nodes.find(node=>node.id==='deep-peer').attempt_id;
 result=await host.rpc('bind_task_agent',{...binding(result),task_id:'deep-peer',attempt_id:pendingAttempt,agent_name:'river-fixture-pending-agent'});
 result=await host.rpc('update_task',{...binding(result),task_id:'deep-peer',attempt_id:pendingAttempt,execution_status:'finished',outcome:'resolved',summary:'PENDING_REVIEW_SUBMISSION',evidence:['fixture://pending-review']});
 for(const id of ['failed','running','rejected','open','cancelled']){
  const assignment=id==='rejected'?SUB:MAIN;
  if(id==='rejected')await host.rpc('reassign_task',{...binding(result),task_id:id,assignment:SUB,reviewer:MAIN,reason:'Fixture requires an explicitly rejected subagent submission'});
  result=await host.rpc('update_task',{...binding(result),task_id:id,execution_status:'running',assignment});
  const attempt_id=p(result).nodes.find(node=>node.id===id).attempt_id;
  if(id==='running')continue;
  if(id==='rejected')result=await host.rpc('bind_task_agent',{...binding(result),task_id:id,attempt_id,agent_name:'attention-rejected-agent'});
  result=await host.rpc('update_task',{...binding(result),task_id:id,attempt_id,execution_status:['failed','cancelled'].includes(id)?id:'finished',...(['open','rejected'].includes(id)?{outcome:id==='open'?'open':'resolved'}:{}),summary:'ATTENTION_'+id,evidence:['fixture://attention-'+id]});
  if(id==='rejected')result=await host.rpc('review_task',{...binding(result),task_id:id,attempt_id,decision:'reject',reviewer:MAIN,summary:'Needs correction',evidence:['fixture://rejection']});
 }
 // Historical plans intentionally repeat IDs and epochs across independently frozen archives.
 const file=path.join(host.temp,'sessions',result.structuredContent.session_id+'.json');
 const data=JSON.parse(fs.readFileSync(file,'utf8'));
 // Explicit legacy fixture: resolved without a recorded acceptance must stay neutral.
 Object.assign(data.nodes.unknown,{execution_status:'finished',outcome:'resolved',responsibility_version:0,review_status:'legacy_unrecorded',responsibility_legacy:true});
 // Known partial timestamps remain facts: failed/cancelled do not imply completion.
 Object.assign(data.nodes.worker,{started_at:'2026-10-25T01:02:03Z',finished_at:'2026-10-26T04:05:06Z'});
 Object.assign(data.nodes.failed,{started_at:null,finished_at:'2026-10-26T01:04:05Z'});
 Object.assign(data.nodes.running,{started_at:'2026-10-26T02:03:04Z',finished_at:null});
 Object.assign(data.nodes.cancelled,{started_at:null,finished_at:null});
 const round=(epoch,reason,closed)=>{
  const value=clone(data);delete value.history;delete value.task_history;
  Object.assign(value,{epoch,reason,closed_at:closed,task_title:'PLAN_'+epoch,updated_at:'2026-10-20T01:00:00Z'});
  return value;
 };
 data.epoch=2;data.history=[round(0,'OLD_ZERO_REASON '+hostile,'2026-10-20T02:03:04Z'),round(1,'OLD_ONE_REASON','2026-10-21T03:04:05Z')];
 data.task_history=['archive-one','archive-two'].map((archive_key,index)=>{
  const value=round(1,'ARCHIVED_REASON_'+index,'2026-10-22T04:05:06Z');
  Object.assign(value,{task_instance_id:'repeated-archive-instance',archive_key,task_title:'ARCHIVE_'+index,archived_at:'2026-10-23T04:05:06Z',history:[round(0,'ARCHIVE_OLD_'+index,'2026-10-19T01:02:03Z')]});
  return value;
 });
 fs.writeFileSync(file,JSON.stringify(data));
 return host.rpc('get_progress_view',{...binding(result),include_history:true});
}
async function main(){
 const app=path.join(__dirname,'../plugins/task-progress/app.html'),source=fs.readFileSync(app),digest=createHash('sha256').update(source).digest('hex');
 host=await createHost();
 host.browser.on('page',page=>{page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});});
 const fixture=await richFixture(),exported=await saved(fixture),report=exported.report;
 fs.copyFileSync(exported.files.find(file=>file.name.endsWith('.html')).path,'/tmp/task-progress-export-preview.html');
 fs.writeFileSync('/tmp/task-progress-export-preview-snapshot.json',JSON.stringify(fixture,null,2));
 await report.emulateMedia({colorScheme:'dark'});await report.reload();
 const theme=report.locator('#rp-theme');
 assert.deepEqual(await theme.locator('option').allTextContents(),['瓷白蓝','米白绿','冰川青','奶油金','雾紫白','杏桃白']);
 assert.equal(await theme.inputValue(),'porcelain-blue','first open defaults to porcelain blue even with a dark OS theme');
 assert.equal(await report.evaluate(()=>getComputedStyle(document.body).backgroundColor),'rgb(250, 251, 255)');
 const expectedTones={failed:'danger',rejected:'danger',blocked:'attention',open:'attention',running:'running','deep-peer':'attention',worker:'success',cancelled:'neutral',unknown:'neutral'};
 for(const [id,tone] of Object.entries(expectedTones))assert.equal(await report.locator('#rp-current [data-node-id="'+id+'"] > .r-task-meta .r-state').getAttribute('data-tone'),tone,id+' semantic tone');
 assert.equal(await report.locator('#rp-plans .rp-round:not(#rp-current) .r-state:not([data-tone="neutral"]),#rp-archives .r-state:not([data-tone="neutral"])').count(),0,'history does not look like a current alert');
 assert((await report.locator('#rp-current [data-node-id="unknown"] > .r-task-meta').textContent()).includes('验收未记录'));
 assert.deepEqual(await report.locator('.rp-attention .rp-concern').allTextContents(),['失败 / 退回 2','待验收 1','依赖阻塞 1','仍未解决 1'],'current leaf attention is counted once, excluding old rounds and archives');
 assert.deepEqual(await report.locator('#rp-current [data-node-id="root"] > .r-child-bottom .rp-concern').allTextContents(),['失败 / 退回 2','待验收 1','依赖阻塞 1','仍未解决 1'],'collapsed parent exposes descendant concerns');
 const semanticColors=await report.locator('.rp-legend > span').evaluateAll(items=>items.map(item=>getComputedStyle(item).color));
 assert.equal(new Set(semanticColors).size,6,'legend has six distinct semantic colors');
 const backgrounds=new Set();
 for(const id of await theme.locator('option').evaluateAll(options=>options.map(option=>option.value))){
  await theme.selectOption(id);
  const colors=await report.evaluate(()=>({page:getComputedStyle(document.body).backgroundColor,report:getComputedStyle(document.getElementById('task-progress-report')).backgroundColor,scheme:getComputedStyle(document.documentElement).colorScheme}));
  assert.deepEqual(await report.locator('.rp-legend > span').evaluateAll(items=>items.map(item=>getComputedStyle(item).color)),semanticColors,'semantic meaning stays stable across themes');
  assert.equal(colors.page,colors.report);assert.equal(colors.scheme,'light');backgrounds.add(colors.page);
 }
 assert.equal(backgrounds.size,6,'all six choices apply their own background');
 await report.reload();assert.equal(await theme.inputValue(),'apricot-white','the report remembers the chosen theme');
 await report.evaluate(()=>localStorage.setItem(document.getElementById('task-progress-report').dataset.themeStorageKey,'__proto__'));
 await report.reload();assert.equal(await theme.inputValue(),'porcelain-blue','unknown stored themes safely fall back to the default');
 const blockedStorage=await host.browser.newPage();
 await blockedStorage.addInitScript(()=>Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Storage unavailable','SecurityError');}}));
 await blockedStorage.goto(report.url());await blockedStorage.locator('#rp-theme').selectOption('ivory-green');
 assert.equal(await blockedStorage.locator('#task-progress-report').getAttribute('data-theme'),'ivory-green');
 await blockedStorage.locator('#rp-tab-table').click();assert(await blockedStorage.locator('#rp-panel-table').isVisible());await blockedStorage.close();
 assert.equal(await report.evaluate(()=>globalThis.exportInjected),undefined,'hostile strings must remain literal text');
 assert.equal(await report.locator('script').count(),1,'script-close text cannot create executable tags');
 assert.equal(await report.locator('img').count(),0);
 assert((await report.locator('body').textContent()).includes(hostile),'literal hostile content is preserved');
 assert.equal(await report.locator('#rp-tab-timeline').getAttribute('aria-selected'),'true');
 assert.equal(await report.locator('#rp-panel-table').isVisible(),false);
 assert.equal(await report.locator('details[open]').count(),0,'all native detail records default closed');
 assert.equal(await report.locator('[data-group][aria-expanded="true"],[data-task][aria-expanded="true"],[data-turn][aria-expanded="true"]').count(),0,'all child groups and details default closed');
 assert.equal(await report.locator('#rp-plans .r-replan').count(),2);
 assert.equal(await report.locator('#rp-plans .rp-round').count(),3);
 assert.equal(await report.locator('#rp-plans .r-replan:visible').count(),2,'replans remain on the default main path');
 assert.equal(await report.locator('#rp-plans .rp-round:visible').count(),3,'main plans remain on the default main path');
 const transitions=await report.locator('.r-replan').evaluateAll(items=>items.map(item=>({from:item.dataset.fromRound,to:item.dataset.toRound,scope:item.dataset.taskScope,text:item.textContent})));
 assert.equal(transitions.length,4,'current and archived instances retain their own transitions');
 for(const event of transitions){
  const prefix=event.from.slice(0,event.from.lastIndexOf('/round:'));
  assert.equal(event.to.slice(0,event.to.lastIndexOf('/round:')),prefix,'replan cannot cross instance/archive boundaries');
  assert.equal(event.scope,prefix);
 }
 const rootCounts=report.locator('#rp-current [data-node-id="root"] > .r-child-bottom [data-group]');
 assert((await rootCounts.textContent()).includes('直接子任务 9 项'));
 const rootLeafCount=p(fixture).nodes.filter(node=>!node.children?.length).length;
 assert((await rootCounts.textContent()).includes('底层任务 '+rootLeafCount+' 项'),'direct children and bottom-level tasks have distinct labels');
 assert.equal(await report.locator('#rp-current [data-node-id="worker"]').getAttribute('data-hierarchy'),'1.1.1.1');
 const failedTime=report.locator('#rp-current [data-node-id="failed"] > .r-task-meta .r-task-time');
 assert((await failedTime.textContent()).includes('历史未记录 · 结束于 '),'end-only timestamps do not invent a start or completion');
 assert((await failedTime.getAttribute('title')).includes('2026-10-26T01:04:05Z'),'tooltip keeps original timestamp and seconds');
 assert((await report.locator('#rp-current [data-node-id="running"] > .r-task-meta .r-task-time').textContent()).startsWith('开始于 '));
 assert.equal(await report.locator('#rp-current [data-node-id="cancelled"] > .r-task-meta .r-task-time').textContent(),'历史未记录 / 历史未记录');
 const workerTime=await report.locator('#rp-current [data-node-id="worker"] > .r-task-meta .r-task-time').textContent();
 assert(workerTime.includes('10/25')&&workerTime.includes('10/26'),'cross-day range keeps both dates');
 assert(!workerTime.includes('未记录')&&!workerTime.includes('2026'),'known range remains compact');
 const pendingGroup=report.locator('#rp-current [data-node-id="nested"] > .r-child-bottom [data-group]');
 assert((await pendingGroup.textContent()).includes('已解决 1/2'),'pending subagent submission does not count as solved in parent group');
 const first=report.locator('#rp-plans .r-replan').first();
 const gapSecond=report.locator('#rp-plans .r-replan').nth(1).locator('.r-event-gap');
 assert((await gapSecond.textContent()).includes('1天 1小时 1分 1秒'),'interval uses consecutive recorded replan boundaries');
 assert((await first.locator('.r-event-gap').textContent()).includes('间隔未知'),'reversed timestamps do not create a duration');
 const firstText=await first.textContent();assert(firstText.includes('OLD_ZERO_REASON')&&!firstText.includes('OLD_ONE_REASON'),'OLD reason belongs to OLD→NEXT');
 const expectedClosed=await report.evaluate(()=>new Date('2026-10-20T02:03:04Z').toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}));
 assert(firstText.includes(expectedClosed),'replan uses OLD closed_at, independently from updated_at');
 const ids=await report.locator('[id]').evaluateAll(items=>items.map(item=>item.id));
 assert.equal(new Set(ids).size,ids.length,'repeated task IDs/epochs/instances cannot collide in disclosure IDs');
 const group=report.locator('#rp-plans [data-group]').first();
 await group.focus();await report.keyboard.press('Enter');
 assert.equal(await group.getAttribute('aria-expanded'),'true');
 const groupBox=report.locator('[id="'+await group.getAttribute('aria-controls')+'"]');await groupBox.waitFor({state:'visible'});
 const nested=groupBox.locator('[data-group]').first();await nested.click();assert.equal(await nested.getAttribute('aria-expanded'),'true');
 await group.click();assert.equal(await group.getAttribute('aria-expanded'),'false');await groupBox.waitFor({state:'hidden'});
 await report.locator('[data-action="expand-all"]').click();
 assert.equal(await report.locator('#rp-panel-timeline [data-group][aria-expanded="false"]').count(),0,'bulk expands nested branches');
 await report.waitForFunction(()=>document.querySelector('.r-branch-path')?.getAttribute('d'));
 assert((await report.locator('.r-main-path').getAttribute('d')).includes('C'),'main path bends through successive plans');
 await report.locator('[data-action="collapse-all"]').click();
 assert.equal(await report.locator('#rp-panel-timeline [data-group][aria-expanded="true"]').count(),0);
 assert.equal(await report.locator('#rp-plans .rp-round:visible').count(),3);
 const turn=first.locator('[data-turn]');await turn.focus();await report.keyboard.press('Space');assert.equal(await turn.getAttribute('aria-expanded'),'true');
 await report.locator('#rp-tab-table').focus();await report.keyboard.press('Enter');
 assert.equal(await report.locator('#rp-panel-table').isVisible(),true);assert.equal(await report.locator('#rp-panel-timeline').isVisible(),false);
 await theme.selectOption('glacier-teal');
 assert.equal(await report.locator('#rp-panel-table').isVisible(),true,'theme changes preserve the selected view');
 assert.equal(await turn.getAttribute('aria-expanded'),'true','theme changes preserve disclosure state');
 const tableText=await report.locator('#rp-panel-table').textContent();
 for(const marker of ['SUBMISSION_0','SUBMISSION_1','REVIEW_reject','REVIEW_accept','fixture://review-reject','fixture://review-accept','ARCHIVE_0','ARCHIVE_1'])assert(tableText.includes(marker),'table loses original record '+marker);
 const tableTones=await report.locator('.rp-task-table .r-state').evaluateAll(items=>items.map(item=>item.dataset.tone));
 for(const [tone,count] of Object.entries({danger:2,attention:3,success:1}))assert(tableTones.filter(value=>value===tone).length>=count,'table retains current '+tone+' badges');
 const taskRows=await report.locator('.rp-task-table tbody tr').count();
 const expectedTasks=p(fixture).nodes.length*7;assert.equal(taskRows,expectedTasks,'all seven current/archive plan snapshots retain their task records');
 const tableHeaders=await report.locator('.rp-task-table thead th').allTextContents();assert.match(tableHeaders[4],/^执行时间/);tableHeaders[4]='执行时间';assert.deepEqual(tableHeaders,['任务 / 层级','计划轮次','状态','执行者','执行时间','重新规划','总结','详情'],'ledger separates factual time, round replanning and recorded summary');
 assert.deepEqual(await report.locator('.rp-marker-root').allTextContents(),['1','2','3','4','5','6','7'],'main task circles have distinct sequential ordinals across snapshots');
 assert.equal(await report.locator('.rp-task-table progress,.rp-task-table img').count(),0,'no inferred percentages or invented avatars');
 const currentRootKey=await report.locator('#rp-current [data-node-id="root"]').getAttribute('data-node-key');
 const rootToggle=report.locator('[data-table-toggle="'+currentRootKey+'"]');
 assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),7,'each snapshot defaults to its main task only');
 await rootToggle.click();assert.equal(await rootToggle.getAttribute('aria-expanded'),'true');
 assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),16,'opening one root shows its direct children only');
 const phaseKey=await report.locator('#rp-current [data-node-id="phase"]').getAttribute('data-node-key');
 const phaseToggle=report.locator('[data-table-toggle="'+phaseKey+'"]');await phaseToggle.click();
 assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),18,'nested group expands independently');
 await report.waitForFunction(()=>document.querySelectorAll('.rp-table-connectors path').length===11);
 const ledgerGeometry=await report.evaluate(()=>{
  const canvas=document.querySelector('.rp-task-table').parentElement,rect=canvas.getBoundingClientRect(),byKey=new Map([...document.querySelectorAll('[data-table-key]')].map(row=>[row.dataset.tableKey,row]));
  return [...document.querySelectorAll('.rp-table-connectors path')].map(path=>{
   const parent=byKey.get(path.dataset.tableFrom),child=byKey.get(path.dataset.tableTo),a=parent.querySelector('.rp-table-marker').getBoundingClientRect(),b=child.querySelector('.rp-table-marker').getBoundingClientRect(),start=path.getPointAtLength(0),end=path.getPointAtLength(path.getTotalLength());
   return {parentVisible:!parent.hidden,childVisible:!child.hidden,belongs:child.dataset.tableParent===parent.dataset.tableKey,startError:Math.abs(start.x-(a.left+a.width/2-rect.left))+Math.abs(start.y-(a.bottom-rect.top)),endError:Math.abs(end.x-(b.left-rect.left-5))+Math.abs(end.y-(b.top+b.height/2-rect.top)),strokeWidth:getComputedStyle(path).strokeWidth};
  });
 });
 assert(ledgerGeometry.every(edge=>edge.parentVisible&&edge.childVisible&&edge.belongs),'only actual visible parent-child relationships receive rails');
 assert(ledgerGeometry.every(edge=>edge.startError<1&&edge.endError<1),'continuous rails attach to real circle positions despite wrapped row heights');
 assert(ledgerGeometry.every(edge=>edge.strokeWidth==='1.1px'),'ancestry uses fine rails');
 const inline=report.locator('[data-table-key="'+phaseKey+'"] .rp-table-name');
 assert.equal(await inline.locator('.rp-table-number').count(),1,'hierarchy number is inline with the task name');
 assert((await inline.locator('.rp-table-counts').textContent()).includes('底层已解决'),'group counts distinguish direct children from solved leaves');
 assert.equal(await phaseToggle.locator('span').textContent(),'›','disclosure uses a directional chevron');
 await report.setViewportSize({width:1500,height:1000});
 const tableWidth=await report.locator('.rp-task-table').evaluate(table=>table.getBoundingClientRect().width);
 const rowDetail=report.locator('[data-table-key="'+phaseKey+'"] .rp-table-detail');await rowDetail.locator(':scope > summary').click();
 assert(await rowDetail.locator('.rp-table-detail-body').isVisible(),'full original detail remains available from the lightweight chevron');
 assert.equal(await report.locator('.rp-task-table').evaluate(table=>table.getBoundingClientRect().width),tableWidth,'opening detailed audit content does not stretch the field columns');
 await rowDetail.locator(':scope > summary').click();

 await report.locator('#rp-search').fill('SUBMISSION_0');await report.locator('#rp-search').fill('');
 assert.equal(await rootToggle.getAttribute('aria-expanded'),'true','search restores root disclosure');
 assert.equal(await phaseToggle.getAttribute('aria-expanded'),'true','search restores nested disclosure');
 assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),18);
 await rootToggle.click();assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),7,'collapsing hides descendants');await report.waitForFunction(()=>document.querySelectorAll('.rp-table-connectors path').length===0);
 await report.locator('[data-action="expand-all"]').click();assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),expectedTasks,'table expand all shows every task');
 await report.locator('[data-action="collapse-all"]').click();assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),7,'table collapse all restores roots');
 await report.locator('#rp-search').fill('SUBMISSION_0');
 assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),28,'search shows matching tasks and their ancestors in each plan');
 await report.locator('#rp-search').fill('NO_SUCH_EXPORT_RECORD');
 assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),0);assert(await report.locator('#rp-search-empty').isVisible());
 await report.locator('#rp-search').fill('');assert.equal(await report.locator('.rp-task-table tbody tr:not([hidden])').count(),7,'clearing search restores collapsed tree');
 await report.locator('#rp-tab-timeline').click();assert.equal(await turn.getAttribute('aria-expanded'),'true','view switching preserves disclosure state');
 for(const width of [320,760,1280]){
  await report.setViewportSize({width,height:900});await report.emulateMedia({reducedMotion:'reduce'});
  if(width>=760){const sides=await report.locator('#rp-plans .rp-round').evaluateAll(items=>items.slice(0,2).map(item=>item.querySelector('.r-plan-content').getBoundingClientRect().left));assert(sides[1]>sides[0]+100,'successive timeline rounds alternate left and right at '+width);}
  await report.locator('[data-action="expand-all"]').click();
  const deep=report.locator('#rp-current [data-node-id="depth-7"]');
  assert.equal(await deep.locator(':scope > [data-task]').textContent(),longTitle,'long titles stay complete');
  const layout=await deep.evaluate(node=>{const title=node.querySelector(':scope > [data-task]'),badge=node.querySelector(':scope > .r-task-meta .r-state'),time=node.querySelector(':scope > .r-task-meta .r-task-time');return {titleWidth:title.getBoundingClientRect().width,titleOverflow:title.scrollWidth>title.clientWidth+1,badgeWidth:badge.getBoundingClientRect().width,badgeHeight:badge.getBoundingClientRect().height,badgeBottom:badge.getBoundingClientRect().bottom,timeTop:time.getBoundingClientRect().top,border:getComputedStyle(node).borderTopWidth};});
  assert(layout.titleWidth>=150,'deep title keeps usable width at '+width);
  assert.equal(layout.titleOverflow,false,'long title wraps without clipping at '+width);
  assert(layout.badgeWidth>60&&layout.badgeHeight<45,'status stays a readable complete badge at '+width);
  assert(layout.timeTop>=layout.badgeBottom,'time has its own line at '+width);
  assert.equal(layout.border,'1px','each task has a visible boundary');
  assert.equal(await report.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'expanded page overflow at '+width);
  await report.locator('#rp-tab-table').click();
  assert.equal(await report.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'table viewport overflow at '+width);
  await report.locator('#rp-tab-timeline').click();
 }
 assert.equal(await report.evaluate(()=>document.getAnimations().filter(animation=>animation.playState==='running').length),0,'reduced motion has no running visual animation');
 results.push('Actual HTML/XLSX save: collapsed three-plan winding river; nested/bulk/keyboard disclosures; OLD→NEXT reason and closedAt; independent archive IDs; table preserves attempts/reviews; hostile strings; 320–1280px and reduced motion.');
 results.push('Semantic attention: current failure/rejection, review, dependencies, open result and running; neutral history/cancellation/unrecorded acceptance; collapsed parent counts and matching table badges.');
 results.push('Task readability: numbered seven-level hierarchy with capped indentation, full long titles and badges, distinct direct/bottom-level counts, end-only/start-only/missing/cross-day time facts with raw timestamp tooltips; expanded 320–1280px layouts.');
 results.push('A/C ledger: distinct highlighted root circles, inline hierarchical numbers, rotating chevrons, true visible parent-child fine rails pinned to wrapped-row marker geometry, combined factual time fields and full audit disclosure without column stretching.');
 results.push('Six light themes default to porcelain blue under a dark OS; persisted choice, invalid preference fallback and unavailable storage are safe; changing themes preserves table/disclosure state.');
 await close(exported);
 const columns=await saved(await timeColumnsFixture(fixture)),timeReport=columns.report;
 fs.copyFileSync(columns.files.find(file=>file.name.endsWith('.html')).path,'/tmp/task-progress-columns-preview.html');
 await timeReport.locator('#rp-tab-table').click();await timeReport.locator('[data-action="expand-all"]').click();
 const currentTimeRows=timeReport.locator('.rp-task-table tbody tr').filter({has:timeReport.locator('.rp-table-round', {hasText:'第 3 轮'})});
 const timeRow=id=>currentTimeRows.filter({has:timeReport.locator('.rp-table-title',{hasText:'Time case '+id})});
 const early=await timeRow('early').locator('.rp-time-track').evaluate(e=>({start:e.dataset.axisStart,end:e.dataset.axisEnd,left:e.querySelector('[data-time-kind="actual"]').style.left,width:e.querySelector('[data-time-kind="actual"]').style.width}));
 const late=await timeRow('late').locator('.rp-time-track').evaluate(e=>({start:e.dataset.axisStart,end:e.dataset.axisEnd,left:e.querySelector('[data-time-kind="actual"]').style.left,width:e.querySelector('[data-time-kind="actual"]').style.width}));
 assert.deepEqual([early.start,early.end,early.width],[late.start,late.end,late.width],'equal durations occupy equal widths on the same round domain');assert(Number.parseFloat(late.left)>Number.parseFloat(early.left),'later starts move right on the shared axis');
 const allAxes=await timeReport.locator('.rp-task-table .rp-time-track').evaluateAll(es=>es.map(e=>[e.dataset.axisStart,e.dataset.axisEnd]));
 assert(allAxes.length>2&&allAxes.every(([start,end])=>start===early.start&&end===early.end),'every round and archived instance uses the same global axis');
 assert.equal(Number(early.start),Date.parse('2026-10-25T00:00:00Z'),'earliest start from an older round sets the axis origin');
 assert.equal(Number(early.end),Date.parse('2026-10-25T10:00:00Z'),'a start beyond the last end remains visible and marks the boundary incomplete');
 assert.equal(await timeRow('late').locator('[data-time-kind="previous-attempt"]').count(),1,'earlier retries appear on the task track');
 assert.match(await timeReport.locator('.rp-time-guide').textContent(),/全局时间轴/);
 const geometry=await timeRow('early').locator('.rp-table-time').evaluate(el=>{const label=el.querySelector('.rp-time-range').getBoundingClientRect(),bar=el.querySelector('.rp-time-track').getBoundingClientRect();return {labelRight:label.right,barLeft:bar.left,barTop:bar.top,labelTop:label.top};});
 assert(geometry.barLeft>=geometry.labelRight,'time range sits to the left of its track');assert(Math.abs(geometry.barTop-geometry.labelTop)<30,'range and track share a horizontal row');
 const headerLeft=await timeReport.locator('.rp-time-head .rp-axis-labels').evaluate(el=>el.getBoundingClientRect().left);assert(Math.abs(headerLeft-geometry.barLeft)<1,'axis labels align with row tracks');
 assert.match(await timeReport.locator('.rp-time-legend').textContent(),/任务区间.*实际执行.*含重试.*截至快照.*重新规划/);
 assert.equal(await timeReport.locator('.rp-time-plan,.rp-time-axis,.rp-time-source,.rp-time-note').count(),0,'secondary time explanations do not crowd the table');
 assert.equal(await timeReport.locator('[data-time-kind="planned"],.rp-task-table progress').count(),0,'no actor assignment, duration or percentage becomes a planned interval');
 const running=timeRow('running');assert((await running.locator('.rp-table-time').getAttribute('title')).includes('截至快照'));assert((await running.locator('.rp-time-total').textContent()).includes('进行中'));
 assert.equal(await running.locator('[data-time-kind="snapshot"]').count(),1);assert.equal(await running.locator('[data-time-kind="actual"]').count(),0,'snapshot cutoff is not a recorded execution end');
 assert((await timeRow('future-start').locator('.rp-table-time').getAttribute('title')).includes('快照时间缺失或早于开始'));assert.equal(await timeRow('future-start').locator('[data-time-kind="snapshot"]').count(),0);
 for(const [id,note] of [['end-only','开始未知'],['start-only','结束未知'],['reverse','结束早于开始'],['invalid','时间格式无效'],['equal','同一时间点']]){assert((await timeRow(id).locator('.rp-table-time').getAttribute('title')).includes(note),id+' boundary explanation');assert.equal(await timeRow(id).locator('[data-time-kind]').count(),0,id+' cannot invent an interval');}
 assert((await timeRow('invalid').locator('.rp-table-time').getAttribute('title')).includes('2026-02-30T01:00:00Z'),'invalid calendar dates stay raw instead of normalizing to a different day');
 assert.equal(await timeReport.locator('.rp-table-replan').count(),2,'only adjacent historical rounds in this instance have replan records');
 assert.equal(await timeReport.locator('.rp-time-replan').count(),1,'unknown close time cannot become an axis marker');
 const recordedReplans=timeReport.locator('.rp-table-replan');assert((await recordedReplans.nth(0).textContent()).includes('FIRST_REPLAN_REASON'));assert((await recordedReplans.nth(1).textContent()).includes('时间未记录'));assert((await recordedReplans.nth(1).textContent()).includes('SECOND_REPLAN_UNKNOWN_TIME'));
 assert.equal(await timeReport.locator('.rp-task-table tr[data-table-depth="0"] .rp-replan-note').filter({hasText:'本轮未发生'}).count(),2,'latest rounds stay distinct from recorded transitions, including archive close without a successor');
 assert((await timeRow('early').textContent()).includes('RECORDED_SUMMARY_early'));assert((await timeRow('early').locator('.rp-table-time').getAttribute('title')).includes('2026-10-25T01:00:00Z'),'raw timestamp retained');
 assert((await timeRow('early').locator('.rp-table-detail-body').textContent()).includes('fixture://submission-1'),'original execution evidence survives the column view');
 await close(columns);results.push('Factual columns: shared report-wide axis across rounds and archives; retry segments; no invented plan dates; running snapshot boundary; partial and invalid times; replan identity and raw evidence retained.');
 const invalidClose='2026-02-30T01:00:00Z',invalidColumns=await saved(await timeColumnsFixture(fixture,invalidClose)),invalidReport=invalidColumns.report;
 await invalidReport.locator('#rp-tab-table').click();
 const invalidReplans=invalidReport.locator('.rp-table-replan'),invalidSummary=await invalidReplans.first().locator(':scope > summary').textContent();
 assert.equal(await invalidReplans.count(),2,'invalid close time preserves the recorded round transition count');
 assert(invalidSummary.includes('本轮结束时重规划 1 次')&&invalidSummary.includes('时间无效 · '+invalidClose),'invalid replan date remains raw and explicitly invalid');
 assert(!invalidSummary.includes('03/02'),'invalid calendar date cannot normalize to March 2 in the replan column');
 assert((await invalidReplans.first().textContent()).includes('FIRST_REPLAN_REASON'),'invalid close time preserves the replan reason');
 assert.equal(await invalidReport.locator('.rp-time-replan').count(),0,'invalid and missing close times cannot become axis markers');
 await close(invalidColumns);results.push('Invalid replan closed_at stays raw and explicitly invalid, with transition count/reason preserved and no invented date or axis marker.');
 const linear=await host.rpc('init_progress',{task_title:'Linear compatibility',legacy_linear:true,steps:['Linear first','Linear second']});
 const linearExport=await saved(linear);assert((await linearExport.report.locator('body').textContent()).includes('Linear first'));assert.equal(await linearExport.report.locator('.r-replan').count(),0);assert.equal(await linearExport.report.locator('#rp-theme').inputValue(),'porcelain-blue','another report starts with its own default');await close(linearExport);
 const empty=await host.rpc('init_task_network',{task_title:'No children yet',atomic_reason:'One empty fixture root'});
 const emptyExport=await saved(empty);assert.equal(await emptyExport.report.locator('.r-replan').count(),0);assert((await emptyExport.report.locator('body').textContent()).includes('No children yet'));await close(emptyExport);
 const noNodes=clone(empty);p(noNodes).nodes=[];
 const noNodesExport=await saved(noNodes);assert.equal(await noNodesExport.report.locator('.r-replan').count(),0);assert((await noNodesExport.report.locator('body').textContent()).includes('该快照没有任务节点'));await close(noNodesExport);
 results.push('Linear, unsplit single-root and empty-node reports export without invented replans; SQLite-only compatibility remains covered by test_history_loading_app.cjs.');
 assert.deepEqual(errors,[],'no app/export page or console errors');assert.deepEqual(host.errors,[]);
 assert.equal(createHash('sha256').update(fs.readFileSync(app)).digest('hex'),digest,'app source changed during serial browser checks');
 console.log(JSON.stringify({sourceSha256:digest,passed:results,preview:'/tmp/task-progress-export-preview.html'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>host?.close());
