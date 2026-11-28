'use strict';
// Contract model of two independent runtimes sharing whole-document KV.
// Multiple different Agents are explicitly outside the user's acceptance scope.
// Retain the historical reproduction; skipping it is not a claim of safety.
const test = require('node:test');
const assert = require('node:assert/strict');
const {harness, sid} = require('./test_cindy_host_archive.cjs');
const first = 'host:' + sid(1), second = 'other:' + sid(2);
const entry = {version:1,host_session_id:'host',session_id:sid(1),observed_at:'2026-11-27T12:00:00.000Z'};

test('shared KV must preserve a second runtime enqueue during first runtime cleanup', {
  timeout:10000,
  skip:'Out of scope by user: multiple different Agents concurrently writing shared KV'
}, async () => {
  let state = {taskProgressSessionsByHostSession:{host:sid(1),other:sid(2)},
    taskProgressHostArchiveOutbox:{[first]:entry}};
  let pauseCleanup = false, signalRead, releaseRead;
  const readStarted = new Promise(resolve => {signalRead = resolve;});
  const resume = new Promise(resolve => {releaseRead = resolve;});
  function fetchFor(owner) {
    return async (_url, opts) => {
      if (opts?.method === 'PUT') state = JSON.parse(opts.body);
      const snapshot = structuredClone(state);
      if (owner === 'a' && !opts?.method && pauseCleanup) {
        pauseCleanup = false; signalRead(); await resume;
      }
      return {ok:true,json:async () => snapshot};
    };
  }
  const a = harness({}, {host:'active',other:'active'}, {fetch:fetchFor('a'),archiveRequest:async () => {
    pauseCleanup = true;
    return {ok:true,result:{session_id:sid(1),archived:true}};
  }});
  await readStarted;
  const b = harness({}, {host:'active',other:'active'}, {fetch:fetchFor('b'),archiveRequest:async () => {
    throw new Error('second runtime is offline');
  }});
  await b.drain();
  await b.dispatch({type:'event',name:'did-session-archived',data:{sessionId:'other'}});
  await b.drain();
  assert(state.taskProgressHostArchiveOutbox[second], 'second runtime successfully persisted its event');
  releaseRead(); await a.drain();
  assert(state.taskProgressHostArchiveOutbox[second], 'stale cleanup PUT must not erase the newer event');
});
