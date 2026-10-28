/* A simulated MCP Apps host forwarding real requests to an isolated server. */
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');

async function createHost({rpcRequest, sessionDir} = {}) {
  const root = path.resolve(__dirname, '..');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-browser-'));
  const pending = new Map();
  let sequence = 0;
  const python = rpcRequest ? null : spawn('python3', ['-c', `import importlib.util,pathlib,sys
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('browser_fixture',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.LEGACY_DB_PATH=pathlib.Path(sys.argv[2])/'progress-v3.sqlite3';m.SESSION_DIR=pathlib.Path(sys.argv[2])/'sessions';m.SESSION_DIR.mkdir()
with patch('pathlib.Path.home',return_value=pathlib.Path(sys.argv[2])):m.main()
`, path.join(root, 'plugins/task-progress/server.py'), temp], {env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
  python?.stderr.on('data', chunk => process.stderr.write(chunk));
  if (python) readline.createInterface({input:python.stdout}).on('line', line => {
    const response = JSON.parse(line), waiter = pending.get(response.id);
    if (!waiter) return;
    pending.delete(response.id); clearTimeout(waiter.timer);
    if (response.error || response.result?.isError) waiter.reject(new Error(JSON.stringify(response)));
    else waiter.resolve(response.result);
  });
  const request = rpcRequest || ((method, params) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(method + ' timed out')); }, 5000);
    pending.set(id, {resolve,reject,timer});
    python.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  }));
  const rpc = (name, args) => request('tools/call', {name,arguments:args});
  // Reading the actual resource also checks manifest-to-app version injection.
  const resource = await request('resources/read', {uri:'ui://task-progress/progress-inspector/v3'});
  const html = resource.contents[0].text;
  const browser = await chromium.launch({channel:process.env.TASK_PROGRESS_BROWSER_CHANNEL || 'chrome',headless:true});
  const errors = [];
  async function page() {
    const page = await browser.newPage({viewport:{width:1150,height:1200}});
    page.setDefaultTimeout(8000);
    page.on('pageerror', error => errors.push(error.message));
    await page.exposeFunction('hostRpc', rpc);
    await page.setContent('<main id="frames"></main>');
    await page.evaluate(() => {
      window.host = {snapshots:{},modes:{},calls:[],sizes:[],initializations:[],holdExecution:null,heldExecution:[]};
      addEventListener('message', async event => {
        const message=event.data;
        if(message?.jsonrpc!=='2.0')return;
        const frame=[...document.querySelectorAll('iframe')].find(f=>f.contentWindow===event.source);
        if(!frame)return;
        const reply=result=>event.source.postMessage({jsonrpc:'2.0',id:message.id,result},'*');
        if(message.method==='ui/initialize'){
          window.host.initializations.push(message.params);
          reply({protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{displayMode:window.host.modes[frame.id]}});
        }
        if(message.method==='ui/notifications/initialized')event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:window.host.snapshots[frame.id]},'*');
        if(message.method==='ui/request-display-mode'){
          reply({displayMode:message.params.mode});
          event.source.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{displayMode:message.params.mode}},'*');
        }
        if(message.method==='ui/notifications/size-changed')window.host.sizes.push({frame:frame.id,...message.params,at:Date.now()});
        if(message.method==='tools/call'){
          const call={frame:frame.id,name:message.params.name,args:message.params.arguments,sentAt:Date.now()};
          window.host.calls.push(call);
          try {
            const rule=window.host.holdExecution;
            if(rule&&frame.id===rule.frame&&message.params.name===rule.name&&message.params.arguments.display_mode===rule.mode){
              call.heldAt=Date.now();window.host.holdExecution=null;
              await new Promise(resolve=>window.host.heldExecution.push(resolve));
            }
            call.executedAt=Date.now();
            const result=await window.hostRpc(message.params.name,message.params.arguments);
            call.result=result;call.completedAt=Date.now();reply(result);
          } catch(error) {
            call.error=error.message;event.source.postMessage({jsonrpc:'2.0',id:message.id,error:{message:error.message}},'*');
          }
        }
      });
    });
    return page;
  }
  async function mount(page,id,snapshot,mode='inline') {
    await page.evaluate(({id,snapshot,mode,html})=>{
      window.host.snapshots[id]=snapshot;window.host.modes[id]=mode;
      const frame=document.createElement('iframe');frame.id=id;frame.style='display:block;width:1100px;height:500px;border:1px dashed gray';frame.srcdoc=html;
      document.getElementById('frames').append(frame);
    },{id,snapshot,mode,html});
    const frame=page.frameLocator('#'+id);
    await frame.locator('#task-title').filter({hasText:snapshot.structuredContent.progress.task_title}).waitFor();
    return frame;
  }
  const mode=(page,id,displayMode)=>page.evaluate(({id,displayMode})=>document.getElementById(id).contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/host-context-changed',params:{displayMode}},'*'),{id,displayMode});
  async function exportReport(snapshot) {
    const hostPage=await page(),frame=await mount(hostPage,'main',snapshot);
    await frame.locator('#export-all').click();
    await frame.locator('#export-feedback').filter({hasText:'已导出 Excel'}).waitFor();
    const call=await hostPage.evaluate(()=>window.host.calls.find(c=>c.name==='save_export_files'));
    const report=await browser.newPage();
    await report.goto('file://'+call.result.structuredContent.files.find(f=>f.name.endsWith('.html')).path);
    return {hostPage,frame,report};
  }
  return {temp,browser,html,rpc,page,mount,mode,exportReport,errors,
    registry:sid=>JSON.parse(fs.readFileSync(path.join(sessionDir || path.join(temp,'sessions'),sid+'.view.json'),'utf8')),
    async close(){await browser.close();python?.stdin.end();python?.kill();for(const item of pending.values())clearTimeout(item.timer);fs.rmSync(temp,{recursive:true,force:true});}};
}
module.exports={createHost};
