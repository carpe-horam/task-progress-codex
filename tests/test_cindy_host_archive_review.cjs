/* Failure injection for retry fairness and startup coverage, including timers. */
const assert = require('node:assert/strict');
const {harness, sid} = require('./test_cindy_host_archive.cjs');
const event = host => ({type:'event', name:'did-session-archived', data:{sessionId:host}});

(async () => {
  const failures = [];
  async function check(name, run) {
    try {await run(); console.log('PASS:', name);}
    catch (error) {failures.push({name, message:error.message}); console.error('FAIL:', name, error.message);}
  }
  await check('one failed task cannot starve other pending archives', async () => {
    const c = harness({bad:sid(1), good:sid(2)}, {bad:'active',good:'active'});
    await c.drain();
    c.fail(req => req.params.session_id === sid(1));
    await c.dispatch(event('bad')); await c.drain();
    await c.dispatch(event('good')); await c.drain();
    for (let i = 0; i < 2; i++) await c.tick();
    assert(c.markers.has(sid(2)), 'valid second task must progress while the first keeps failing');
    assert(!c.markers.has(sid(1)));
    c.fail(false); await c.tick();
    assert(c.markers.has(sid(1)), 'first task must remain retryable');
  });
  await check('failed first startup batch cannot hide later batches', async () => {
    const mappings = Object.fromEntries(Array.from({length:205}, (_, i) => ['host-' + i, sid(i + 10)]));
    const c = harness(mappings, ({sessionIds}) => sessionIds[0] === 'host-0'
      ? {ok:false,errorCode:'INTERNAL'}
      : {ok:true,sessions:sessionIds.map(sessionId => ({sessionId,status:'archived'}))});
    await c.drain();
    assert.deepEqual(c.queries.map(x => x.length), [100,100,5]);
    assert(c.markers.has(sid(214)), 'final startup batch must still synchronize');
    assert(!c.markers.has(sid(10)), 'failed query must not invent archive evidence');
    c.state(({sessionIds}) => ({ok:true,sessions:sessionIds.map(sessionId => ({sessionId,status:'archived'}))}));
    await c.tick();
    assert.equal(c.markers.size, 205, 'failed startup batch must recover on the next scheduled pass');
    assert.equal(c.calls.length, 205, 'already synchronized later batches must not write duplicates');
  });
  await check('legacy host retries events automatically without sessionStates', async () => {
    const c = harness({host:sid(1)}, {}, {unsupported:true});
    c.fail(true);
    await c.dispatch(event('host')); await c.drain();
    c.fail(false);
    await c.tick();
    assert(c.markers.has(sid(1)), 'event retry must not depend on the optional startup query API');
  });
  await check('lost acknowledgment retries without duplicating persisted archive', async () => {
    let loseReply = true, persisted = 0;
    const c = harness({host:sid(1)}, {host:'active'}, {afterArchive:async (_req, {changed}) => {
      persisted += Number(changed);
      if (loseReply) {loseReply = false; throw new Error('reply lost after commit');}
    }});
    await c.drain();
    await c.dispatch(event('host')); await c.drain();
    assert.equal(persisted, 1);
    await c.tick();
    assert.equal(c.calls.length, 2);
    assert.equal(persisted, 1, 'uncertain reply must not cause a second archive record');
    await c.tick();
    await Promise.all([c.dispatch(event('host')), c.dispatch(event('host'))]);
    await c.drain();
    assert.equal(c.calls.length, 2, 'acknowledged retries and duplicate events must deduplicate');
  });
  if (failures.length) {console.error(JSON.stringify(failures, null, 2)); process.exitCode = 1;}
})().catch(error => {console.error(error);process.exitCode = 1;});
