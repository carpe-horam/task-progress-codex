/* Validate semantic ranges and rendered geometry through the actual HTML export. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
const {createHost}=require('./browser_host.cjs');
const fixture=require('./fixtures/realistic_execution.cjs');
let host;
(async()=>{try{
 host=await createHost();
 const init=await host.rpc('init_task_network',{task_title:'Isolated realistic timeline fixture'});
 const {progress,expected}=fixture(init.structuredContent.progress),sid=progress.session_id;
 const sessionFile=path.join(host.temp,'sessions',sid+'.json');fs.writeFileSync(sessionFile,JSON.stringify(progress));
 const snapshot=await host.rpc('get_progress_view',{session_id:sid,include_history:true});
 const before=fs.readFileSync(sessionFile,'utf8');
 const exported=await host.exportReport(snapshot),page=exported.report;
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setViewportSize({width:1720,height:1100});await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto(page.url()+'#table');await page.reload();
 assert.equal(await page.locator('#rp-tab-table').getAttribute('aria-selected'),'true','table link opens the requested view');
 assert.equal(await page.locator('[data-table-key]:visible').count(),2,'only main tasks visible by default');
 const selector=page.locator('#rp-track-style'),root=page.locator('#task-progress-report');
 const styles=['signal','ink','mist','outline','dotted','stripe','bracket','ruler','beads','ribbon'];
 assert.equal(await selector.inputValue(),'beads','node trail is the default');
 assert.deepEqual(await selector.locator('option').evaluateAll(es=>es.map(e=>e.value)),styles);
 const originalRanges=await page.locator('.rp-time-track').evaluateAll(es=>es.map(e=>[e.dataset.rangeStart,e.dataset.rangeEnd,e.dataset.axisStart,e.dataset.axisEnd]));
 for(const style of styles){await selector.selectOption(style);assert.equal(await root.getAttribute('data-track-style'),style);assert.deepEqual(await page.locator('.rp-time-track').evaluateAll(es=>es.map(e=>[e.dataset.rangeStart,e.dataset.rangeEnd,e.dataset.axisStart,e.dataset.axisEnd])),originalRanges,'style must not change factual axis');}
 await selector.selectOption('bracket');await page.reload();assert.equal(await selector.inputValue(),'bracket','style preference survives reload');
 await page.locator('#rp-theme').selectOption('porcelain-blue');assert.equal(await selector.inputValue(),'bracket','theme changes preserve timeline style');
 await selector.selectOption('beads');

 await page.locator('[data-action="expand-all"]').click();
 await page.locator('.rp-table-connectors path').first().waitFor({state:'attached'});
 const row=title=>page.locator('.rp-task-table tbody tr').filter({has:page.locator('.rp-table-title',{hasText:new RegExp('^'+title+'$')})});
 for(const [title,[from,to]]of Object.entries(expected.ranges)){
  const track=row(title).locator('.rp-time-track');
  assert.equal(Number(await track.getAttribute('data-range-start')),Date.parse(expected.at(from)),title+' starts at earliest attempt');
  assert.equal(Number(await track.getAttribute('data-range-end')),Date.parse(expected.at(to)),title+' includes latest descendant/end or snapshot');
 }
 const tracks=await page.locator('.rp-time-track').evaluateAll(items=>items.map(el=>({a:+el.dataset.axisStart,b:+el.dataset.axisEnd})));
 assert(tracks.every(t=>t.a===Date.parse(expected.axis[0])&&t.b===Date.parse(expected.axis[1])),'all rows share report-wide axis');
 const geometry=await page.locator('.rp-task-table').evaluate(table=>{
  const rows=[...table.querySelectorAll('[data-table-key]')],byKey=new Map(rows.map(r=>[r.dataset.tableKey,r]));
  const box=row=>{const t=row.querySelector('.rp-time-track'),e=t.querySelector('.rp-time-envelope');return e?{start:+t.dataset.rangeStart,end:+t.dataset.rangeEnd,left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right}:null};
  return rows.filter(r=>r.dataset.tableParent&&box(r)).map(r=>({title:r.querySelector('.rp-table-title').textContent,child:box(r),parent:box(byKey.get(r.dataset.tableParent))}));
 });
 assert(geometry.length>=15);
 for(const {title,child,parent}of geometry){assert(parent,title+' parent has span');assert(parent.start<=child.start&&parent.end>=child.end,title+' semantic containment');assert(parent.left<=child.left+.1&&parent.right+.1>=child.right,title+' pixel containment');}
 const retry=row('修复文字布局与换行');assert.match(await retry.locator('.rp-time-range').textContent(),/10:15.*10:52/);
 assert.match(await retry.locator('.rp-time-total').textContent(),/历时 37分.*重试 1 次/);
 assert.equal(await retry.locator('[data-time-kind="previous-attempt"]').count(),1);
 assert.equal(await retry.locator('[data-time-kind="actual"]').count(),1);
 for(const title of ['修复图片裁切偏移','修复文字布局与换行']){
  const centers=await row(title).locator('.rp-time-track').evaluate(track=>{const env=track.querySelector('.rp-time-envelope'),actual=track.querySelector('.rp-time-actual');const e=env.getBoundingClientRect(),a=actual.getBoundingClientRect();const before=getComputedStyle(env,'::before'),after=getComputedStyle(env,'::after');return [e.top+parseFloat(before.top)+parseFloat(before.height)/2,e.top+parseFloat(after.top)+parseFloat(after.height)/2,a.top+a.height/2];});
  assert(Math.abs(centers[0]-centers[2])<.1&&Math.abs(centers[1]-centers[2])<.1,title+' endpoint centers align with execution bar');
 }

 assert.match(await retry.locator('.rp-table-time').getAttribute('title'),/24分/,'actual work is 24min, not 37min elapsed');
 assert.equal(await row('提交交付与远端核对').locator('.rp-time-span').count(),0,'pending never gets a fake interval');
 assert.match(await row('最终运行验证').locator('.rp-time-total').textContent(),/进行中.*截至快照/);
 assert.equal(await page.locator('.rp-time-replan').count(),1);
 assert.deepEqual(await page.locator('.rp-axis-labels>span').allTextContents(),['09:00','10:00','11:00','12:15']);
 assert.doesNotMatch(await row('提交交付与远端核对').locator('.rp-time-total').textContent(),/快照|历时/);
 const files=await exported.hostPage.evaluate(()=>window.host.calls.find(c=>c.name==='save_export_files').result.structuredContent.files);
 const html=fs.readFileSync(files.find(f=>f.name.endsWith('.html')).path,'utf8');
 const result={sourceSha256:createHash('sha256').update(fs.readFileSync(path.join(__dirname,'../plugins/task-progress/app.html'))).digest('hex'),nodes:20,axis:expected.axis,parentChildChecks:geometry.length,geometry};
 if(process.env.TASK_PROGRESS_DEMO_DIR){const dir=process.env.TASK_PROGRESS_DEMO_DIR;fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,'task-progress-time-validation.html'),html);fs.writeFileSync(path.join(dir,'task-progress-realistic-fixture.json'),JSON.stringify(snapshot,null,2));fs.writeFileSync(path.join(dir,'task-progress-time-axis-validation.json'),JSON.stringify(result,null,2));
  await page.locator('#rp-workbook').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(dir,'task-progress-time-axis-desktop.png'),fullPage:true});
  // Also capture a focused selection at ordinary laptop width.
  await page.locator('[data-action="collapse-all"]').click();
  await page.locator('[data-table-depth="0"]').last().locator('[data-table-toggle]').click();
  await row('并行修复三个模块').locator('[data-table-toggle]').click();
  await page.setViewportSize({width:1440,height:1000});await page.locator('#rp-workbook').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(dir,'task-progress-time-axis-laptop.png')});
 }
 for(const width of [1440,760,390]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page stays within viewport at '+width);}
 assert.equal(fs.readFileSync(sessionFile,'utf8'),before,'export does not rewrite task records');assert.deepEqual(errors,[]);assert.deepEqual(host.errors,[]);
 console.log(JSON.stringify({nodes:20,parentChildChecks:geometry.length,axis:expected.axis,sourceSha256:result.sourceSha256,result:'PASS'}));
}finally{await host?.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
