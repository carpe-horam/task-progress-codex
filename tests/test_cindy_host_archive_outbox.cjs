/* Durable enqueue before send, recovery before current-state reconciliation. */
const assert = require('node:assert/strict');
const {harness, sid} = require('./test_cindy_host_archive.cjs');
const event = {type:'event',name:'did-session-archived',data:{sessionId:'host'}};
const key = 'host:' + sid(1);
const queued = () => ({taskProgressSessionsByHostSession:{host:sid(1)},
  unrelated:{keep:true}, taskProgressHostArchiveOutbox:{[key]:{
    version:1,host_session_id:'host',session_id:sid(1),observed_at:'2026-11-27T12:00:00.000Z'}}});

(async () => {
  {
    const first = harness({host:sid(1)}, {host:'active'});
    await first.drain();
    first.fail(true);
    await first.dispatch(event); await first.drain();
    const saved = first.getKv();
    assert(saved.taskProgressHostArchiveOutbox[key], 'failed send must remain durable');
    assert.equal(first.markers.size, 0);
    const restarted = harness({}, {host:'active'}, {initialKv:saved});
    await restarted.drain();
    assert.equal(restarted.calls.length, 1, 'startup replays persisted intent even though host is active');
    assert(restarted.markers.has(sid(1)));
    assert.deepEqual(restarted.getKv().taskProgressHostArchiveOutbox, {});
    await restarted.tick();
    assert.equal(restarted.calls.length, 1, 'acknowledged intent is no longer pending');
  }
  {
    const legacy = harness({}, {}, {initialKv:queued(), unsupported:true});
    await legacy.drain();
    assert(legacy.markers.has(sid(1)), 'startup restores durable intents without sessionStates');
    assert.deepEqual(legacy.getKv().unrelated, {keep:true}, 'queue updates preserve other plugin fields');
    assert.equal(legacy.queries.length, 0);
  }
  {
    const saved = queued(); saved.taskProgressSessionsByHostSession.host = sid(2);
    const changed = harness({}, {host:'active'}, {initialKv:saved});
    await changed.drain();
    assert.equal(changed.calls.length, 0, 'recovery cannot redirect an old intent to a new binding');
    assert(changed.getKv().taskProgressHostArchiveOutbox[key], 'binding conflict is retained durably');
    changed.rebind('host', sid(1)); await changed.tick();
    assert(changed.markers.has(sid(1)), 'matching binding can later resume the retained intent');
  }
  {
    let state = {taskProgressSessionsByHostSession:{host:sid(1)}}, attempted = 0, deny = true;
    const fetch = async (_url, opts) => {
      if (opts?.method === 'PUT') {
        attempted++;
        if (deny) throw new Error('Persistence unavailable');
        state = JSON.parse(opts.body);
      }
      return {ok:true,json:async () => structuredClone(state)};
    };
    const failed = harness({}, {host:'active'}, {fetch});
    await failed.drain();
    await failed.dispatch(event); await failed.drain();
    assert.equal(attempted, 1);
    assert.equal(failed.calls.length, 0, 'never send before durable enqueue is confirmed');
    // A failed PUT is uncertain; existing KV fencing prevents later writes.
    deny = false;
    const recovered = harness({}, {host:'archived'}, {fetch});
    await recovered.drain();
    assert.equal(recovered.calls.length, 1, 'restart can rediscover an event that never persisted');
  }
  {
    let state = queued(), loseCleanup = true;
    const fetch = async (_url, opts) => {
      if (opts?.method === 'PUT') {
        const next = JSON.parse(opts.body);
        if (loseCleanup && !next.taskProgressHostArchiveOutbox[key]) {
          loseCleanup = false; throw new Error('Queue cleanup failed after service accepted');
        }
        state = next;
      }
      return {ok:true,json:async () => structuredClone(state)};
    };
    const first = harness({}, {host:'active'}, {fetch});
    await first.drain();
    assert.equal(first.calls.length, 1);
    assert(state.taskProgressHostArchiveOutbox[key]);
    const persistedMarker = first.markers.get(sid(1));
    const replay = harness({}, {host:'active'}, {fetch, archiveRequest:async req => {
      assert.equal(req.params.session_id, persistedMarker.session_id);
      return {ok:true,result:{session_id:sid(1),archived:true,changed:false}};
    }});
    await replay.drain();
    assert.equal(replay.calls.length, 1);
    assert.deepEqual(state.taskProgressHostArchiveOutbox, {}, 'restart retries cleanup after idempotent acknowledgment');
  }
  {
    const broken = queued(); broken.taskProgressHostArchiveOutbox[key].session_id = sid(2);
    const client = harness({}, {}, {initialKv:broken});
    await client.drain();
    assert.equal(client.calls.length, 0, 'corrupt durable identities never authorize writes');
    assert.deepEqual(client.getKv(), broken, 'corrupt queue is not silently cleared');
  }
  {
    let state = {taskProgressSessionsByHostSession:{host:sid(1)}}, releasePut;
    const fetch = async (_url, opts) => {
      if (opts?.method === 'PUT') {
        await new Promise(resolve => {releasePut = resolve;});
        state = JSON.parse(opts.body);
      }
      return {ok:true,json:async () => structuredClone(state)};
    };
    const late = harness({}, {host:'active'}, {fetch});
    await late.drain();
    await late.dispatch(event); await late.drain();
    assert.equal(late.calls.length, 0, 'enqueue timeout does not authorize sending');
    releasePut();
    await new Promise(resolve => setImmediate(resolve));
    assert(state.taskProgressHostArchiveOutbox[key], 'a late PUT can still have committed');
    assert.equal(late.calls.length, 0, 'late enqueue acknowledgment does not authorize stale dispatch');
    const restarted = harness({}, {host:'active'}, {initialKv:state});
    await restarted.drain();
    assert.equal(restarted.calls.length, 1, 'restart recovers the durably committed late enqueue');
    assert.deepEqual(restarted.getKv().taskProgressHostArchiveOutbox, {});
  }
  console.log('PASS: persistent enqueue, active/legacy restart recovery, binding conflict, storage failure, cleanup failure and corrupt queue');
})().catch(error => {console.error(error);process.exitCode = 1;});
