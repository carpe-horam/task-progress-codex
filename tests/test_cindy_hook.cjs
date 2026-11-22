const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../plugins/task-progress/cindy/main.js'), 'utf8');

function harness(initialKv = {}, nodeResult = null, failures = {}) {
  let handler;
  let kv = structuredClone(initialKv);
  const sent = [];
  const calls = [];
  const broadcasts = [];
  let bus;
  const sandbox = {
    BroadcastChannel: class { constructor() { bus = this; } postMessage(value) { broadcasts.push(value); } },
    fetch: async (_url, options) => {
      if (failures.beforeFetch) await failures.beforeFetch(options);
      if (failures.hangRead && options?.method !== 'PUT') return new Promise(()=>{});
      if (failures.kv) return { ok: false };
      if (options?.method === 'PUT' && failures.beforePutResult) {
        const result = await failures.beforePutResult();
        if (result) return result;
      }
      if (options?.method === 'PUT') kv = JSON.parse(options.body);
      if (failures.afterFetch) return (await failures.afterFetch(options)) || { ok: true, json: async () => kv };
      return { ok: true, json: async () => kv };
    },
    cindy: {
      onHostMessage: callback => { handler = callback; },
      ...(failures.taskView ? { taskView: failures.taskView } : {}),
      node: { request: async request => { calls.push(request); if (failures.node) throw new Error('private backend details'); return typeof nodeResult === 'function' ? nodeResult(request) : nodeResult; } },
      send: value => { if (failures.send) throw new Error('private transport details'); sent.push(value); },
    },
    AbortController, Date, Map, Object, JSON, setTimeout, clearTimeout,
  };
  vm.runInNewContext(source, sandbox);
  return { dispatch: event => handler(event), deliverPanel: value => bus.onmessage({ data: value }),
    sent, calls, broadcasts, getKv: () => kv, updateKv: (change, options) => sandbox.updatePluginKv(change, options),
    recordHookReceipt: (msg, options) => sandbox.recordHookReceipt(msg, options),
    waitForPluginKvWrites: () => sandbox.waitForPluginKvWrites(),
    readReviewState: (...args) => sandbox.readReviewState(...args),
    syncPanelFocus: () => sandbox.syncPanelFocus() };
}

const session = '00000000-0000-0000-0000-000000000001';
const instance = 'instance-1';
const current = { taskProgressSessionsByHostSession: {host:session}, taskProgressContextsByHostSession: { host: { session_id: session, task_instance_id: instance } } };
const overview = { ok: true, result: { structuredContent: {
  view: 'overview', session_id: session, task_instance_id: instance, state_revision: 4,
  progress: { schema_version: 2, task_instance_id: instance, epoch: 1,
    planning_status: 'ready', pending_review_count: 1, stage_inventory_due_count: 1 }
} } };
const event = { type: 'event', name: 'will-compose-context', hookId: 'hook-1', data: { sessionId: 'host' } };

