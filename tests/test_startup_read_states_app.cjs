/* Production pages in a local browser with individually controlled host replies. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const {chromium} = require('playwright');

const plugin = path.resolve(__dirname,'../plugins/task-progress');
const output = process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const shots = [];

async function observe(page, frame, name, {empty = false, task = null} = {}) {
  const text = await frame.locator('body').innerText();
  assert.equal(text.includes('暂无任务'), empty, name + ': empty verdict');
  assert.equal(await frame.locator('#leaf-progress').isVisible(), !!task, name + ': progress bar');
  assert.equal(await frame.locator('#summary').isVisible(), !!task, name + ': task summary');
  if (task) assert.equal(await frame.locator('#task-title').innerText(), task);
  assert.doesNotMatch(text,/上个会话的迟到错误|旧快照残留/);
  if (output) {
    const file = name + '.png';
    const bytes = await page.screenshot({path:path.join(output,file)});
    shots.push({file,sha256:hash(bytes),text,viewport:page.viewportSize()});
  }
}

async function runSurface(browser, stage, panel) {
  const surface = panel ? 'panel' : 'app';
  const page = await browser.newPage({viewport:panel ? {width:320,height:640}:{width:390,height:780},colorScheme:panel?'dark':'light'});
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror',error => errors.push(error.message));
  const root = panel ? path.join(stage,'core') : plugin;
  const entry = panel ? 'panel-app.html?source=panel' : 'app.html';
  await page.route('http://fixture.test/**',route => {
    const file = new URL(route.request().url()).pathname.slice(1);
    if (file === 'host') return route.fulfill({contentType:'text/html',body:'<style>html,body{color-scheme:light dark;margin:0}iframe{display:block;border:none;width:100vw;height:100vh}</style><main></main>'});
    if (!['app.html','panel-app.html','panel-app.js'].includes(file)) return route.abort();
    return route.fulfill({path:path.join(root,file),contentType:file.endsWith('.js')?'text/javascript':'text/html'});
  });
  await page.goto('http://fixture.test/host');
  await page.evaluate(entry => {
    const frame = document.createElement('iframe');
    const send = message => frame.contentWindow.postMessage({jsonrpc:'2.0',...message},location.origin);
    window.reads = [];
    window.bind = id => send({method:'ui/notifications/tool-input',params:{arguments:{session_id:id}}});
    window.receipt = id => send({method:'ui/notifications/tool-result',params:{structuredContent:{session_id:id,acknowledged:true,state_revision:1}}});
    window.respond = (index,title,error) => {
      const {id,sid} = window.reads[index];
      const progress = title ? {session_id:sid,task_instance_id:sid+'-task',task_title:title,status:'in_progress',steps:[{text:'核对进度',status:'in_progress'}]} : null;
      send(error ? {id,error:{message:error}} : {id,result:{structuredContent:{session_id:sid,state_revision:1,progress}}});
    };
    addEventListener('message',event => {
      if (event.source!==frame.contentWindow) return;
      const m=event.data;
      if (m.method==='ui/initialize') window.completeHandshake=()=>send({id:m.id,result:{hostContext:{displayMode:'fullscreen',availableDisplayModes:['fullscreen']},hostCapabilities:{}}});
      if (m.method==='ui/notifications/initialized') window.ready=true;
      if (m.method==='tools/call') {
        if (m.params.name==='get_progress_view') window.reads.push({id:m.id,sid:m.params.arguments.session_id});
        else send({id:m.id,result:{structuredContent:{}}});
      }
    });
    frame.src=entry;document.querySelector('main').append(frame);
  },entry);
  const frame = page.frameLocator('iframe');
  const bind = id => page.evaluate(id=>window.bind(id),id);
  const reply = (index,title=null,error=null) => page.evaluate(([i,t,e])=>window.respond(i,t,e),[index,title,error]);
  const waitRead = (index,sid) => page.waitForFunction(([i,s])=>window.reads[i]?.sid===s,[index,sid]);
  const afterReply = () => frame.locator('body').evaluate(() => new Promise(done => requestAnimationFrame(()=>requestAnimationFrame(done))));

  try {
    await page.waitForFunction(()=>!!window.completeHandshake);
    await observe(page,frame,surface+'-01-before-handshake');
    await page.evaluate(()=>window.completeHandshake());await page.waitForFunction(()=>window.ready);
    await observe(page,frame,surface+'-02-waiting-for-session');
    await bind('first');
    if (!panel) await page.evaluate(()=>window.receipt('first'));
    await waitRead(0,'first');
    assert.equal(await frame.locator('#empty').getAttribute('aria-busy'),'true');
    await observe(page,frame,surface+'-03-reading');
    await reply(0);await frame.locator('#empty h2').filter({hasText:'暂无任务'}).waitFor();
    await observe(page,frame,surface+'-04-confirmed-empty',{empty:true});
    if (!panel) { assert.deepEqual(errors,[]); return; }
    // Keep the old read in flight across a rebind. Each side may already have shown data.
    let nextRead = 1;
    for (const oldCached of [false,true]) {
      for (const currentCached of [false,true]) {
        for (const staleError of [false,true]) {
          const scenario = [oldCached?'old-cached':'old-loading',currentCached?'new-cached':'new-loading',staleError?'error':'success'].join('-');
          const oldSession = 'old-'+scenario, currentSession = 'current-'+scenario;
          await bind(oldSession);
          let oldRead = nextRead++;
          await waitRead(oldRead,oldSession);
          if (oldCached) {
            await reply(oldRead,'切换前的交付记录');
            await frame.locator('#task-title').filter({hasText:'切换前的交付记录'}).waitFor();
            await frame.locator('#refresh').click();
            oldRead=nextRead++; await waitRead(oldRead,oldSession);
          }
          await bind(currentSession);
          const currentRead=nextRead++;
          await waitRead(currentRead,currentSession);
          await frame.locator('#summary').waitFor({state:'hidden'});
          const currentTitle='当前会话的验收记录';
          if (currentCached) {
            await reply(currentRead,currentTitle);
            await frame.locator('#task-title').filter({hasText:currentTitle}).waitFor();
          }
          await reply(oldRead,staleError?null:'旧快照残留',staleError?'上个会话的迟到错误':null);
          await afterReply();
          await observe(page,frame,'panel-'+scenario,{task:currentCached?currentTitle:null});
          assert.equal(await frame.locator('#error').isVisible(),false,scenario);
          if (currentCached) {
            assert.equal(await frame.locator('#auto-refresh-status').evaluate(el=>el.classList.contains('failed')),false);
          } else {
            assert.equal(await frame.locator('#empty').getAttribute('aria-busy'),'true',scenario);
            assert.equal(await frame.locator('#read-retry').isVisible(),false,scenario);
            await reply(currentRead,currentTitle);
            await frame.locator('#task-title').filter({hasText:currentTitle}).waitFor();
          }
        }
      }
    }
    await frame.locator('#refresh').click();
    await waitRead(nextRead,'current-old-cached-new-cached-error');
    await reply(nextRead++,null,'已缓存数据的刷新错误');
    await frame.locator('#error').filter({hasText:'已缓存数据的刷新错误'}).waitFor();
    await bind('fresh');
    await waitRead(nextRead,'fresh');
    assert.equal(await frame.locator('#error').isVisible(),false);
    assert.doesNotMatch(await frame.locator('body').innerText(),/已缓存数据的刷新错误|当前会话的验收记录/);
    await observe(page,frame,'panel-clears-visible-cached-error');
    assert.deepEqual(errors,[]);
  } finally {
    await page.close();
  }
}

// Replay the same host-session ID across three bindings through the actual panel adapter.
async function runOuterPanel(browser, stage, staleError) {
  const page=await browser.newPage({viewport:{width:320,height:640},colorScheme:'dark'});
  page.setDefaultTimeout(5000);
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://panel.test/**',route=>{
    const file=new URL(route.request().url()).pathname.slice(1);
    if (!['panel.html','panel.css','panel.js','core/panel-app.html','core/panel-app.js'].includes(file)) return route.abort();
    return route.fulfill({path:path.join(stage,file)});
  });
  await page.addInitScript(()=>{
    if (top!==self) return;
    const raw=window.fetch, channel=new BroadcastChannel('task-progress-panel'), reads=new Map();
    let session='';
    window.outerReads=reads; window.receipts=[];
    window.fetch=(url,...rest)=>{
      if(url==='/kv') return Promise.resolve(Response.json({taskProgressActiveHostSessionId:'host-'+session,taskProgressSessionsByHostSession:{['host-'+session]:session}}));
      if(url==='cindy-ghost://task-progress/wake') return Promise.resolve(new Response(''));
      return raw(url,...rest);
    };
    window.outerBind=(value,receipt)=>{
      session=value; channel.postMessage({type:'bind-session',session_id:value,receipt_id:receipt,host_session_id:'host-'+value,trigger:'session-switch'});
    };
    window.finishOuter=(requestId,title,error)=>{
      const sid=reads.get(requestId);
      const progress={session_id:sid,task_instance_id:sid+'-task',task_title:title,status:'in_progress',steps:[{text:'核对进度',status:'in_progress'}]};
      channel.postMessage(error?{type:'panel-tool-error',requestId,message:error}:{type:'panel-tool-result',requestId,result:{structuredContent:{session_id:sid,state_revision:1,progress}}});
    };
    channel.onmessage=({data})=>{
      if (data.type==='panel-refresh-result') window.receipts.push(data);
      if (data.type!=='panel-tool-request') return;
      if (data.name==='get_progress_view') reads.set(data.requestId,data.arguments.session_id);
      else channel.postMessage({type:'panel-tool-result',requestId:data.requestId,result:{structuredContent:{}}});
    };
  });
  try {
    await page.goto('http://panel.test/panel.html');
    const frame=page.frameLocator('#progress-app');
    await frame.locator('#empty h2').filter({hasText:'等待会话'}).waitFor();
    const bindAndWait = async (session,receipt,count) => {
      await page.evaluate(([s,r])=>window.outerBind(s,r),[session,receipt]);
      await page.waitForFunction(expected=>window.outerReads.size>=expected,count);
      return page.evaluate(()=>[...window.outerReads.keys()]);
    };
    const oldA=await bindAndWait('A','first-A',2);
    await bindAndWait('B','middle-B',4);
    const all=await bindAndWait('A','current-A',6);
    for (const id of all.slice(4)) await page.evaluate(id=>window.finishOuter(id,'重新进入 A 的最新任务'),id);
    await frame.locator('#task-title').filter({hasText:'重新进入 A 的最新任务'}).waitFor();
    for (const id of oldA) await page.evaluate(([id,fail])=>window.finishOuter(id,'旧快照残留',fail?'上个会话的迟到错误':null),[id,staleError]);
    await page.waitForFunction(()=>window.receipts.some(r=>r.receipt_id==='first-A'&&r.status==='skipped_session_changed'));
    assert.equal(await page.locator('#panel-status').isVisible(),false);
    await observe(page,frame,'outer-aba-late-'+(staleError?'error':'success'),{task:'重新进入 A 的最新任务'});
    assert.deepEqual(errors,[]);
  } finally { await page.close(); }
}

async function main() {
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'task-progress-read-states-')),stage=path.join(tmp,'stage');
  let browser;
  try {
    if (output) fs.mkdirSync(output,{recursive:true});
    execFileSync('python3',[path.join(plugin,'cindy/prepare.py'),'--stage',stage]);
    const sources=Object.fromEntries([path.join(plugin,'app.html'),path.join(stage,'core/app.html'),path.join(stage,'core/panel-app.html'),path.join(stage,'core/panel-app.js'),path.join(stage,'panel.js')].map(file=>[file.startsWith(stage)?path.relative(stage,file):'shared/app.html',hash(fs.readFileSync(file))]));
    browser=await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL||'chrome',headless:true});
    for (const panel of [false,true]) await runSurface(browser,stage,panel);
    for (const staleError of [false,true]) await runOuterPanel(browser,stage,staleError);
    if (output) fs.writeFileSync(path.join(output,'startup-facts.json'),JSON.stringify({sources,shots,transport:'Intercepted local iframe host messages, no backend service'},null,2)+'\n');
    console.log('Startup and session rebind passed on shared app and generated Cindy panel. '+JSON.stringify(sources));
  } finally {
    await browser?.close();fs.rmSync(tmp,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
