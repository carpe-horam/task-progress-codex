/* Real browser UI and HTTP service, with an isolated simulated MCP Apps host. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {createHost} = require('./browser_host.cjs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'task-progress-http-browser-'));
const state = path.join(root, 'service'), sessions = path.join(root, 'sessions');
fs.mkdirSync(state, {mode:0o700});
const authorization = 'Bearer ' + crypto.randomBytes(48).toString('base64url');
fs.writeFileSync(path.join(state, 'http-headers.json'), JSON.stringify({Authorization:authorization}), {mode:0o600});
let host, service, sequence = 0;
const pids = new Set();
async function main() {
  service = spawn('python3', ['-B', path.resolve(__dirname, '../plugins/task-progress/shared_service.py'),
    '--port', '0', '--state-dir', state, '--session-dir', sessions], {env:{...process.env,HOME:root}});
  service.stderr.on('data', chunk => process.stderr.write(chunk));
  const metadataPath = path.join(state, 'daemon.json');
  for(let i=0; i<150 && !fs.existsSync(metadataPath); i++) {
    if(service.exitCode !== null) throw new Error('Shared service exited');
    await pause(20);
  }
  const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
  const rpcRequest = async (method, params) => {
    const id = ++sequence;
    const response = await fetch(`http://127.0.0.1:${metadata.port}/mcp`, {
      method:'POST', headers:{Authorization:authorization,'Content-Type':'application/json',Accept:'application/json, text/event-stream'},
      body:JSON.stringify({jsonrpc:'2.0',id,method,params}), signal:AbortSignal.timeout(8000)});
    assert.equal(response.status, 200);
    pids.add(Number(response.headers.get('X-Task-Progress-Pid')));
    const message = await response.json();
    assert.equal(message.id, id); assert(!message.error && !message.result?.isError, JSON.stringify(message));
    return message.result;
  };
  host = await createHost({rpcRequest, sessionDir:sessions});
  const [a,b] = await Promise.all(['HTTP Session A','HTTP Session B'].map(task_title=>
    host.rpc('init_task_network',{task_title,atomic_reason:'One isolated UI acceptance operation'})));
  const [pageA,pageB] = await Promise.all([host.page(),host.page()]);
  const frameA = await host.mount(pageA,'main',a);
  const frameB = await host.mount(pageB,'main',b);
  await host.mount(pageA,'tab',a,'fullscreen');
  await frameA.locator('.card.tab-hidden').waitFor({state:'attached'});
  await frameB.locator('.card:not(.tab-hidden):not(.inline-stale)').waitFor({state:'attached'});
  assert(!(await frameB.locator('body').textContent()).includes('HTTP Session A'));
  await host.mode(pageA,'tab','inline');
  await frameA.locator('.card:not(.tab-hidden):not(.inline-stale)').waitFor({state:'attached'});
  const args={session_id:b.structuredContent.session_id,task_instance_id:b.structuredContent.progress.task_instance_id,task_id:'root'};
  const running=await host.rpc('update_task',{...args,execution_status:'running',assignment:{execution_mode:'main_session',self_execution_reason:'Short isolated browser fixture operation'}});
  await host.rpc('update_task',{...args,execution_status:'finished',outcome:'resolved',attempt_id:running.structuredContent.progress.nodes[0].attempt_id,summary:'SESSION_B_ONLY_RESULT'});
  await frameB.getByText('SESSION_B_ONLY_RESULT',{exact:true}).first().waitFor({state:'attached',timeout:12000});
  await frameB.locator('#steps details > summary.step-main').first().click();
  await frameB.locator('#steps .execution-review > summary').first().click();
  await frameB.getByText('SESSION_B_ONLY_RESULT',{exact:true}).first().waitFor();
  assert(!(await frameA.locator('body').textContent()).includes('SESSION_B_ONLY_RESULT'));
  // A closed chat does not own the backend. The surviving chat can still read.
  await pageA.close();
  const fresh=await host.rpc('get_progress_view',{session_id:b.structuredContent.session_id});
  assert.equal(fresh.structuredContent.progress.nodes[0].summary,'SESSION_B_ONLY_RESULT');
  assert.deepEqual([...pids],[service.pid]); assert.deepEqual(host.errors,[]);
  const artifacts=process.env.TASK_PROGRESS_BROWSER_ARTIFACTS;
  const result={ok:true,groups:3,transport:'HTTP',clients:2,server_processes:pids.size,
    checks:['Session and fullscreen isolation','Concurrent update and refresh isolation','Closing one chat leaves shared service available'],
    browser:host.browser.version(),pageErrors:host.errors};
  if(artifacts) {
    fs.mkdirSync(artifacts,{recursive:true});
    await pageB.screenshot({path:path.join(artifacts,'shared-http-session-b.png'),fullPage:true});
    fs.writeFileSync(path.join(artifacts,'shared-http-browser.json'),JSON.stringify(result,null,2));
  }
  console.log(JSON.stringify(result,null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(host)await host.close();
  if(service && service.exitCode===null){service.kill('SIGTERM');await once(service,'exit');}
  fs.rmSync(root,{recursive:true,force:true});
});
