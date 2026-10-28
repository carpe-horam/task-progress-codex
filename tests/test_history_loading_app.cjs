/* Lazy history and stale-response checks use the actual app, real save handler and private fixtures. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {createHost}=require('./browser_host.cjs');
const results=[];let host;
const clone=x=>JSON.parse(JSON.stringify(x));
const progress=r=>r.structuredContent.progress;
function compact(r){const c=clone(r),p=progress(c);p.history_included=false;delete p.history;delete p.task_history;delete p.legacy_history;return c;}
async function push(page,r){await page.evaluate(snapshot=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot},'*'),r);}
async function configure(page,full){await page.evaluate(full=>{
  const real=window.hostRpc;window.fixture={full,fail:false,held:null,hold:false};
  window.hostRpc=async(name,args)=>{
    if(name!=='get_progress_view')return real(name,args);
    if(args.include_history){
      if(window.fixture.fail)throw new Error('fixture full history unavailable');
      const captured=structuredClone(window.fixture.full);
      if(window.fixture.hold){window.fixture.hold=false;await new Promise(resolve=>window.fixture.held=resolve);}
      return captured;
    }
    const c=structuredClone(window.fixture.full),p=c.structuredContent.progress;
    p.history_included=false;delete p.history;delete p.task_history;delete p.legacy_history;return c;
  };
},full);}
async function savedReport(page){const call=await page.evaluate(()=>window.host.calls.findLast(c=>c.name==='save_export_files'));const report=await host.browser.newPage();await report.goto('file://'+call.result.structuredContent.files.find(f=>f.name.endsWith('.html')).path);return {report,call};}
async function main(){
 host=await createHost();
 const r=await host.rpc('init_task_network',{task_title:'Compact history fixture',atomic_reason:'Isolated browser data fixture'}),sid=r.structuredContent.session_id;
 const file=path.join(host.temp,'sessions',sid+'.json'),data=JSON.parse(fs.readFileSync(file,'utf8'));
 const archived=clone(data);archived.task_instance_id='archived-one';archived.task_title='ARCHIVE_COMPLETE';archived.archived_at=data.updated_at;delete archived.task_history;
 const round=clone(data);round.task_title='PLAN_COMPLETE';round.closed_at=data.updated_at;round.epoch=0;delete round.history;delete round.task_history;
 data.task_history=[archived];data.history=[round];data.epoch=1;fs.writeFileSync(file,JSON.stringify(data));
 const full=await host.rpc('get_progress_view',{session_id:sid,include_history:true});
 assert.equal(progress(full).history_summary.task_count,1);assert.equal(progress(full).history_summary.plan_count,1);
 const page=await host.page();await configure(page,full);const frame=await host.mount(page,'main',compact(full));
 assert((await frame.locator('#task-history-title').textContent()).includes('1 项'));
 assert.equal(await page.evaluate(()=>window.host.calls.filter(c=>c.name==='get_progress_view'&&c.args.include_history).length),0,'initial compact card never loads full history');
 await frame.locator('#task-history-title').click();await frame.locator('#task-history-list').filter({hasText:'ARCHIVE_COMPLETE'}).waitFor();
 await frame.locator('#refresh').click();await frame.locator('#refresh:not([disabled])').waitFor();
 assert((await frame.locator('#task-history-list').textContent()).includes('ARCHIVE_COMPLETE'),'same binding compact refresh retains history');
 assert((await page.evaluate(()=>window.host.calls.filter(c=>c.name==='get_progress_view'))).some(c=>c.args.include_history===false),'refresh explicitly requests compact state');
 // A newer compact snapshot increases history count. It must invalidate the cache and reject the held old full response.
 await frame.locator('#task-history-title').click();
 const newer=clone(full);newer.structuredContent.state_revision=progress(full).state_revision+2;progress(newer).state_revision=newer.structuredContent.state_revision;progress(newer).epoch=2;progress(newer).revision+=2;
 const extra=clone(progress(newer).task_history[0]);extra.task_instance_id='archived-two';extra.task_title='ARCHIVE_NEW';progress(newer).task_history.push(extra);progress(newer).history_summary.task_count=2;
 await page.evaluate(()=>{window.fixture.hold=true;});
 // Invalidate current full cache while the section is closed, then begin a deliberately stale full request.
 const intermediate=clone(full);intermediate.structuredContent.state_revision++;progress(intermediate).state_revision++;
 await push(page,compact(intermediate));await frame.locator('#export-all').click();
 await page.waitForFunction(()=>!!window.fixture.held);
 await page.evaluate(newer=>{window.fixture.full=newer;},newer);await push(page,compact(newer));
 await page.evaluate(()=>{window.fixture.held();window.fixture.held=null;});
 await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
 const exported=await savedReport(page);assert((await exported.report.locator('#rp-archives').textContent()).includes('ARCHIVE_NEW'));assert((await exported.report.locator('#rp-plans').textContent()).includes('PLAN_COMPLETE'));
 await exported.report.close();
 await frame.locator('#task-history-title').click();await frame.locator('#task-history-list').filter({hasText:'ARCHIVE_NEW'}).waitFor();
 results.push('Compact initial/refresh responses omit full history; exact-bound history survives refresh, new revisions/counts invalidate it, stale full replies cannot truncate export.');
 // Failed full loading must not invoke save_export_files.
 await frame.locator('#task-history-title').click();const failed=clone(newer);failed.structuredContent.state_revision++;progress(failed).state_revision++;
 await page.evaluate(failed=>{window.fixture.full=failed;window.fixture.fail=true;},failed);await push(page,compact(failed));
 const saves=await page.evaluate(()=>window.host.calls.filter(c=>c.name==='save_export_files').length);
 await frame.locator('#export-all').click();await frame.locator('#export-feedback.error').waitFor();
 assert.equal(await page.evaluate(()=>window.host.calls.filter(c=>c.name==='save_export_files').length),saves);
 results.push('Unavailable full history produces an export error and saves zero incomplete files.');await page.close();
 // Old SQLite semantics remain separate from JSON acceptance semantics.
 const dual=clone(full);progress(dual).legacy_history={schema:3,source:'sqlite_v3',read_only:true,status:'available',task_count:1,records:[{task_instance_id:'sqlite-old',title:'SQLITE_OLD_TASK',status:'resolved',control_state:'completed',summary:'SQLITE_ORIGINAL_SUMMARY',is_current:true}]};progress(dual).history_summary.legacy_task_count=1;
 const dualPage=await host.page();await configure(dualPage,dual);const dualFrame=await host.mount(dualPage,'main',compact(dual));
 await dualFrame.locator('#legacy-history-title').click();await dualFrame.locator('#legacy-history-list').filter({hasText:'SQLITE_OLD_TASK'}).waitFor();
 const legacyText=await dualFrame.locator('#legacy-history-list').textContent();assert(legacyText.includes('状态冲突不自动合并')&&legacyText.includes('旧状态按原值展示'));
 await dualFrame.locator('#export-all').click();await dualFrame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();const dualExport=await savedReport(dualPage);assert((await dualExport.report.locator('#rp-legacy').textContent()).includes('SQLITE_ORIGINAL_SUMMARY'));
 const xlsx=dualExport.call.result.structuredContent.files.find(f=>f.name.endsWith('.xlsx')).path;
 const {execFileSync}=require('node:child_process');const workbook=execFileSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(z.read('xl/workbook.xml').decode()); print(z.read('xl/worksheets/sheet7.xml').decode())",xlsx],{encoding:'utf8'});assert(workbook.includes('SQLite只读历史')&&workbook.includes('SQLITE_ORIGINAL_SUMMARY'));
 await dualExport.report.close();await dualPage.close();
 const only=clone(dual);Object.assign(progress(only),{mode:'legacy_archive',schema_version:3,read_only:true,task_title:'SQLite-only fixture',status:'legacy_archive'});delete progress(only).nodes;delete progress(only).steps;delete progress(only).task_instance_id;progress(only).task_history=[];progress(only).history=[];progress(only).history_summary={task_count:0,plan_count:0,legacy_task_count:1};
 const onlyPage=await host.page();await configure(onlyPage,only);const onlyBootstrap={structuredContent:{session_id:only.structuredContent.session_id,state_revision:only.structuredContent.state_revision,view_sequence:only.structuredContent.view_sequence,acknowledged:true,bootstrap:true,view_tool:'get_progress_view'}};const onlyFrame=await host.mount(onlyPage,'main',onlyBootstrap);await onlyFrame.locator('#task-title').filter({hasText:'SQLite-only fixture'}).waitFor();
 assert.equal(await onlyFrame.locator('#empty').isVisible(),false);assert.equal(await onlyFrame.locator('#task-status-label').textContent(),'旧版只读记录');assert.equal(await onlyFrame.locator('#steps-section').isVisible(),false);assert(!(await onlyFrame.locator('#summary').textContent()).includes('0 / 0'));
 await onlyFrame.locator('#legacy-history-title').click();await onlyFrame.locator('#legacy-history-list').filter({hasText:'SQLITE_OLD_TASK'}).waitFor();
 assert((await onlyFrame.locator('#legacy-history-list').textContent()).includes('无当前 JSON 任务'));
 await onlyFrame.locator('#export-all').click();await onlyFrame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();const onlyExport=await savedReport(onlyPage);assert.equal(await onlyExport.report.locator('.rp-delivery').textContent(),'旧版只读记录');
 const onlyText=await onlyExport.report.locator('body').textContent();for(const invented of ['顺序步骤','待开始','当前任务','第 1 轮计划','计划沿革','当前计划展开','0/0'])assert(!onlyText.includes(invented),'SQLite-only export invents '+invented);
 assert.equal(await onlyExport.report.locator('#rp-current,#rp-plans,.rp-metrics').count(),0);
 const onlyWorkbook=onlyExport.call.result.structuredContent.files.find(f=>f.name.endsWith('.xlsx')).path;const onlyOverview=execFileSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(z.read('xl/worksheets/sheet1.xml').decode())",onlyWorkbook],{encoding:'utf8'});assert(onlyOverview.includes('旧库任务数'));assert(!onlyOverview.includes('当前任务计划轮次数')&&!onlyOverview.includes('当前计划'));

 results.push('JSON current state and SQLite history show distinct sources; SQLite-only card/export remains read-only and never claims modern acceptance.');
 if(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS){fs.mkdirSync(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,{recursive:true});await onlyExport.report.screenshot({path:path.join(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,'sqlite-only.png'),fullPage:true});fs.writeFileSync(path.join(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,'results.json'),JSON.stringify(results,null,2));fs.writeFileSync(path.join(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,'sqlite-only.html'),await onlyExport.report.content());}
 await onlyExport.report.close();await onlyPage.close();
 const partial=clone(only);progress(partial).legacy_history.truncated=true;progress(partial).legacy_history.task_count=101;progress(partial).history_summary.legacy_task_count=101;progress(partial).legacy_history.records[0].nodes_truncated=false;progress(partial).legacy_history.records[0].nodes=[{id:'missing-old-detail',details_unavailable:true}];
 const partialPage=await host.page();await configure(partialPage,partial);const partialFrame=await host.mount(partialPage,'main',compact(partial));
 await partialFrame.locator('#legacy-history-title').click();await partialFrame.locator('#legacy-history-list').filter({hasText:'这里只显示旧库摘录'}).waitFor();
 assert((await partialFrame.locator('#legacy-history-list').textContent()).includes('节点已截断或详情不可用'));
 await partialFrame.locator('#export-all').click();await partialFrame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
 const partialExport=await savedReport(partialPage);const partialText=await partialExport.report.locator('#rp-legacy').textContent();assert(partialText.includes('有界兼容摘录') && partialText.includes('历史已截断') && partialText.includes('节点已截断或详情不可用'));
 await partialExport.report.close();
 const unavailable=clone(dual);unavailable.structuredContent.state_revision++;progress(unavailable).state_revision++;progress(unavailable).legacy_history={schema:3,source:'sqlite_v3',read_only:true,status:'unavailable',task_count:0,records:[],warning:'旧库来源不可用'};progress(unavailable).history_summary.legacy_task_count=0;progress(unavailable).legacy_history_warning='旧库来源不可用';
 await partialPage.evaluate(full=>{window.fixture.full=full;},unavailable);await push(partialPage,compact(unavailable));await partialFrame.locator('#export-all').click();await partialFrame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
 const unavailableExport=await savedReport(partialPage);assert((await unavailableExport.report.locator('#rp-legacy').textContent()).includes('旧库来源不可用'));assert((await unavailableExport.report.locator('#rp-archives').textContent()).includes('ARCHIVE_COMPLETE'));await unavailableExport.report.close();
 results.push('Bounded/truncated SQLite excerpts and missing details retain explicit warnings in export; an unavailable old database cannot block complete current JSON history.');
 if(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS)fs.writeFileSync(path.join(process.env.TASK_PROGRESS_BROWSER_ARTIFACTS,'results.json'),JSON.stringify(results,null,2));
 const broken=clone(unavailable);Object.assign(progress(broken),{mode:'legacy_archive',schema_version:3,read_only:true,task_title:'Unreadable SQLite-only fixture',task_history:[],history:[]});progress(broken).history_summary={task_count:0,plan_count:0,legacy_task_count:0};delete progress(broken).nodes;delete progress(broken).task_instance_id;
 const brokenPage=await host.page();await configure(brokenPage,broken);const brokenFrame=await host.mount(brokenPage,'main',compact(broken));assert.equal(await brokenFrame.locator('#empty').isVisible(),false);assert((await brokenFrame.locator('#task-status-label').textContent()).includes('结果未知'));assert((await brokenFrame.locator('#legacy-history-title').textContent()).includes('数量未知'));await brokenFrame.locator('#legacy-history-title').click();assert((await brokenFrame.locator('#legacy-history-list').textContent()).includes('任务是否存在及结果未知'));await brokenPage.close();
 await partialPage.close();assert.deepEqual(host.errors,[]);console.log(JSON.stringify({passed:results},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>host?.close());
