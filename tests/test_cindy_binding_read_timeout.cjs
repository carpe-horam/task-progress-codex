'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  let receive, backendCalls = 0;
  const replies = [], lateBodies = [];
  const context = {
    BroadcastChannel: class { postMessage() {} },
    cindy: {
      onHostMessage(fn) { receive = fn; },
      node: { async request() { backendCalls++; throw new Error('unexpected backend call'); } },
      async send(message) { replies.push(message); },
    },
    fetch: async () => ({ok:true, json:() => new Promise(resolve => lateBodies.push(resolve))}),
    setTimeout: (fn, ms) => setTimeout(fn, ms === 3000 ? 5 : ms), clearTimeout,
  };
  vm.runInNewContext(fs.readFileSync('plugins/task-progress/cindy/main.js','utf8'), context);
  for (const [id, tool, args] of [
    ['init','init_task_network',{task_title:'new'}],
    ['update','update_task',{session_id:'task-session',task_id:'root',execution_status:'running'}],
  ]) {
    await receive({type:'tool-call',callId:id,tool,args:{...args,session_context:{session_id:'host-session'}}});
    const reply = replies.find(item => item.callId === id);
    assert.equal(reply?.ok,false,`${tool} must fail when the binding body never arrives`);
    assert.match(reply.message,/当前.*绑定/);
  }
  assert.equal(backendCalls,0,'timed-out binding checks must not reach the task backend');
  for (const resolve of lateBodies) resolve({taskProgressSessionsByHostSession:{'host-session':'task-session'}});
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(backendCalls,0,'late binding responses must not start operations');
  console.log('Binding timeout: initialization and update reject stalled reads; late responses do not dispatch.');
})().catch(error => {console.error(error);process.exitCode=1;});
