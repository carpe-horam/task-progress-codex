/* Visual interpolation of reported progress; projected snapshots isolate UI timing. */
'use strict';
const assert=require('node:assert/strict');
const {createHost}=require('./browser_host.cjs');
let host;
(async()=>{
 host=await createHost();
 const assignment={execution_mode:'main_session',self_execution_reason:'Isolated UI fixture'};
 const init=await host.rawRpc('init_task_network',{task_title:'进度动效预览',tasks:['a','b','c','d'].map(id=>({id,title:id,assignment}))});
 const original=await host.rawRpc('get_progress_view',{session_id:init.structuredContent.session_id});
 const page=await host.page();await page.setViewportSize({width:430,height:800});
 await page.addStyleTag({content:'html,body{margin:0;padding:0}iframe{width:100vw!important;height:100vh!important;border:0!important}'});
 const frame=await host.mount(page,'main',original,'fullscreen');
 const sample=()=>frame.locator('#fill').evaluate(e=>({value:parseFloat(e.style.width),number:Number(document.getElementById('percent').textContent),aria:Number(document.getElementById('progressbar').getAttribute('aria-valuenow')),moving:e.querySelector('.progress-sheen').getAnimations().some(a=>a.playState==='running')}));
 let delivery=0;
 const push=async(value,epoch=original.structuredContent.progress.epoch)=>{
  const snapshot=JSON.parse(JSON.stringify(original));
  Object.assign(snapshot.structuredContent.progress,{accepted_resolved_percent:value,accepted_resolved_leaf_count:Math.round(value/25),epoch});
  await page.evaluate(({snapshot,delivery})=>new Promise(resolve=>{
   const app=document.getElementById('main').contentWindow;
   const received=event=>{if(event.data?.testDelivery!==delivery)return;app.removeEventListener('message',received);app.requestAnimationFrame(()=>app.requestAnimationFrame(resolve));};
   app.addEventListener('message',received);app.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot,testDelivery:delivery},'*');
  }),{snapshot,delivery:++delivery});
 };
 const finished=async value=>{await frame.locator('#fill').evaluate((e,v)=>new Promise(resolve=>{const check=()=>Math.abs(parseFloat(e.style.width)-v)<.01?resolve():requestAnimationFrame(check);check()}),value)};
 assert.equal((await sample()).value,0);
 await push(75);await page.waitForTimeout(140);
 const mid=await sample();assert(mid.value>0&&mid.value<75);assert.equal(mid.number,Math.round(mid.value));assert.equal(mid.aria,75);assert(mid.moving);
 await push(75);const repeated=await sample();assert(repeated.value>=mid.value,'unchanged refresh does not restart tween');
 await push(100);const retarget=await sample();assert(retarget.value>=repeated.value&&retarget.value<100,'retarget continues from displayed value');
 await finished(100);assert.equal((await sample()).number,100);
 await page.waitForTimeout(950);assert.equal((await sample()).moving,false,'no idle animation');
 await push(25);await page.waitForTimeout(100);assert((await sample()).value<100,'decreases animate too');
 await page.emulateMedia({reducedMotion:'reduce'});await push(50);assert.equal((await sample()).value,50);assert.equal((await sample()).moving,false);
 await page.emulateMedia({reducedMotion:'no-preference'});await push(25,999);assert.equal((await sample()).value,25,'new plan does not animate from previous plan');
 await push(75,999);await page.waitForTimeout(100);
 await page.evaluate(()=>document.getElementById('main').contentWindow.postMessage({jsonrpc:'2.0',id:'motion-teardown',method:'ui/resource-teardown'},'*'));
 await page.waitForTimeout(60);const stopped=await sample();await page.waitForTimeout(100);assert.equal((await sample()).value,stopped.value);assert.equal((await sample()).moving,false);
 assert.deepEqual(host.errors,[]);
 console.log('PASS: synchronized interpolation, retarget, unchanged refresh, decreases, ARIA target, reduced motion, plan switch, idle and teardown');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(host)await host.close()});
