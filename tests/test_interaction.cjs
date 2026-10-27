/* Interaction regressions against real app/server with an isolated MCP Apps host. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const artifactDir = process.env.TASK_PROGRESS_TEST_ARTIFACTS;
if (artifactDir) fs.mkdirSync(artifactDir, {recursive:true});
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'progress-interaction-audit-'));
const html = fs.readFileSync(path.join(root, 'plugins/task-progress/app.html'), 'utf8');
const pending = new Map(); let sequence = 0; let browser; const results = [];
const python = spawn('python3', ['-c', `import importlib.util,pathlib,sys
spec=importlib.util.spec_from_file_location('audit',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.SESSION_DIR=pathlib.Path(sys.argv[2]);m.main()
`, path.join(root, 'plugins/task-progress/server.py'), temp]);
python.stderr.on('data', data => process.stderr.write(data));
readline.createInterface({input:python.stdout}).on('line', line => {
  const r=JSON.parse(line), waiter=pending.get(r.id); if(!waiter)return; pending.delete(r.id);clearTimeout(waiter.timer);
  if(r.error||r.result?.isError)waiter.reject(new Error(JSON.stringify(r.error||r.result.content)));else waiter.resolve(r.result);
});
function rpc(name,args){const id=++sequence;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Backend timeout: '+name)),5000);pending.set(id,{resolve,reject,timer});python.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args}})+'\n');});}
const assignment={execution_mode:'main_session',model:'audit-fixture',reasoning_effort:'high'};
const add=(name,observed)=>results.push({case:name,observed});
async function main(){
  browser=await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1000,height:1100}});page.setDefaultTimeout(5000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('auditRpc', async(name,args)=>{
    if(name==='save_export_files'){
      for(const file of args.files)fs.writeFileSync(path.join(temp,file.name),file.encoding==='base64'?Buffer.from(file.content,'base64'):file.content);
      return {structuredContent:{saved:true,output_directory:temp,files:args.files.map(f=>({path:path.join(temp,f.name)}))}};
    }
    return rpc(name,args);
  });
  await page.setContent('<!doctype html><body style="margin:0;background:#fff"><main id="frames"></main></body>');
  await page.evaluate(()=>{
    window.audit={snapshots:{},modes:{},calls:[],failReads:false,failRegister:false,download:false};
    addEventListener('message',async e=>{
      const m=e.data;if(!m||m.jsonrpc!=='2.0')return;
      const frame=[...document.querySelectorAll('iframe')].find(f=>f.contentWindow===e.source);if(!frame)return;
      const reply=result=>e.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*');
      if(m.method==='ui/initialize')reply({protocolVersion:'2026-01-26',hostCapabilities:window.audit.download?{downloadFile:{}}:{},hostContext:{displayMode:window.audit.modes[frame.id]||'inline',theme:'light'}});
      if(m.method==='ui/notifications/initialized')e.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.audit.snapshots[frame.id]},'*');
      if(m.method==='ui/download-file'){window.audit.calls.push({frame:frame.id,name:m.method});reply({});}
      if(m.method==='tools/call'){
        window.audit.calls.push({frame:frame.id,name:m.params.name});
        if((m.params.name==='get_progress'&&window.audit.failReads)||(m.params.name==='register_ui_view'&&window.audit.failRegister)){reply({isError:true,content:[{type:'text',text:'Injected transient unavailable'}]});return;}
        try{reply(await window.auditRpc(m.params.name,m.params.arguments));}catch(err){e.source.postMessage({jsonrpc:'2.0',id:m.id,error:{code:-32603,message:err.message}},'*');}
      }
    });
  });
  async function mount(id,snapshot,width=900,displayMode='inline'){
    await page.evaluate(({id,snapshot,html,width,displayMode})=>{window.audit.snapshots[id]=snapshot;window.audit.modes[id]=displayMode;const f=document.createElement('iframe');f.id=id;f.style=`width:${width}px;height:1000px;border:0;display:block`;f.srcdoc=html;document.getElementById('frames').append(f);},{id,snapshot,html,width,displayMode});
    const frame=page.frameLocator('#'+id);await frame.locator('#summary').waitFor({state:'attached'});return frame;
  }
  const initial=await rpc('init_task_network',{task_title:'交互检查：任务执行与结果追溯',main_session:{model:'audit-fixture',reasoning_effort:'high'}});
  const sid=initial.structuredContent.session_id;
  await rpc('decompose_task',{session_id:sid,task_id:'root',tasks:[{id:'a',title:'读取长任务说明并复制结果',assignment:{execution_mode:'main_session'}},{id:'b',title:'检查报告',depends_on:['a'],assignment:{execution_mode:'main_session'}}]});
  let r=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'running',assignment:{...assignment,dispatch_text:Array.from({length:70},(_,i)=>'第 '+i+' 行：需要保留阅读位置的长任务说明。').join('\n')}});
  const aid=r.structuredContent.progress.nodes.find(n=>n.id==='a').attempt_id;
  r=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'running',attempt_id:aid,summary:'供用户复制的稳定结果文本，自动刷新时内容没有发生变化。'});
  const card=await mount('current',r,900,'fullscreen');
  await page.waitForFunction(()=>window.audit.calls.some(c=>c.name==='register_ui_view'));
  const openAndSelect=()=>{
    const leaf=document.querySelector('li.step > details[data-key$=":a"]');leaf.open=true;
    for(const d of leaf.querySelectorAll('details'))d.open=true;
    const scroll=leaf.querySelector('.dispatch .detail-value');scroll.scrollTop=160;
    const summary=leaf.querySelector(':scope > summary');summary.focus();
    const range=document.createRange();range.selectNodeContents(leaf.querySelector('.note .detail-value'));const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
    return {focus:document.activeElement.parentElement.dataset.key,selection:selection.toString(),scrollTop:scroll.scrollTop};
  };
  const before=await card.locator('body').evaluate(openAndSelect);
  const readCount=await page.evaluate(()=>window.audit.calls.filter(c=>c.name==='get_progress').length);
  await page.waitForFunction(n=>window.audit.calls.filter(c=>c.name==='get_progress').length>n,readCount,{timeout:8500});
  await card.locator('body').evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const after=await card.locator('body').evaluate(()=>({focusTag:document.activeElement.tagName,focusKey:document.activeElement.parentElement?.dataset.key||null,selection:getSelection().toString(),scrollTop:document.querySelector('li.step > details[data-key$=":a"] .dispatch .detail-value').scrollTop,expanded:document.querySelector('li.step > details[data-key$=":a"]').open}));
  assert.equal(after.focusKey,before.focus);assert.equal(after.selection,before.selection);assert.equal(after.scrollTop,before.scrollTop);assert.equal(after.expanded,true);
  add('automatic_poll_preserves_interaction',{before,after});
  const changed=await rpc('update_task',{session_id:sid,task_id:'b',execution_status:'cancelled'});
  await page.evaluate(r=>document.getElementById('current').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*'),changed);
  await card.locator('li.step.cancelled').waitFor();
  const changedState=await card.locator('body').evaluate(()=>({focusKey:document.activeElement.parentElement?.dataset.key,selection:getSelection().toString(),scrollTop:document.querySelector('li.step > details[data-key$=":a"] .dispatch .detail-value').scrollTop}));
  assert.equal(changedState.focusKey,before.focus);assert.equal(changedState.selection,before.selection);assert.equal(changedState.scrollTop,before.scrollTop);
  await page.keyboard.press('Enter');assert.equal(await card.locator('li.step > details[data-key$=":a"]').evaluate(e=>e.open),false);
  await page.keyboard.press('Enter');assert.equal(await card.locator('li.step > details[data-key$=":a"]').evaluate(e=>e.open),true);
  add('changed_sibling_preserves_interaction_and_keyboard',changedState);
  const selectedHeader=await card.locator('#task-title').evaluate(element=>{
    const range=document.createRange();range.selectNodeContents(element);getSelection().removeAllRanges();getSelection().addRange(range);
    return getSelection().toString();
  });
  await page.evaluate(r=>document.getElementById('current').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*'),changed);
  await card.locator('body').evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert.equal(await card.locator('body').evaluate(()=>getSelection().toString()),selectedHeader);
  add('unchanged_header_preserves_selection',selectedHeader);
  await card.locator('#refresh').focus();
  await page.evaluate(()=>{window.audit.failReads=true;});
  const failedReadCount=await page.evaluate(()=>window.audit.calls.filter(c=>c.name==='get_progress').length);
  await page.waitForFunction(n=>window.audit.calls.filter(c=>c.name==='get_progress').length>n,failedReadCount,{timeout:8500});
  await card.locator('body').evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert((await card.locator('#auto-refresh-label').textContent()).includes('同步失败'));
  assert.equal(await card.locator('#auto-refresh-status').evaluate(e=>e.classList.contains('failed')),true);
  assert.equal(await card.locator('body').evaluate(()=>document.activeElement.id),'refresh');
  add('automatic_poll_error_feedback',{label:await card.locator('#auto-refresh-label').textContent(),error:await card.locator('#error').textContent(),errorVisible:await card.locator('#error').isVisible(),paused:await card.locator('#auto-refresh-status').evaluate(e=>e.classList.contains('paused'))});
  await card.locator('#refresh').click();await card.locator('#error:not(.hidden)').waitFor();
  add('manual_refresh_error_feedback',{error:await card.locator('#error').textContent()});
  await page.evaluate(()=>{window.audit.failReads=false;});await card.locator('#refresh').click();await card.locator('#refresh:not([disabled])').waitFor();
  assert.equal(await card.locator('#error').isVisible(),false);assert.equal(await card.locator('#auto-refresh-status').evaluate(e=>e.classList.contains('failed')),false);
  add('manual_refresh_recovers',{errorHidden:!await card.locator('#error').isVisible()});
  const widths=[];
  for(const width of [280,320,375,480,900]){
    await page.locator('#current').evaluate((f,w)=>f.style.width=w+'px',width);
    widths.push(await card.locator('body').evaluate(()=>{const button=document.getElementById('refresh').getBoundingClientRect(),card=document.querySelector('.card');return{width:innerWidth,bodyScrollWidth:document.body.scrollWidth,cardWidth:card.clientWidth,cardScrollWidth:card.scrollWidth,refreshRight:button.right,refreshInside:button.right<=innerWidth};}));
    if(artifactDir && [280,320].includes(width))await page.locator('#current').screenshot({path:path.join(artifactDir,'interaction-after-'+width+'.png')});
  }
  assert(widths.every(w=>w.refreshInside && w.cardScrollWidth<=w.cardWidth+1),JSON.stringify(widths));
  add('responsive_widths',widths);
  assert.equal(await card.locator('html').getAttribute('data-theme'),'light');
  assert.equal(await card.locator('.card').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(255, 255, 255)');
  add('light_host_theme',{cardBackground:await card.locator('.card').evaluate(e=>getComputedStyle(e).backgroundColor),colorScheme:await card.locator('html').evaluate(e=>getComputedStyle(e).colorScheme)});
  const contrast=await card.locator('.dispatch').first().evaluate(element=>{
    const luminance=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);
    const bg=luminance(getComputedStyle(element).backgroundColor);
    return ['summary','.detail-value'].map(selector=>{const fg=luminance(getComputedStyle(element.querySelector(selector)).color);return (Math.max(bg,fg)+0.05)/(Math.min(bg,fg)+0.05);});
  });
  assert(contrast.every(value=>value>=4.5),JSON.stringify(contrast));add('light_theme_readable_dispatch',contrast);
  await page.evaluate(()=>document.getElementById('current').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{theme:'dark'}},'*'));
  await card.locator('html[data-theme="dark"]').waitFor();assert.equal(await card.locator('.card').evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(21, 25, 32)');
  add('host_theme_change','light to dark applied');
  // Finish, retry, and verify whether the current card exposes preserved attempt history.
  await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'failed',attempt_id:aid,summary:'FIRST_ATTEMPT_FAILURE_EVIDENCE'});
  r=await rpc('update_task',{session_id:sid,task_id:'a',execution_status:'running',retry:true,assignment});
  await card.locator('#refresh').click();await card.locator('#refresh:not([disabled])').waitFor();
  await card.locator('body').evaluate(()=>document.querySelectorAll('details').forEach(d=>d.open=true));
  assert((await card.locator('body').textContent()).includes('FIRST_ATTEMPT_FAILURE_EVIDENCE'));
  assert.equal(await card.locator('li.step > details[data-key$=":a"] > .task-details > .attempt-history .attempt-record').count(),2);
  add('retry_attempt_visibility',{storedAttempts:r.structuredContent.progress.nodes.find(n=>n.id==='a').attempts.length,oldFailureInDOM:(await card.locator('body').textContent()).includes('FIRST_ATTEMPT_FAILURE_EVIDENCE')});
  await card.locator('#export-all').click();await card.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
  const report=await browser.newPage();await report.goto('file://'+path.join(temp,fs.readdirSync(temp).find(f=>f.endsWith('.html'))));
  const text=await report.locator('#task-progress-report .rp-node[data-node-id="a"] .rp-node-body').textContent();
  add('export_plan_line_breaks',{text,containsLiteralBackslashN:text.includes('\\n'),containsNewline:text.includes('\n'),attemptFailureInHTML:(await report.locator('body').textContent()).includes('FIRST_ATTEMPT_FAILURE_EVIDENCE')});
  assert.equal(text.includes('\\n'),false,text);assert.equal(text.includes('\n'),true,text);
  if(artifactDir)await report.screenshot({path:path.join(artifactDir,'export-after.png'),fullPage:true});
  await report.close();
  const nextTask=await rpc('init_task_network',{session_id:sid,task_title:'切换后的任务'});
  await page.evaluate(r=>document.getElementById('current').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:r},'*'),nextTask);
  await card.locator('#task-title').filter({hasText:'切换后的任务'}).waitFor();
  await card.locator('body').evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert.equal(await page.locator('iframe').count(),1);assert.equal(await card.locator('#task-title').isVisible(),true);
  assert.equal(await card.locator('.card').evaluate(e=>e.classList.contains('compact')),false);
  add('task_switch_keeps_existing_full_view','same full view shows the new task without a second iframe');
  // A transient registration error retries with the original source token, then yields to a newer view.
  await page.evaluate(()=>{window.audit.failRegister=true;});
  const recovery=await mount('registration-recovery',await rpc('get_progress',{session_id:sid}));
  await page.waitForFunction(()=>window.audit.calls.some(c=>c.frame==='registration-recovery'&&c.name==='register_ui_view'));
  await page.evaluate(()=>{window.audit.failRegister=false;});
  await page.waitForFunction(()=>window.audit.calls.filter(c=>c.frame==='registration-recovery'&&c.name==='register_ui_view').length>=2,{},{timeout:5000});
  const newest=await mount('newest',await rpc('get_progress',{session_id:sid}));
  await page.waitForFunction(()=>window.audit.calls.some(c=>c.frame==='newest'&&c.name==='register_ui_view'));
  await recovery.locator('.card.inline-stale').waitFor({timeout:8500});
  assert.equal(await card.locator('.card').evaluate(e=>e.classList.contains('compact')),false);
  assert.equal(await newest.locator('.card').evaluate(e=>e.classList.contains('inline-stale')),false);
  add('registration_failure_recovers',{registrationCalls:await page.evaluate(()=>window.audit.calls.filter(c=>c.frame==='registration-recovery'&&c.name==='register_ui_view').length),viewChecks:await page.evaluate(()=>window.audit.calls.filter(c=>c.frame==='registration-recovery'&&c.name==='check_ui_view').length),failedCardCollapsed:await recovery.locator('.card').evaluate(e=>e.classList.contains('inline-stale')),newestCollapsed:await newest.locator('.card').evaluate(e=>e.classList.contains('inline-stale'))});
  assert.deepEqual(errors,[]);add('runtime_errors',errors);
  console.log(JSON.stringify({browser:await browser.version(),host:'Simulated MCP Apps parent; actual app.html and isolated server.py',results},null,2));
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();python.stdin.end();for(const p of pending.values())clearTimeout(p.timer);fs.rmSync(temp,{recursive:true,force:true});});
