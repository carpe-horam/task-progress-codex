'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {harness, sid} = require('./test_cindy_host_archive.cjs');
const event = host => ({type:'event',name:'did-session-archived',data:{sessionId:host}});
const key = (host, target) => host + ':' + target;
const entry = (host, target, stamp='2026-11-27T12:00:00.000Z') =>
  ({version:1,host_session_id:host,session_id:target,observed_at:stamp});
const gate = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise,resolve};};
const ack = target => ({ok:true,result:{session_id:target,archived:true,changed:false}});
function storage(initial) {
  let state = structuredClone(initial);
  return {get:() => state, set:value => {state = value;},
    fetch:async (_url, opts) => {
      if (opts?.method === 'PUT') state = JSON.parse(opts.body);
      return {ok:true,json:async () => structuredClone(state)};
    }};
}

test('one runtime serializes concurrent duplicate events and reconciliation', {timeout:10000}, async () => {
  const started = gate(), release = gate();
  const c = harness({host:sid(1)}, {host:'active'}, {archiveRequest:async req => {
    started.resolve(); await release.promise; return ack(req.params.session_id);
  }});
  await c.drain();
  await c.dispatch(event('host')); await started.promise;
  const triggers = Array.from({length:20}, () => c.dispatch(event('host')));
  triggers.push(...Array.from({length:20}, () => c.sync()));
  assert.equal(c.calls.length, 1, 'no second send while the first is awaiting acknowledgment');
  release.resolve(); await Promise.all(triggers); await c.drain();
  assert.equal(c.calls.length, 1);
  assert.deepEqual(c.getKv().taskProgressHostArchiveOutbox, {}, 'confirmed send must remove the durable intent');
});

test('binding changes during send never redirect the request or erase the new target', {timeout:10000}, async () => {
  const s = storage({taskProgressSessionsByHostSession:{host:sid(1)}});
  const started = gate(), release = gate();
  const c = harness({}, {host:'active'}, {fetch:s.fetch, archiveRequest:async req => {
    if (req.params.session_id === sid(1)) {started.resolve(); await release.promise;}
    return ack(req.params.session_id);
  }});
  await c.drain(); await c.dispatch(event('host')); await started.promise;
  s.get().taskProgressSessionsByHostSession.host = sid(2);
  await c.dispatch(event('host')); release.resolve(); await c.drain();
  assert.deepEqual(c.calls.map(x => x.params.session_id), [sid(1),sid(2)]);
  assert.equal(s.get().taskProgressSessionsByHostSession.host, sid(2));
  assert.deepEqual(s.get().taskProgressHostArchiveOutbox, {});
});

test('confirmed send can finish cleanup after a transient read failure and rebind', {timeout:10000}, async () => {
  const k = key('host',sid(1));
  const s = storage({taskProgressSessionsByHostSession:{host:sid(1)},taskProgressHostArchiveOutbox:{[k]:entry('host',sid(1))}});
  let failRead = false;
  const c = harness({}, {host:'active'}, {fetch:async (...args) => {
    if (!args[1]?.method && failRead) {failRead = false; throw new Error('cleanup read failed before PUT');}
    return s.fetch(...args);
  },archiveRequest:async () => {failRead = true; return ack(sid(1));}});
  await c.drain(); assert(s.get().taskProgressHostArchiveOutbox[k]);
  s.get().taskProgressSessionsByHostSession.host = sid(2);
  await c.tick();
  assert.equal(c.calls.length, 1, 'already confirmed work must not send to either target again');
  assert(!s.get().taskProgressHostArchiveOutbox[k], 'old confirmed intent can be cleaned independently of current binding');
});

test('replacement queue generation survives old acknowledgment and does not block other tasks', {timeout:10000}, async () => {
  const k = key('host',sid(1)), other = key('other',sid(2));
  const s = storage({taskProgressSessionsByHostSession:{host:sid(1),other:sid(2)},
    taskProgressHostArchiveOutbox:{[k]:entry('host',sid(1))}});
  let replace = true;
  const c = harness({}, {host:'active',other:'active'}, {fetch:s.fetch, archiveRequest:async req => {
    if (replace) {
      replace = false;
      s.get().taskProgressHostArchiveOutbox[k] = entry('host',sid(1),'2026-11-27T12:00:01.000Z');
      s.get().taskProgressHostArchiveOutbox[other] = entry('other',sid(2));
    }
    return ack(req.params.session_id);
  }});
  await c.drain();
  assert(s.get().taskProgressHostArchiveOutbox[k], 'old acknowledgment must preserve the replacement intent');
  assert.equal(s.get().taskProgressHostArchiveOutbox[k].observed_at, '2026-11-27T12:00:01.000Z',
    'acknowledgment of old generation cannot clear its replacement');
  await c.tick();
  assert(c.calls.some(x => x.params.session_id === sid(2)), 'other pending work must continue');
  assert.deepEqual(s.get().taskProgressHostArchiveOutbox, {});
});

test('restart with changed binding retains unconfirmed intent without sending', {timeout:10000}, async () => {
  const k = key('host',sid(1));
  const state = {taskProgressSessionsByHostSession:{host:sid(2)},taskProgressHostArchiveOutbox:{[k]:entry('host',sid(1))}};
  const restarted = harness({}, {host:'active'}, {initialKv:state});
  await restarted.drain(); await restarted.tick();
  assert.equal(restarted.calls.length, 0);
  assert.deepEqual(restarted.getKv().taskProgressHostArchiveOutbox, state.taskProgressHostArchiveOutbox);
});

test('late acknowledgment after timeout cannot clear a retained intent or affect the new binding', {timeout:10000}, async () => {
  const k = key('host',sid(1)), release = gate();
  const s = storage({taskProgressSessionsByHostSession:{host:sid(1)},taskProgressHostArchiveOutbox:{[k]:entry('host',sid(1))}});
  const c = harness({}, {host:'active'}, {fetch:s.fetch,quickTimeout:true,archiveRequest:async req => {
    if (req.params.session_id === sid(1)) {await release.promise; return ack(sid(1));}
    return ack(sid(2));
  }});
  await c.drain(); // First request timed out; the actual request may still be running.
  s.get().taskProgressSessionsByHostSession.host = sid(2);
  await c.dispatch(event('host')); await c.drain();
  release.resolve(); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(c.calls.map(x => x.params.session_id), [sid(1),sid(2)]);
  assert(s.get().taskProgressHostArchiveOutbox[k], 'an expired request reply cannot authorize cleanup');
  assert(!s.get().taskProgressHostArchiveOutbox[key('host',sid(2))]);
  assert.equal(s.get().taskProgressSessionsByHostSession.host, sid(2));
});