(async () => {
  {
    let release;
    const client=harness({},null,{afterFetch:async()=>({ok:true,json:()=>new Promise(resolve=>{release=resolve;})})});
    await client.dispatch({type:'tool-call',tool:'get_hook_diagnostics',callId:'diagnostic-read-timeout',args:{session_context:{session_id:'host'}}});
    const reply=client.sent.filter(item=>item.callId==='diagnostic-read-timeout');
    assert.equal(reply.length,1);
    assert.equal(reply[0].errorCode,'HOOK_DIAGNOSTICS_UNAVAILABLE');
    release({taskProgressHookDiagnostics:{host:{events:{old:{received:1}}}}});
    await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(client.sent.filter(item=>item.callId==='diagnostic-read-timeout').length,1);
  }
  await assert.rejects(harness({},null,{kv:true}).readReviewState(50),/验收设置暂不可读取/);
  {
    let client;
    client=harness({nested:{value:'stored'}},null,{afterFetch:async()=>({ok:true,json:async()=>structuredClone(client.getKv())})});
    const unchanged=await client.updateKv(current=>{current.nested.value='uncommitted';return null;},{reportChange:true});
    assert.equal(unchanged.changed,false);
    assert.equal(unchanged.previous.nested.value,'stored');
    assert.equal(unchanged.value.nested.value,'stored');
    assert.equal(client.getKv().nested.value,'stored');
    const committed=await client.updateKv(current=>{current.nested.value='committed';return current;},{reportChange:true});
    assert.equal(committed.changed,true);
    assert.equal(committed.previous.nested.value,'stored');
    assert.equal(committed.value.nested.value,'committed');
    assert.equal(client.getKv().nested.value,'committed');
  }
  let failFocusRead = true;
  const focusKvFailure = harness({taskProgressActiveHostSessionId:'old-host'}, null, {
    taskView:async()=>({ok:true,sessionId:'new-host'}),
    beforeFetch:()=>{if(failFocusRead){failFocusRead=false;throw new Error('temporary read failure');}},
  });
  await focusKvFailure.syncPanelFocus();
  assert.equal(focusKvFailure.getKv().taskProgressActiveHostSessionId,'old-host','KV failure must not be mistaken for a lost host focus');
  assert.equal(focusKvFailure.broadcasts.length,0);
  await focusKvFailure.syncPanelFocus();
  assert.equal(focusKvFailure.getKv().taskProgressActiveHostSessionId,'new-host','next successful focus sync uses the confirmed host');
  assert.equal(focusKvFailure.broadcasts.length,1);
  const focusPeekFailure = harness({taskProgressActiveHostSessionId:'old-host'}, null, {taskView:async()=>{throw new Error('host unavailable');}});
  await focusPeekFailure.syncPanelFocus();
  assert.equal(focusPeekFailure.getKv().taskProgressActiveHostSessionId,'','a failed host lookup still clears a stale focus');
  assert.equal(focusPeekFailure.broadcasts[0].session_id,null);
  let releasePeek,peeks=0;
  const focusPeekTimeout=harness({taskProgressActiveHostSessionId:'old-host'},null,{taskView:()=>{
    if(++peeks===1)return new Promise(resolve=>{releasePeek=resolve;});
    return Promise.resolve({ok:true,sessionId:'new-host'});
  }});
  await focusPeekTimeout.syncPanelFocus();
  assert.equal(focusPeekTimeout.getKv().taskProgressActiveHostSessionId,'old-host','timeout alone does not change focus');
  await focusPeekTimeout.syncPanelFocus();
  assert.equal(focusPeekTimeout.getKv().taskProgressActiveHostSessionId,'new-host','a subsequent poll can recover');
  releasePeek({ok:true,sessionId:'stale-host'});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(focusPeekTimeout.getKv().taskProgressActiveHostSessionId,'new-host','a late peek cannot overwrite the recovered focus');
  let finishPeek;
  const focusSwitchRace=harness({taskProgressActiveHostSessionId:'old-host'},null,{taskView:()=>new Promise(resolve=>{finishPeek=resolve;})});
  const oldPeek=focusSwitchRace.syncPanelFocus();
  await new Promise(resolve=>setTimeout(resolve,0));
  await focusSwitchRace.dispatch({type:'event',name:'did-session-switched',data:{sessionId:'new-host'}});
  finishPeek({ok:true,sessionId:'old-host'});
  await oldPeek;
  assert.equal(focusSwitchRace.getKv().taskProgressActiveHostSessionId,'new-host','an older peek cannot undo a switch event');
  const receiptEvent = {name:'did-task-state-changed',data:{sessionId:'host'}};
  const receiptHarness = harness({...current,taskProgressActiveHostSessionId:'host'});
  const refreshTarget = await receiptHarness.recordHookReceipt(receiptEvent,{refresh:true});
  assert.equal(refreshTarget.session_id,session);
  assert.equal(receiptHarness.getKv().taskProgressHookDiagnostics.host.pending[0].receipt_id,refreshTarget.receipt_id);
  const fullDiagnostics = Object.fromEntries(['host',...Array.from({length:16},(_,i)=>`other-${i}`)].map(id=>[id,{events:{},pending:[]}]));
  const evictedReceipt = harness({...current,taskProgressActiveHostSessionId:'host',taskProgressHookDiagnostics:fullDiagnostics});
  assert.equal(await evictedReceipt.recordHookReceipt(receiptEvent,{refresh:true}),null,'an evicted receipt must not trigger a refresh');
  assert.equal(evictedReceipt.getKv().taskProgressHookDiagnostics.host,undefined);
  // A different runtime can claim the binding while initialization is in
  // flight. The returned task identity must not be reported as saved.
  let releaseInitialization, initializationStarted;
  const initializationGate = new Promise(resolve => { releaseInitialization = resolve; });
  const initializationEntered = new Promise(resolve => { initializationStarted = resolve; });
  const bindingRace = harness({}, async () => {
    initializationStarted();
    await initializationGate;
    return {ok:true,result:{content:[],structuredContent:{session_id:session,task_instance_id:instance}}};
  });
  const initializing = bindingRace.dispatch({type:'tool-call',tool:'init_task_network',callId:'init-race',args:{session_context:{session_id:'host'}}});
  await initializationEntered;
  await bindingRace.updateKv(value => ({...value,taskProgressSessionsByHostSession:{host:'other-session'}}));
  releaseInitialization();
  await initializing;
  assert.equal(bindingRace.getKv().taskProgressSessionsByHostSession.host,'other-session');
  const initialized = bindingRace.sent.find(item => item.callId === 'init-race');
  assert.equal(initialized.ok,true);
  assert.match(initialized.result.content[0].text,/绑定未能确认保存/);
  await bindingRace.dispatch({type:'tool-call',tool:'init_task_network',callId:'init-again',args:{session_context:{session_id:'host'}}});
  assert.equal(bindingRace.sent.find(item => item.callId === 'init-again').ok,false);
  assert.equal(bindingRace.calls.length,1,'a conflicting unbound initialization must not be repeated');
  const repairManifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../plugins/task-progress/cindy/ghost.json'), 'utf8'))
    .tools.find(tool => tool.name === 'repair_session_binding');
  assert.doesNotMatch(repairManifest.description,/原子切换/);
  assert.match(repairManifest.description,/不保证跨实例原子/);
  const repair = harness(current,overview);
  await repair.dispatch({type:'tool-call',tool:'repair_session_binding',callId:'repair',args:{host_session_id:'host',expected_session_id:session,session_id:session,task_instance_id:instance,session_context:{session_id:'host'}}});
  const repairResult = repair.sent.find(item => item.callId === 'repair');
  assert.equal(repairResult.ok,true);
  assert.equal(repairResult.result.structuredContent.cross_instance_atomic,false);
  assert.match(repairResult.result.content[0].text,/不保证跨实例原子/);
  const mutationReceipt = harness({ marker:'existing' });
  const unchanged = await mutationReceipt.updateKv(() => null, {reportChange:true});
  assert.equal(unchanged.changed,false);
  assert.equal(unchanged.value.marker,'existing');
  const changed = await mutationReceipt.updateKv(value => ({...value, marker:'committed'}), {reportChange:true});
  assert.equal(changed.changed,true);
  assert.equal(changed.value.marker,'committed');
  assert.equal(mutationReceipt.getKv().marker,'committed');
  const matched = harness(current, overview);
  await matched.dispatch(event);
  assert.equal(matched.sent[0].action, 'context');
  assert.match(matched.sent[0].blocks[0].text, /待主会话验收/);
  assert.match(matched.sent[0].blocks[0].text, /不代表用户原话/);
  assert.deepEqual(JSON.parse(JSON.stringify(matched.calls[0].params)), {
    name: 'peek_hook_overview', arguments: {session_id: session, task_instance_id: instance}
  });
  assert.equal(matched.getKv().taskProgressHookDiagnostics.host.events['will-compose-context'].received, 1);
  assert.equal(matched.getKv().taskProgressHookDiagnostics.host.events['will-compose-context'].last.outcome, 'received');

  const simultaneous = harness(current,{ok:true,result:{structuredContent:{...overview.result.structuredContent,
    progress:{...overview.result.structuredContent.progress,planning_status:'required',synthesis_ready_count:1,synthesis_ready_ids:['package-a'],closure_review_due:true}}}});
  await simultaneous.dispatch(event);
  for(const expected of ['尚需完成','待主会话验收','package-a','阶段盘点','根任务收尾']) assert(simultaneous.sent[0].blocks[0].text.includes(expected));

  const synthesis = harness(current, {ok:true,result:{structuredContent:{...overview.result.structuredContent,
    progress:{...overview.result.structuredContent.progress,pending_review_count:0,stage_inventory_due_count:0,synthesis_ready_count:1,synthesis_ready_ids:['package-a']}}}});
  await synthesis.dispatch(event);
  assert.equal(synthesis.sent[0].action,'context');
  assert.match(synthesis.sent[0].blocks[0].text,/package-a.*综合验收/);
  const noSynthesis = harness(current, {ok:true,result:{structuredContent:{...overview.result.structuredContent,
    progress:{...overview.result.structuredContent.progress,pending_review_count:0,stage_inventory_due_count:0,synthesis_ready_count:0,synthesis_ready_ids:[]}}}});
  await noSynthesis.dispatch(event);
  assert.equal(noSynthesis.sent[0].action,'allow');
  const staleSynthesis = harness(current, {ok:true,result:{structuredContent:{...overview.result.structuredContent,task_instance_id:'foreign',
    progress:{...overview.result.structuredContent.progress,pending_review_count:0,stage_inventory_due_count:0,synthesis_ready_count:1,synthesis_ready_ids:['package-a']}}}});
  await staleSynthesis.dispatch(event);
  assert.equal(staleSynthesis.sent[0].action,'allow');

  const unknown = harness({}, overview);
  await unknown.dispatch(event);
  assert.equal(unknown.sent[0].action, 'context');
  assert.equal(unknown.sent[0].blocks[0].id, 'task-progress-startup');
  assert.match(unknown.sent[0].blocks[0].text, /init_task_network/);
  assert.match(unknown.sent[0].blocks[0].text, /复用原实例/);
  assert.match(unknown.sent[0].blocks[0].text, /无需建树/);
  assert.equal(unknown.calls.length, 0);

  const wrongBinding=harness({...current,taskProgressSessionsByHostSession:{host:'other-session'}},overview);
  await wrongBinding.dispatch(event);
  assert.equal(wrongBinding.sent[0].action,'allow');
  assert.equal(wrongBinding.calls.length,0,'stale context must not query another session');

  const hungSignals=[];
  const hung=harness(current,overview,{hangRead:true,beforeFetch:options=>{if(options?.signal)hungSignals.push(options.signal);}});
  const hungDispatch=hung.dispatch(event);
  await new Promise(resolve=>setTimeout(resolve,2350));
  assert.equal(hung.sent[0].action,'allow','unavailable KV cannot hold the host hook');
  assert.ok(hungSignals.some(signal=>signal.aborted),'stalled hook reads must be aborted');

  const stale = harness(current, { ok: true, result: { structuredContent: {
    ...overview.result.structuredContent, task_instance_id: 'another-instance' } } });
  await stale.dispatch(event);
  assert.equal(stale.sent[0].action, 'allow');

  // Every RPC failure, including an archived instance, leaves the exact
  // binding alone. The hook cannot infer the intended foreground instance.
  for (const failure of [
    {ok:true,result:{isError:true,content:[{type:'text',text:'旧实例已归档'}]}},
    {ok:true,result:{isError:true,content:[{type:'text',text:'temporary storage failure'}]}},
    {ok:true,result:{result:{isError:true}}},
    {ok:false}
  ]) {
    const unavailable = harness(current, failure);
    await unavailable.dispatch(event);
    assert.equal(unavailable.calls.length,1);
    assert.equal(unavailable.getKv().taskProgressContextsByHostSession.host.task_instance_id,instance);
    assert.equal(unavailable.sent[0].action,'allow');
  }

  let release;
  const blockedRead = new Promise(resolve => { release = resolve; });
  const race = harness(current, async request => {
    if (request.params.name === 'clear_progress') return {ok:true,result:{structuredContent:{session_id:session}}};
    await blockedRead;
    return overview;
  });
  const pending = race.dispatch(event);
  await new Promise(resolve=>setImmediate(resolve));
  await race.dispatch({type:'tool-call',tool:'clear_progress',callId:'clear',
    args:{session_id:session,session_context:{session_id:'host'}}});
  release();
  await pending;
  assert.equal(race.getKv().taskProgressContextsByHostSession.host,undefined);
  assert.equal(race.sent.find(item=>item.type==='event-verdict').action,'allow');
  assert.equal(race.getKv().taskProgressHookDiagnostics.host.events[event.name].last_guidance.reason,'binding_changed');

  // Hold the receipt's KV read past its deadline. A clear must finish even
  // while the transport has not yet delivered the diagnostic read.
  let releaseReceipt;
  const blockedReceipt = new Promise(resolve=>{releaseReceipt=resolve;});
  let holdReceipt = true;
  const interrupted = harness(current, {ok:true,result:{structuredContent:{session_id:session}}}, {
    beforeFetch: options => {
      if (options?.signal && holdReceipt) { holdReceipt = false; return blockedReceipt; }
    }
  });
  const pendingReceipt = interrupted.dispatch({...event,name:'did-task-state-changed'});
  await new Promise(resolve=>setImmediate(resolve));
  const clear = interrupted.dispatch({type:'tool-call',tool:'clear_progress',callId:'critical-clear',
    args:{session_id:session,session_context:{session_id:'host'}}});
  let deadline;
  try {
    await Promise.race([clear, new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('clear blocked by diagnostics')),1500);})]);
  } finally { clearTimeout(deadline); releaseReceipt(); }
  await pendingReceipt;
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(interrupted.sent.at(-1).ok,true);
  assert.equal(interrupted.getKv().taskProgressContextsByHostSession.host,undefined);
  assert.equal(interrupted.getKv().taskProgressHookDiagnostics,undefined);

  let putStarted = false, releasePut;
  const delayedPut = new Promise(resolve => { releasePut = resolve; });
  const interruptedPut = harness(current, {ok:true,result:{structuredContent:{session_id:session}}}, {
    beforeFetch: options => {
      if (options?.method === 'PUT' && !putStarted) {
        putStarted = true;
        return delayedPut;
      }
    }
  });
  const writing = interruptedPut.dispatch({...event,name:'did-task-state-changed'});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(putStarted,true);
  const clearing = interruptedPut.dispatch({type:'tool-call',tool:'clear_progress',callId:'critical-clear',
    args:{session_id:session,session_context:{session_id:'host'}}});
  await new Promise(resolve=>setTimeout(resolve,650));
  assert.equal(interruptedPut.sent.some(item=>item.callId==='critical-clear'),false);
  releasePut();
  await clearing;
  await writing;
  assert.equal(interruptedPut.sent.at(-1).ok,true);
  assert.equal(interruptedPut.getKv().taskProgressContextsByHostSession.host,undefined);

  // A serialization error occurs before fetch is called and must not be
  // mistaken for a potentially committed PUT.
  let serializedPuts = 0;
  const serializationFailure = harness(current, overview, {beforeFetch: options => {
    if (options?.method === 'PUT') serializedPuts++;
  }});
  const cyclic = {}; cyclic.self = cyclic;
  await assert.rejects(serializationFailure.updateKv(() => cyclic), /circular/i);
  await assert.rejects(serializationFailure.updateKv(() => undefined), /Invalid Task Progress plugin state/);
  let asyncInvoked = false;
  await assert.rejects(serializationFailure.updateKv(async () => { asyncInvoked = true; return { unsafe:true }; }), /synchronous/i);
  assert.equal(asyncInvoked, false);
  await assert.rejects(serializationFailure.updateKv(() => Promise.resolve({ unsafe:true })), /synchronous/i);
  await assert.rejects(serializationFailure.updateKv(() => Promise.reject(new Error('late change failure'))), /synchronous/i);
  await assert.rejects(serializationFailure.updateKv(() => new Map([['binding', 'lost']])), /Invalid Task Progress plugin state/);
  await assert.rejects(serializationFailure.updateKv(function* () { yield { binding:'lost' }; }), /Invalid Task Progress plugin state/);
  assert.equal(serializedPuts, 0);
  await serializationFailure.updateKv(value => ({...value, recoveredAfterSerialization:true}));
  assert.equal(serializationFailure.getKv().recoveredAfterSerialization, true);
  assert.equal(serializedPuts, 1);

  // A successful GET with a non-object body is not evidence of empty state.
  for (const invalid of [null, [], 'invalid', 42]) {
    let puts = 0, changed = false;
    const malformedRead = harness(invalid, overview, {beforeFetch: options => { if (options?.method === 'PUT') puts++; }});
    await assert.rejects(malformedRead.updateKv(() => { changed = true; return {lost:true}; }), /Invalid Task Progress plugin state/);
    assert.equal(changed, false);
    assert.equal(puts, 0);
  }
  let transformedPuts = 0;
  const transformed = harness({}, overview, {beforeFetch: options => { if (options?.method === 'PUT') transformedPuts++; }});
  await assert.rejects(transformed.updateKv(() => ({toJSON: () => null})), /Invalid Task Progress plugin state/);
  assert.equal(transformedPuts, 0);
  const actual = await transformed.updateKv(() => ({label:'before', toJSON: () => ({label:'saved'})}), {reportChange:true});
  assert.equal(actual.value.label, 'saved');
  assert.equal(transformed.getKv().label, 'saved');
  assert.equal(transformedPuts, 1);

  // Only a host's explicit sealed receipt proves this PUT could not commit.
  let rejectedPuts = 0;
  const sealed = harness(current, overview, {beforePutResult: () => {
    if (++rejectedPuts === 1) return {ok:false, status:409, json:async()=>({error:'LEGACY_KV_SEALED'})};
    return null;
  }});
  await assert.rejects(sealed.updateKv(value => ({...value, rejected:true})), /state update failed/);
  await sealed.updateKv(value => ({...value, continued:true}));
  assert.equal(sealed.getKv().rejected,undefined);
  assert.equal(sealed.getKv().continued,true);
  assert.equal(rejectedPuts,2);

  // A host confirmation after the inner deadline but before the caller's
  // deadline is still a confirmed write, not a failed one.
  let releaseConfirmed,signalConfirmed;
  const enteredConfirmed=new Promise(resolve=>{signalConfirmed=resolve;});
  const confirmedGate=new Promise(resolve=>{releaseConfirmed=resolve;});
  const delayedConfirmation=harness({marker:'old'},null,{beforePutResult:async()=>{
    signalConfirmed();await confirmedGate;return null;
  }});
  const confirmation=delayedConfirmation.updateKv(value=>({...value,marker:'saved'}),{diagnostic:true,reportChange:true});
  await enteredConfirmed;
  await new Promise(resolve=>setTimeout(resolve,650));
  await assert.rejects(delayedConfirmation.updateKv(value=>({...value,unsafe:true})),/awaiting confirmation/);
  releaseConfirmed();
  const confirmedResult=await confirmation;
  assert.equal(confirmedResult.changed,true);
  assert.equal(confirmedResult.previous.marker,'old');
  assert.equal(confirmedResult.value.marker,'saved');
  assert.equal(delayedConfirmation.getKv().marker,'saved');

  // A late, explicit rejection releases the quarantine only after the host's
  // receipt arrives; a request made while it is pending must not overtake it.
  let releaseSealed, signalSealed, sealedPuts = 0;
  const sealedPending = new Promise(resolve => { signalSealed = resolve; });
  const sealedGate = new Promise(resolve => { releaseSealed = resolve; });
  const lateSealed = harness(current, overview, {beforePutResult: async () => {
    if (++sealedPuts === 1) {
      signalSealed();
      await sealedGate;
      return {ok:false, status:409, json:async()=>({error:'LEGACY_KV_SEALED'})};
    }
    return null;
  }});
  const lateRejected = lateSealed.updateKv(value => ({...value, rejected:true}), {diagnostic:true}).catch(() => {});
  await sealedPending;
  await new Promise(resolve=>setTimeout(resolve,650));
  await assert.rejects(lateSealed.updateKv(value => ({...value, unsafe:true})), /awaiting confirmation/);
  assert.equal(sealedPuts,1);
  releaseSealed();
  await lateRejected;
  await lateSealed.updateKv(value => ({...value, recovered:true}));
  assert.equal(lateSealed.getKv().rejected,undefined);
  assert.equal(lateSealed.getKv().unsafe,undefined);
  assert.equal(lateSealed.getKv().recovered,true);

  // A definitive rejection can also arrive after the caller's deadline. It
  // proves no write was committed, so the next operation may safely proceed.
  let releaseLateRejection, signalLateRejection, lateRejectionPuts=0;
  const rejectionEntered=new Promise(resolve=>{signalLateRejection=resolve;});
  const rejectionGate=new Promise(resolve=>{releaseLateRejection=resolve;});
  const rejectedAfterTimeout=harness({counter:0},null,{beforePutResult:async()=>{
    if (++lateRejectionPuts===1) {
      signalLateRejection();await rejectionGate;
      return {ok:false,status:409,json:async()=>({error:'LEGACY_KV_SEALED'})};
    }
    return null;
  }});
  const rejectedCall=rejectedAfterTimeout.updateKv(value=>({...value,counter:value.counter+1}),{diagnostic:true});
  await rejectionEntered;
  await assert.rejects(rejectedCall,/timed out/);
  releaseLateRejection();
  await new Promise(resolve=>setImmediate(resolve));
  await rejectedAfterTimeout.updateKv(value=>({...value,counter:value.counter+1}));
  assert.equal(rejectedAfterTimeout.getKv().counter,1);

  // Conversely a late success must not unlock a duplicate increment even
  // though the host has already applied the original request.
  let releaseLateSuccess, signalLateSuccess;
  const successEntered=new Promise(resolve=>{signalLateSuccess=resolve;});
  const successGate=new Promise(resolve=>{releaseLateSuccess=resolve;});
  const committedAfterTimeout=harness({counter:0,taskProgressAutoReview:{host:{enabled:true}}},null,{beforePutResult:async()=>{
    signalLateSuccess();await successGate;return null;
  }});
  const committedCall=committedAfterTimeout.updateKv(value=>({...value,counter:value.counter+1}),{diagnostic:true});
  await successEntered;
  await assert.rejects(committedCall,/timed out/);
  releaseLateSuccess();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(committedAfterTimeout.getKv().counter,1);
  await assert.rejects(committedAfterTimeout.updateKv(value=>({...value,counter:value.counter+1})),/reconciliation required/);
  assert.equal(committedAfterTimeout.getKv().counter,1);
  await committedAfterTimeout.dispatch({type:'event',name:'did-session-switched',data:{sessionId:'host'}});
  assert.equal(committedAfterTimeout.broadcasts.some(item=>item.type==='auto-review-state'),false);
  const healthySwitch=harness({taskProgressAutoReview:{host:{enabled:true}}});
  await healthySwitch.dispatch({type:'event',name:'did-session-switched',data:{sessionId:'host'}});
  assert.equal(healthySwitch.broadcasts.some(item=>item.type==='auto-review-state' && item.status==='association_unverified'),true);

  // A PUT accepted by the host may never receive an acknowledgement. Reject
  // later updates promptly and never replay a caller that timed out in queue.
  let releaseUncertain, firstPut = true;
  const missingAck = new Promise(resolve => { releaseUncertain = resolve; });
  const uncertain = harness(current, overview, {beforeFetch: options => {
    if (options?.method === 'PUT' && firstPut) { firstPut = false; return missingAck; }
  }});
  const pendingDiagnostic = uncertain.updateKv(value => ({...value, marker:'old'}), {diagnostic:true}).catch(() => {});
  await new Promise(resolve=>setImmediate(resolve));
  const queued = uncertain.updateKv(value => ({...value, shouldNotAppear:true})).catch(() => {});
  await new Promise(resolve=>setTimeout(resolve,650));
  await assert.rejects(uncertain.updateKv(value => ({...value, unsafe:true})), /awaiting confirmation/);
  await queued;
  releaseUncertain();
  await pendingDiagnostic;
  await new Promise(resolve=>setImmediate(resolve));
  await assert.rejects(uncertain.updateKv(value => ({...value, recovered:true})), /reconciliation required/);
  assert.equal(uncertain.getKv().marker,'old');
  assert.equal(uncertain.getKv().recovered,undefined);
  assert.equal(uncertain.getKv().shouldNotAppear,undefined);
  assert.equal(uncertain.getKv().unsafe,undefined);
  await uncertain.dispatch({type:'tool-call',tool:'get_hook_diagnostics',callId:'diagnostics-after-late-commit',
    args:{session_context:{session_id:'host'}}});
  assert.equal(uncertain.sent.find(item=>item.callId==='diagnostics-after-late-commit').errorCode,'HOOK_DIAGNOSTICS_UNAVAILABLE');

  let releaseNormal, firstNormalPut = true;
  const missingNormalAck = new Promise(resolve => { releaseNormal = resolve; });
  const normal = harness(current, overview, {beforeFetch: options => {
    if (options?.method === 'PUT' && firstNormalPut) { firstNormalPut = false; return missingNormalAck; }
  }});
  const pendingNormal = normal.updateKv(value => ({...value, first:true})).catch(() => {});
  await new Promise(resolve=>setTimeout(resolve,1300));
  await assert.rejects(normal.updateKv(value => ({...value, unsafe:true})), /awaiting confirmation/);
  await normal.dispatch({type:'tool-call',tool:'get_hook_diagnostics',callId:'diagnostics-during-uncertain-write',
    args:{session_context:{session_id:'host'}}});
  const unavailableDiagnostics = normal.sent.find(item => item.callId === 'diagnostics-during-uncertain-write');
  assert.equal(unavailableDiagnostics.ok,false);
  assert.equal(unavailableDiagnostics.errorCode,'HOOK_DIAGNOSTICS_UNAVAILABLE');
  await normal.dispatch({type:'tool-call',tool:'clear_progress',callId:'clear-during-uncertain-write',
    args:{session_id:session,session_context:{session_id:'host'}}});
  const clearReceipt = normal.sent.find(item => item.callId === 'clear-during-uncertain-write');
  assert.equal(clearReceipt.ok,false);
  assert.equal(normal.calls.length,0);
  await normal.dispatch({type:'tool-call',tool:'get_progress',callId:'read-during-uncertain-write',
    args:{session_id:session,session_context:{session_id:'host'}}});
  const readReceipt = normal.sent.find(item => item.callId === 'read-during-uncertain-write');
  assert.equal(readReceipt.ok,true);
  assert.match(readReceipt.result.content.at(-1).text,/绑定未能确认保存/);
  assert.match(readReceipt.result.content.at(-1).text,/勿重复执行/);
  releaseNormal();
  await pendingNormal;
  await new Promise(resolve=>setImmediate(resolve));
  await normal.updateKv(value => ({...value, later:true}));
  assert.equal(normal.getKv().later,true);
  assert.equal(normal.getKv().unsafe,undefined);

  // The host can commit before its acknowledgement is lost. A readable new
  // value is not proof that no earlier write can still arrive.
  let committedPuts = 0;
  const committedWithoutAck = harness(current, overview, {afterFetch: options => {
    if (options?.method === 'PUT') { committedPuts++; return new Promise(() => {}); }
  }});
  const lostAck = committedWithoutAck.updateKv(value => ({...value, committed:true}), {diagnostic:true}).catch(() => {});
  await new Promise(resolve=>setTimeout(resolve,650));
  assert.equal(committedWithoutAck.getKv().committed,true);
  for (let i = 0; i < 3; i++)
    await assert.rejects(committedWithoutAck.updateKv(value => ({...value, unsafe:true})), /awaiting confirmation/);
  await committedWithoutAck.dispatch(event);
  assert.equal(committedWithoutAck.sent.find(item => item.type === 'event-verdict').action,'allow');
  assert.equal(committedPuts,1,'no subsequent PUT may overtake an unconfirmed write');
  assert.equal(committedWithoutAck.getKv().unsafe,undefined);
  await lostAck;

  // An arbitrary 409, malformed receipt, or transport failure gives no proof
  // of rejection. Later writes must remain quarantined.
  for (const failure of ['reject', 'http', 'other-409', 'malformed-409']) {
    let putCount = 0;
    const failedPut = harness(current, overview, {beforeFetch: options => {
      if (options?.method === 'PUT') {
        putCount++;
        if (failure === 'reject') throw new Error('connection lost');
      }
    }, afterFetch: options => {
      if (options?.method !== 'PUT') return null;
      if (failure === 'http') return {ok:false};
      if (failure === 'other-409') return {ok:false,status:409,json:async()=>({error:'OTHER'})};
      if (failure === 'malformed-409') return {ok:false,status:409,json:async()=>{throw new Error('bad body');}};
      return null;
    }});
    const failed = failedPut.updateKv(value => ({...value, attempt:true}), {diagnostic:true}).catch(() => {});
    await new Promise(resolve=>setImmediate(resolve));
    await assert.rejects(failedPut.updateKv(value => ({...value, unsafe:true})), /awaiting confirmation/);
    assert.equal(putCount,1,`${failure} must quarantine later writes`);
    await failed;
  }

  const emptyRead = harness({}, {ok:true,result:{structuredContent:{session_id:session,progress:null}}});
  await emptyRead.dispatch({type:'tool-call',tool:'get_progress',callId:'untrusted-instance',
    args:{session_id:session,task_instance_id:'from-request',session_context:{session_id:'host'}}});
  assert.equal(emptyRead.getKv().taskProgressContextsByHostSession?.host,undefined);

  for (const result of [{isError:true}, {result:{isError:true}}]) {
    const panel = harness(current,{ok:true,result});
    await panel.deliverPanel({type:'panel-tool-request',requestId:'refresh',name:'get_progress_view',arguments:{session_id:session}});
    assert.equal(panel.broadcasts.at(-1).type,'panel-tool-error');
    await panel.deliverPanel({type:'refresh',requestId:'legacy',session_id:session});
    assert.equal(panel.broadcasts.at(-1).type,'refresh-error');
  }

  const failed = harness(current, { ok: false });
  await failed.dispatch(event);
  assert.equal(failed.sent[0].action, 'allow');

  const cleared = harness(current, { ok: true, result: { structuredContent: { session_id: session } } });
  await cleared.dispatch({ type: 'tool-call', callId: 'call-1', tool: 'clear_progress',
    args: { session_id: session, session_context: { session_id: 'host' } } });
  assert.equal(cleared.getKv().taskProgressContextsByHostSession.host, undefined);
  await cleared.dispatch(event);
  assert.equal(cleared.sent.at(-1).blocks[0].id, 'task-progress-startup');

  const diagnostic = h => h.getKv().taskProgressHookDiagnostics.host.events['will-compose-context'];
  assert.equal(diagnostic(matched).guidance_sent, 1);
  assert.equal(diagnostic(matched).last_guidance.kind, 'current_state');
  assert.equal(diagnostic(unknown).last_guidance.kind, 'startup');
  assert.equal(diagnostic(stale).guidance_not_sent, 1);
  assert.equal(diagnostic(stale).last_guidance.reason, 'overview_identity_mismatch');
  assert.equal(diagnostic(failed).last_guidance.reason, 'state_unavailable');
  const quietResult = JSON.parse(JSON.stringify(overview));
  quietResult.result.structuredContent.progress.pending_review_count = 0;
  quietResult.result.structuredContent.progress.stage_inventory_due_count = 0;
  const quiet = harness(current, quietResult);
  await quiet.dispatch(event);
  assert.equal(quiet.sent[0].action, 'allow');
  assert.equal(diagnostic(quiet).last_guidance.reason, 'no_actionable_reminder');
  const invalid = harness({ taskProgressContextsByHostSession: { host: {} } });
  await invalid.dispatch(event);
  assert.equal(invalid.calls.length, 0);
  assert.equal(diagnostic(invalid).last_guidance.reason, 'binding_invalid');
  for (const progress of [null, { ...quietResult.result.structuredContent.progress, read_only: true },
      { ...quietResult.result.structuredContent.progress, schema_version: 1 }]) {
    const unusable = harness(current, { ok: true, result: { structuredContent: {
      ...quietResult.result.structuredContent, progress } } });
    await unusable.dispatch(event);
    assert.equal(unusable.sent[0].action, 'allow');
    assert.equal(diagnostic(unusable).last_guidance.reason, 'overview_unusable');
  }
  const throws = harness(current, null, { node: true });
  await throws.dispatch(event);
  assert.equal(diagnostic(throws).last_guidance.reason, 'state_unavailable');
  assert.doesNotMatch(JSON.stringify(throws.getKv()), /private backend/);
  const transport = harness({}, null, { send: true });
  await transport.dispatch(event);
  assert.equal(diagnostic(transport).received, 1);
  assert.equal(diagnostic(transport).guidance_sent, undefined);
  assert.equal(diagnostic(transport).guidance_send_failed, 1);
  assert.equal(diagnostic(transport).last_guidance.reason, 'verdict_send_failed');
  const missingHook = harness();
  await missingHook.dispatch({ ...event, hookId: undefined });
  assert.equal(missingHook.sent.length, 0);
  assert.equal(diagnostic(missingHook).received, 1);
  assert.equal(diagnostic(missingHook).last_guidance.reason, 'missing_hook_id');
  const missingSession = harness();
  await missingSession.dispatch({ ...event, data: {} });
  assert.equal(missingSession.sent.length, 0);
  assert.equal(missingSession.getKv().taskProgressHookDiagnostics, undefined);
  const unavailableKv = harness({}, null, { kv: true });
  await unavailableKv.dispatch(event);
  assert.equal(unavailableKv.sent[0].action, 'allow');
  await unknown.dispatch(event);
  assert.equal(diagnostic(unknown).received, 2);
  assert.equal(diagnostic(unknown).guidance_sent, 2);

  const lifecycle = harness({ taskProgressActiveHostSessionId: 'host',
    taskProgressSessionsByHostSession: { host: session } });
  await lifecycle.dispatch({ type: 'event', name: 'did-task-state-changed',
    data: { sessionId: 'host', state: { activity: { recordStatus: 'failed' } } } });
  assert.equal(lifecycle.broadcasts[0].type, 'refresh-mapped-session');
  assert.equal(lifecycle.broadcasts[0].session_id, session);
  assert.equal(lifecycle.calls.length, 0);
  const firstReceipt = lifecycle.broadcasts[0];
  await lifecycle.deliverPanel({ ...firstReceipt, type: 'panel-refresh-result',
    status: 'read_ok', state_revision: 7 });
  await lifecycle.deliverPanel({ ...firstReceipt, type: 'panel-refresh-result',
    status: 'read_ok', state_revision: 7 });
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-task-state-changed'].refresh_ok, 1);
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-task-state-changed'].last_refresh.state_revision, 7);
  await lifecycle.dispatch({ type: 'event', name: 'did-message-created',
    data: { sessionId: 'host', messageId: 'saved-assistant-1', role: 'assistant', content: 'Ignored' } });
  assert.equal(lifecycle.broadcasts[1].type, 'refresh-mapped-session');
  await lifecycle.dispatch({ type: 'event', name: 'did-message-created',
    data: { sessionId: 'host', messageId: 'saved-user-1', role: 'user', content: 'Ignored' } });
  assert.equal(lifecycle.broadcasts.length, 2);
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-message-created'].received, 2);
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-message-created'].refresh_requested, 1);
  await lifecycle.deliverPanel({ ...lifecycle.broadcasts[1], type: 'panel-refresh-result', status: 'read_failed' });
  assert.equal(lifecycle.getKv().taskProgressHookDiagnostics.host.events['did-message-created'].refresh_failed, 1);
  await lifecycle.dispatch({ type: 'tool-call', callId: 'read-diagnostics', tool: 'get_hook_diagnostics',
    args: { session_context: { session_id: 'host' } } });
  const view = lifecycle.sent.at(-1).result.structuredContent;
  assert.equal(view.events['did-message-created'].received, 2);
  assert.equal(view.pending_refresh_count, 0);
  await lifecycle.dispatch({ type: 'tool-call', callId: 'other-diagnostics', tool: 'get_hook_diagnostics',
    args: { session_context: { session_id: 'another-host' } } });
  assert.equal(Object.keys(lifecycle.sent.at(-1).result.structuredContent.events).length, 0);
  const unrelated = harness({ taskProgressActiveHostSessionId: 'other',
    taskProgressSessionsByHostSession: { host: session } });
  await unrelated.dispatch({ type: 'event', name: 'did-task-state-changed', data: { sessionId: 'host' } });
  await unrelated.dispatch({ type: 'event', name: 'did-message-created',
    data: { sessionId: 'host', messageId: 'saved-assistant-2', role: 'assistant' } });
  assert.equal(unrelated.broadcasts.length, 0);

  const switched = harness({ taskProgressSessionsByHostSession: { host: session } });
  await switched.dispatch({ type: 'event', name: 'did-session-switched', data: { sessionId: 'host' } });
  assert.equal(switched.getKv().taskProgressActiveHostSessionId, 'host');
  assert.equal(switched.broadcasts[0].type, 'bind-session');
  assert.equal(switched.broadcasts[0].session_id, session);
  assert.equal(switched.getKv().taskProgressHookDiagnostics.host.events['did-session-switched'].refresh_requested, 1);
  let rebindReads=0;
  const reboundSession='00000000-0000-0000-0000-000000000002';
  const rebind = harness({taskProgressSessionsByHostSession:{host:session}}, null, {
    afterFetch: options=>{
      if(options?.method === 'PUT' || ++rebindReads !== 2)return null;
      return {ok:true,json:async()=>({taskProgressSessionsByHostSession:{host:reboundSession}})};
    },
  });
  await rebind.dispatch({type:'event',name:'did-session-switched',data:{sessionId:'host'}});
  const rebound = rebind.broadcasts.find(item=>item.type==='bind-session');
  assert.equal(rebound.session_id,reboundSession,'an old diagnostic receipt cannot override the latest binding');
  assert.equal(rebound.receipt_id,undefined,'a receipt for the old binding must not be attached to the new binding');
  for (const broken of ['unavailable','null','array','invalid-json']) {
    let reads = 0;
    const uncertainSwitch = harness({taskProgressSessionsByHostSession:{host:session}}, null, {
      afterFetch: options => {
        if (options?.method === 'PUT' || ++reads !== 2) return null;
        if (broken === 'unavailable') return {ok:false};
        if (broken === 'invalid-json') return {ok:true,json:async()=>{throw new Error('bad JSON');}};
        return {ok:true,json:async()=>broken === 'null' ? null : []};
      },
    });
    const switchedEvent = {type:'event',name:'did-session-switched',data:{sessionId:'host'}};
    await uncertainSwitch.dispatch(switchedEvent);
    assert.equal(uncertainSwitch.broadcasts.some(item=>item.type==='bind-session'),false,`${broken} state must not clear the binding`);
    await uncertainSwitch.dispatch(switchedEvent);
    assert.equal(uncertainSwitch.broadcasts.find(item=>item.type==='bind-session')?.session_id,session,'a later valid read restores the binding');
  }
  const emptySwitch = harness({});
  await emptySwitch.dispatch({type:'event',name:'did-session-switched',data:{sessionId:'host'}});
  assert.equal(emptySwitch.broadcasts.find(item=>item.type==='bind-session')?.session_id,null,'confirmed absence still clears the binding');
  console.log('Cindy Hook contract tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
