/* Resident adapter against the documented host contract; no native host writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
// Fault-coverage checks can load a temporary mutated copy. Production sources
// are never edited, and ordinary regression runs use the real plugin source.
const source = fs.readFileSync(process.env.TASK_PROGRESS_TEST_SOURCE ||
  path.join(__dirname, '../plugins/task-progress/cindy/main.js'), 'utf8');
const sid = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

function harness(mappings, states, options = {}) {
  let handler, kv = structuredClone(options.initialKv || {taskProgressSessionsByHostSession:mappings});
  let api = states, failure = false;
  const calls = [], queries = [], broadcasts = [], markers = new Map(), intervals = [], sourceRegistrations = [];
  const sandbox = {
    BroadcastChannel:class {postMessage(msg) {broadcasts.push(msg);}},
    cindy:{
      onHostMessage:fn => {handler = fn;}, send:async () => {},
      ...(options.unsupported ? {} : {sessionStates:async args => {
        queries.push([...args.sessionIds]);
        return typeof api === 'function' ? api(args) : {ok:true, sessions:args.sessionIds.map(sessionId =>
          ({sessionId, status:api[sessionId] || 'out_of_scope'}))};
      }}),
      node:{request:async req => {
        if (req.method === 'task-progress/register-source') {
          sourceRegistrations.push(req);
          if (options.sourceRegistrationRequest) return options.sourceRegistrationRequest(req);
          return {ok:true, result:{sessions:req.params.session_ids.map(session_id => ({session_id, registered:true}))}};
        }
        calls.push(req);
        if (typeof failure === 'function' ? failure(req) : failure) throw new Error('offline');
        if (options.archiveRequest) return options.archiveRequest(req);
        const p = req.params;
        assert.equal(req.method, 'task-progress/archive-host-session');
        const changed = !markers.has(p.session_id);
        if (changed) markers.set(p.session_id, structuredClone(p));
        if (options.afterArchive) await options.afterArchive(req, {changed, markers});
        return {ok:true, result:{session_id:p.session_id, archived:true, changed}};
      }}
    },
    fetch:async (_url, opts) => {
      if (options.fetch) return options.fetch(_url, opts);
      if (opts?.method === 'PUT') kv = JSON.parse(opts.body);
      return {ok:true, json:async () => structuredClone(kv)};
    },
    setTimeout:(fn, ms) => setTimeout(fn, options.quickTimeout && ms === 3000 ? 25 : ms), clearTimeout, AbortController,
    setInterval:(fn, ms) => {intervals.push({fn, ms}); return {unref() {}};},
  };
  vm.runInNewContext(source, sandbox);
  return {calls, queries, broadcasts, markers, intervals, sourceRegistrations,
    dispatch:msg => handler(msg),
    sync:() => sandbox.syncHostArchives(),
    drain:() => vm.runInNewContext('hostArchiveWorkQueue', sandbox),
    state:value => {api = value;}, fail:value => {failure = value;},
    getKv:() => structuredClone(kv),
    updateKv:change => sandbox.updateRequiredPluginKv(change),
    rebind:(host, value) => {kv.taskProgressSessionsByHostSession[host] = value;},
    tick:async () => {
      const timer = intervals.find(item => item.ms === 60000);
      assert(timer, 'archive retry/reconciliation timer must be installed');
      timer.fn();
      await vm.runInNewContext('hostArchiveWorkQueue', sandbox);
    },
  };
}

module.exports = {harness, sid};
if (require.main === module) (async () => {
  const client = harness({old:sid(1), active:sid(2), gone:sid(3), unknown:sid(4)},
    {old:'archived', active:'active', gone:'deleted'});
  await client.drain();
  assert.deepEqual(Array.from(client.sourceRegistrations[0].params.session_ids), [sid(1),sid(2),sid(3),sid(4)]);
  assert.equal(client.sourceRegistrations[0].params.source, 'cindy', 'source derives from bindings, not archive or model status');
  let sourceOffline = true;
  const legacySources = harness(Object.fromEntries(Array.from({length:205},(_,i)=>['host'+i,sid(1000+i)])), {}, {
    unsupported:true, sourceRegistrationRequest:async req => {
      if (sourceOffline) throw new Error('source registration unavailable');
      return {ok:true,result:{sessions:req.params.session_ids.map(session_id=>({session_id,registered:true}))}};
    }});
  await legacySources.drain();
  assert.equal(legacySources.sourceRegistrations.length,1);
  assert.equal(legacySources.calls.length,0,'source registration never archives active/unknown hosts');
  sourceOffline=false;await legacySources.tick();
  assert.deepEqual(legacySources.sourceRegistrations.map(req=>req.params.session_ids.length),[100,100,100,5]);
  assert.deepEqual(client.calls.map(c => c.params.session_id), [sid(1)], 'startup sync only archives explicit archived states');
  assert(client.intervals.length >= 1, 'startup also installs a bounded reconciliation timer');
  client.state({old:'active', active:'active'});
  await client.sync();
  assert(client.markers.has(sid(1)), 'unarchive/active never restores Task Progress');
  assert.equal(client.calls.length, 1);
  await client.dispatch({type:'event', name:'did-session-archived', data:{sessionId:'active'}});
  await client.drain();
  assert(client.markers.has(sid(2)), 'non-focused sessions archive with auto-review disabled');
  await client.dispatch({type:'event', name:'did-task-state-changed', data:{sessionId:'active', state:{status:'active'}}});
  await client.drain();
  await client.dispatch({type:'event', name:'did-task-state-changed', data:{sessionId:'active', state:{activity:{recordStatus:'archived'}}}});
  await client.drain();
  assert.equal(client.calls.length, 2, 'duplicate events do not repeat acknowledged writes');
  await client.dispatch({type:'event', name:'did-session-archived', data:{sessionId:'unmapped'}});
  await client.drain();
  assert.equal(client.calls.length, 2, 'missing host mapping is never guessed');

  const mappings = Object.fromEntries(Array.from({length:205}, (_, i) => ['host-' + i, sid(i + 10)]));
  const many = harness(mappings, args => ({ok:true, sessions:args.sessionIds.map(sessionId =>
    ({sessionId, status:sessionId === 'host-204' ? 'archived' : 'active'}))}));
  await many.drain();
  assert.deepEqual(many.queries.map(q => q.length), [100,100,5]);
  assert(many.markers.has(sid(214)), 'startup reaches the final batch');

  for (const bad of [{ok:false, errorCode:'PERMISSION_DENIED'}, {ok:true, sessions:[]},
    {ok:true, sessions:[{sessionId:'wrong',status:'archived'}]},
    {ok:true, sessions:[{sessionId:'host',status:'unexpected'}]}]) {
    const broken = harness({host:sid(1)}, () => bad);
    await broken.drain();
    assert.equal(broken.calls.length, 0, 'failed or malformed state query cannot authorize writes');
    assert(broken.broadcasts.some(x => x.status === 'unavailable'));
  }
  const legacy = harness({host:sid(1)}, {}, {unsupported:true});
  await legacy.sync();
  assert.equal(legacy.queries.length, 0);
  assert.equal(legacy.calls.length, 0);
  await legacy.dispatch({type:'event', name:'did-session-archived', data:{sessionId:'host'}});
  await legacy.drain();
  assert(legacy.markers.has(sid(1)), 'real-time events still work without the startup query API');

  let release;
  const race = harness({host:sid(1)}, () => new Promise(resolve => {release = resolve;}));
  await new Promise(resolve => setImmediate(resolve));
  race.rebind('host', sid(2));
  release({ok:true, sessions:[{sessionId:'host', status:'archived'}]});
  await race.drain();
  assert.equal(race.calls.length, 0, 'late query cannot archive a newly rebound session');

  let finishLateQuery;
  const late = harness({host:sid(1)}, () => new Promise(resolve => {finishLateQuery = resolve;}), {quickTimeout:true});
  await late.drain();
  finishLateQuery({ok:true, sessions:[{sessionId:'host', status:'archived'}]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(late.calls.length, 0, 'late replies after the query deadline cannot authorize writes');
  late.state({host:'archived'});
  await late.sync();
  assert(late.markers.has(sid(1)), 'next reconciliation recovers after a timed-out query');

  const retry = harness({host:sid(1)}, {host:'active'});
  await retry.drain();
  retry.fail(true);
  await retry.dispatch({type:'event', name:'did-session-archived', data:{sessionId:'host'}});
  await retry.drain();
  assert.equal(retry.markers.size, 0);
  retry.fail(false);
  await retry.sync();
  assert(retry.markers.has(sid(1)), 'an observed archive retries even if the host has since become active');

  const gap = harness({host:sid(1)}, {host:'active'});
  await gap.drain();
  gap.state({host:'archived'});
  await gap.dispatch({type:'event',name:'unrelated',dropped:2});
  await gap.drain();
  assert(gap.markers.has(sid(1)), 'dropped events trigger state reconciliation');
  console.log('PASS: startup batching, one-way archive, event replay, event gaps, retries, binding races and fail-closed queries');
})().catch(error => {console.error(error);process.exitCode = 1;});
