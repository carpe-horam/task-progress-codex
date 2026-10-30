/* Export checks through real app and real save handler; Path.home is isolated in the child process. */
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),readline=require('node:readline');
const {spawn,execFileSync}=require('node:child_process');const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'progress-export-audit-'));
// Direct-source fixture only; the served resource injects the manifest version.
const html=fs.readFileSync(path.join(root,'plugins/task-progress/app.html'),'utf8').replaceAll('__TASK_PROGRESS_VERSION__','test-source');
const pending=new Map();let seq=0,browser;const results=[];
const python=spawn('python3',['-c',`import importlib.util,pathlib,sys
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('audit',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.LEGACY_DB_PATH=pathlib.Path(sys.argv[2])/'progress-v3.sqlite3';m.SESSION_DIR=pathlib.Path(sys.argv[2])/'sessions';m.SESSION_DIR.mkdir()
with patch('pathlib.Path.home',return_value=pathlib.Path(sys.argv[2])):m.main()
`,path.join(root,'plugins/task-progress/server.py'),temp]);
python.stderr.on('data',b=>process.stderr.write(b));
readline.createInterface({input:python.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.id);if(!p)return;pending.delete(r.id);clearTimeout(p.timer);r.error?p.reject(new Error(r.error.message)):p.resolve(r.result);});
const rpc = require('./snapshot_rpc.cjs').snapshotRpc(rawRpc);
function rawRpc(name,args){const id=++seq;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error(name+' timeout')),5000);pending.set(id,{resolve,reject,timer});python.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name,arguments:args}})+'\n');});}
const assignment={execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation',model:'fixture',reasoning_effort:'high'};
async function exportSnapshot(snapshot){
 const page=await browser.newPage({viewport:{width:1000,height:900}});page.setDefaultTimeout(5000);await page.exposeFunction('auditRpc',rawRpc);
 await page.setContent('<iframe id="app" style="width:950px;height:850px"></iframe>');
 await page.evaluate(({snapshot,html})=>{
   addEventListener('message',async e=>{const m=e.data;if(m?.jsonrpc!=='2.0')return;const reply=result=>e.source.postMessage({jsonrpc:'2.0',id:m.id,result},'*');
   if(m.method==='ui/initialize')reply({protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{displayMode:'inline'}});
   if(m.method==='ui/notifications/initialized')e.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:snapshot},'*');
   if(m.method==='tools/call')try{reply(await window.auditRpc(m.params.name,m.params.arguments));}catch(err){e.source.postMessage({jsonrpc:'2.0',id:m.id,error:{code:-32603,message:err.message}},'*');}});
   document.getElementById('app').srcdoc=html;
 },{snapshot,html});
 const f=page.frameLocator('#app');await f.locator('#export-all:not(.hidden)').waitFor();await f.locator('#export-all').click();await f.locator('#export-all:not([disabled])').waitFor();
 const feedback=await f.locator('#export-feedback').textContent();await page.close();return feedback;
}
async function main(){
 browser=await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome',headless:true});
 let r=await rpc('init_task_network',{task_title:'Project root'});const sid=r.structuredContent.session_id;
 await rpc('decompose_task',{session_id:sid,task_id:'root',tasks:[{id:'build','title':'Build leaf',assignment},{id:'test','title':'Test leaf',assignment}]});
 for(const id of ['build','test']){
  r=await rpc('update_task',{session_id:sid,task_id:id,execution_status:'running',assignment});const aid=r.structuredContent.progress.nodes.find(n=>n.id===id).attempt_id;
  r=await rpc('update_task',{session_id:sid,task_id:id,execution_status:'finished',outcome:'resolved',attempt_id:aid});
 }
 const feedback=await exportSnapshot(r),exportDir=path.join(temp,'Downloads','Task Progress Exports');
 const report=await browser.newPage();await report.goto('file://'+path.join(exportDir,fs.readdirSync(exportDir).find(n=>n.endsWith('.html'))));
 results.push({case:'export_attempt_titles',feedback,rows:await report.evaluate(()=>[...document.querySelectorAll('#rp-current .rp-leaf')].map(node=>({
  taskId:node.dataset.nodeId,title:node.querySelector(':scope > [data-task]').textContent,
  attemptCount:node.querySelectorAll(':scope > .r-detail > .rp-task-detail > .rp-node-body .rp-attempt').length
 }))) });
 const workbook=path.join(exportDir,fs.readdirSync(exportDir).find(n=>n.endsWith('.xlsx')));
 const xlsxTitles=JSON.parse(execFileSync('python3',['-c',`import json,sys,zipfile,xml.etree.ElementTree as E
with zipfile.ZipFile(sys.argv[1]) as z:
 assert z.testzip() is None
 doc=E.fromstring(z.read('xl/worksheets/sheet3.xml'))
 ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
 rows=doc.findall('.//s:row',ns)
 headers=[''.join(c.itertext()) for c in rows[0].findall('s:c',ns)]
 title_index=headers.index('任务名称')
 print(json.dumps([''.join(row.findall('s:c',ns)[title_index].itertext()) for row in rows[1:]]))
`,workbook],{encoding:'utf8'}));
 assert.deepEqual(xlsxTitles,['Build leaf','Test leaf']);results[0].xlsxTitles=xlsxTitles;
 await report.close();
 r=await rpc('init_task_network',{task_title:'中'.repeat(47)+'😀'});
 results.push({case:'emoji_title_at_utf16_truncation_boundary',feedback:await exportSnapshot(r)});
 r=await rpc('init_task_network',{task_title:'中文😀正常标题'});
 results.push({case:'ordinary_unicode_title',feedback:await exportSnapshot(r)});
 for(const result of results)assert(result.feedback.startsWith('已导出 Excel'),result.feedback);
 assert.deepEqual(results[0].rows,[{taskId:'build',title:'Build leaf',attemptCount:1},{taskId:'test',title:'Test leaf',attemptCount:1}]);
 console.log(JSON.stringify(results,null,2));
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();python.stdin.end();for(const p of pending.values())clearTimeout(p.timer);fs.rmSync(temp,{recursive:true,force:true});});
