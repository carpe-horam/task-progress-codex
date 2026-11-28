'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {harness, sid} = require('./test_cindy_host_archive.cjs');
const k = 'host:' + sid(1);
const event = {type:'event',name:'did-session-archived',data:{sessionId:'host'}};
const pending = () => ({taskProgressSessionsByHostSession:{host:sid(1)},
  keep:{value:42},taskProgressHostArchiveOutbox:{[k]:{version:1,host_session_id:'host',
    session_id:sid(1),observed_at:'2026-11-27T12:00:00.000Z'}}});
const ack = () => ({ok:true,result:{session_id:sid(1),archived:true}});
const gate = () => {let resolve; const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const response = state => ({ok:true,json:async()=>structuredClone(state)});

for (const [name, bad] of [
  ['truthy string ok',{ok:'true',result:{session_id:sid(1),archived:true}}],
  ['truthy numeric ok',{ok:1,result:{session_id:sid(1),archived:true}}],
  ['wrong target',{ok:true,result:{session_id:sid(2),archived:true}}],
  ['string archived',{ok:true,result:{session_id:sid(1),archived:'true'}}],
  ['missing result',{ok:true}],
  ['rejected response',{ok:false,result:{session_id:sid(1),archived:true}}],
]) {
  test('invalid acknowledgment retains pending intent: '+name,{timeout:10000},async()=>{
    let reply=bad;
    const c=harness({}, {host:'active'},{initialKv:pending(),archiveRequest:async()=>reply});
    await c.drain();
    assert(c.getKv().taskProgressHostArchiveOutbox[k],'invalid reply must not authorize cleanup');
    reply=ack(); await c.tick();
    assert.deepEqual(c.getKv().taskProgressHostArchiveOutbox,{});
  });
}

test('failed startup read is retried without sessionStates',{timeout:10000},async()=>{
  let state=pending(),fail=true;
  const c=harness({}, {},{unsupported:true,fetch:async(_url,opts)=>{
    if(fail){fail=false;throw new Error('startup read unavailable');}
    if(opts?.method==='PUT')state=JSON.parse(opts.body);
    return response(state);
  }});
  await c.drain();assert.equal(c.calls.length,0);
  await c.tick();assert.equal(c.calls.length,1);assert.deepEqual(state.taskProgressHostArchiveOutbox,{});
});

test('late startup body does not dispatch until a fresh recovery pass',{timeout:10000},async()=>{
  let state=pending(),first=true;const late=gate();
  const c=harness({}, {},{unsupported:true,quickTimeout:true,fetch:async(_url,opts)=>{
    if(opts?.method==='PUT')state=JSON.parse(opts.body);
    if(first){first=false;return {ok:true,json:()=>late.promise};}
    return response(state);
  }});
  await c.drain();assert.equal(c.calls.length,0);
  late.resolve(structuredClone(state));await new Promise(resolve=>setImmediate(resolve));
  assert.equal(c.calls.length,0);
  await c.tick();assert.equal(c.calls.length,1);assert.deepEqual(state.taskProgressHostArchiveOutbox,{});
});

test('enqueue waits for persistence; queued triggers and serialized rebind cannot bypass it',{timeout:10000},async()=>{
  let state={taskProgressSessionsByHostSession:{host:sid(1)}};
  const started=gate(),release=gate();let delay=true;
  const c=harness({}, {host:'active'},{fetch:async(_url,opts)=>{
    if(opts?.method==='PUT'){
      if(delay){delay=false;started.resolve();await release.promise;}
      state=JSON.parse(opts.body);
    }
    return response(state);
  }});
  await c.drain();await c.dispatch(event);await started.promise;
  const rebind=c.updateKv(value=>({...value,taskProgressSessionsByHostSession:{host:sid(2)}}));
  const check=c.sync();
  assert.equal(c.calls.length,0,'unconfirmed enqueue must not send');
  release.resolve();await rebind;await check;await c.drain();
  assert.equal(c.calls.length,0,'serialized binding repair must be respected before dispatch');
  assert(state.taskProgressHostArchiveOutbox[k]);assert.equal(state.taskProgressSessionsByHostSession.host,sid(2));
});

test('transient enqueue read failure retries the observed event while host is active',{timeout:10000},async()=>{
  let state={taskProgressSessionsByHostSession:{host:sid(1)}},reads=0,failAt=Infinity;
  const c=harness({}, {},{unsupported:true,fetch:async(_url,opts)=>{
    if(opts?.method==='PUT')state=JSON.parse(opts.body);
    else if(++reads===failAt)throw new Error('enqueue read failed before PUT');
    return response(state);
  }});
  await c.drain();failAt=reads+2;
  await c.dispatch(event);await c.drain();assert.equal(c.calls.length,0);
  await c.tick();assert.equal(c.calls.length,1);assert.deepEqual(state.taskProgressHostArchiveOutbox,{});
});

test('cleanup committed before a late reply is not resent and recovers empty on restart',{timeout:10000},async()=>{
  let state=pending();const started=gate(),release=gate();let hold=true;
  const c=harness({}, {host:'active'},{fetch:async(_url,opts)=>{
    if(opts?.method==='PUT'){
      state=JSON.parse(opts.body);
      if(hold){hold=false;started.resolve();await release.promise;}
    }
    return response(state);
  }});
  await started.promise;await c.drain(); // Existing KV writer times out and quarantines later writes.
  assert.equal(c.calls.length,1);assert.deepEqual(state.taskProgressHostArchiveOutbox,{});
  await c.tick();assert.equal(c.calls.length,1,'uncertain cleanup must not resend');
  release.resolve();await new Promise(resolve=>setImmediate(resolve));
  const restarted=harness({}, {host:'active'},{initialKv:state});await restarted.drain();
  assert.equal(restarted.calls.length,0);assert.deepEqual(restarted.getKv().keep,{value:42});
});

test('invalid queue can recover after repair without losing unrelated fields',{timeout:10000},async()=>{
  let state=pending();state.taskProgressHostArchiveOutbox[k].version=2;let writes=0;
  const c=harness({}, {},{unsupported:true,fetch:async(_url,opts)=>{
    if(opts?.method==='PUT'){writes++;state=JSON.parse(opts.body);}
    return response(state);
  }});
  await c.drain();assert.equal(c.calls.length,0);assert.equal(writes,0);
  state.taskProgressHostArchiveOutbox[k].version=1;
  await c.tick();assert.equal(c.calls.length,1);assert.deepEqual(state.keep,{value:42});
});

test('blocked old binding cannot starve another valid persisted intent',{timeout:10000},async()=>{
  const state=pending(),other='other:'+sid(2);
  state.taskProgressSessionsByHostSession={host:sid(3),other:sid(2)};
  state.taskProgressHostArchiveOutbox[other]={version:1,host_session_id:'other',session_id:sid(2),observed_at:'2026-11-27T12:00:01Z'};
  const c=harness({}, {},{unsupported:true,initialKv:state});await c.drain();
  assert.deepEqual(c.calls.map(x=>x.params.session_id),[sid(2)]);
  assert(c.getKv().taskProgressHostArchiveOutbox[k]);assert(!c.getKv().taskProgressHostArchiveOutbox[other]);
});
