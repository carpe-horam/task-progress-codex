/* Real HTTP persistence behind the resident Cindy adapter; isolated host states. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {harness} = require('./test_cindy_host_archive.cjs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-adapter-integration-'));
  const state = path.join(root, 'service');
  fs.mkdirSync(state);
  const authorization = 'Bearer ' + crypto.randomBytes(32).toString('hex');
  fs.writeFileSync(path.join(state, 'http-headers.json'), JSON.stringify({Authorization:authorization}), {mode:0o600});
  const service = spawn('python3', ['-B', path.resolve(__dirname, '../plugins/task-progress/shared_service.py'),
    '--port', '0', '--state-dir', state, '--session-dir', path.join(root, 'sessions')], {env:{...process.env, HOME:root}});
  let errorOutput = '';
  service.stderr.on('data', data => {errorOutput += data;});
  try {
    const metadata = path.join(state, 'daemon.json');
    for (let i = 0; i < 200 && !fs.existsSync(metadata); i++) {
      if (service.exitCode !== null) throw new Error(errorOutput);
      await pause(20);
    }
    const origin = 'http://127.0.0.1:' + JSON.parse(fs.readFileSync(metadata)).port;
    async function post(endpoint, value) {
      const response = await fetch(origin + endpoint, {method:'POST', headers:{Authorization:authorization,
        'Content-Type':'application/json', Accept:'application/json'}, body:JSON.stringify(value), signal:AbortSignal.timeout(4000)});
      assert.equal(response.status, 200);
      return response.json();
    }
    let sequence = 0;
    const rpc = (method, params) => post('/mcp', {jsonrpc:'2.0', id:++sequence, method, params});
    async function create(title) {
      const value = await rpc('tools/call', {name:'init_task_network', arguments:{task_title:title,atomic_reason:'One fixture'}});
      assert(!value.error && !value.result.isError);
      return value.result.structuredContent;
    }
    const a = await create('Archived at startup'), b = await create('Archive event with lost acknowledgment');
    const document = task => fs.readFileSync(path.join(root, 'sessions', task.session_id + '.json'));
    const before = [document(a), document(b)];
    let loseReply = false;
    const archiveRequest = async req => {
      const wire = await rpc(req.method, req.params);
      if (loseReply) {loseReply = false; throw new Error('Lost reply after real HTTP commit');}
      assert(!wire.error, JSON.stringify(wire));
      return {ok:true, result:wire.result};
    };
    const mappings = {old:a.session_id, current:b.session_id};
    const adapter = harness(mappings, {old:'archived',current:'active'}, {archiveRequest});
    await adapter.drain();
    const markerFile = task => path.join(root, 'sessions', task.session_id + '.host-archive.json');
    const firstMarker = fs.readFileSync(markerFile(a));
    assert(!fs.existsSync(markerFile(b)));
    loseReply = true;
    await adapter.dispatch({type:'event',name:'did-session-archived',data:{sessionId:'current'}});
    await adapter.drain();
    const eventMarker = fs.readFileSync(markerFile(b));
    await adapter.tick();
    assert.deepEqual(fs.readFileSync(markerFile(b)), eventMarker, 'actual lost-reply retry preserves the first archive');
    adapter.state({old:'active',current:'active'});
    await adapter.tick();
    const restarted = harness(mappings, {old:'archived',current:'archived'}, {archiveRequest});
    await restarted.drain();
    assert.deepEqual(fs.readFileSync(markerFile(a)), firstMarker);
    assert.deepEqual(fs.readFileSync(markerFile(b)), eventMarker);
    assert.deepEqual(document(a), before[0]);
    assert.deepEqual(document(b), before[1]);
    // Separate OS processes share only a real KV file. The second process sees
    // an active host, so only durable recovery can deliver the pending event.
    const durable = await create('Durable outbox across process exit');
    const durableBefore = document(durable);
    const kvFile = path.join(root, 'host-kv.json');
    fs.writeFileSync(kvFile, JSON.stringify({taskProgressSessionsByHostSession:{host:durable.session_id}}));
    async function runAdapterProcess(phase, expectedSignal = null) {
      const child = spawn(process.execPath, [path.join(__dirname, 'fixtures/cindy_archive_outbox_process.cjs')],
        {stdio:['pipe','pipe','pipe']});
      let output = '', errors = '';
      child.stdout.on('data', data => {output += data;});
      child.stderr.on('data', data => {errors += data;});
      const done = once(child, 'close');
      let expired = false;
      const deadline = setTimeout(() => {expired = true; child.kill('SIGKILL');}, 6000);
      child.stdin.end(JSON.stringify({phase, origin, authorization, kvFile}));
      const [code, signal] = await done;
      clearTimeout(deadline);
      assert.equal(expired, false, 'fixture must reach its explicit crash point before the deadline');
      if (expectedSignal) {assert.equal(signal, expectedSignal, errors); return null;}
      assert.equal(code, 0, errors);
      return JSON.parse(output);
    }
    assert.deepEqual(await runAdapterProcess('enqueue-offline'), {pending:1,calls:1});
    assert(!fs.existsSync(markerFile(durable)), 'offline event must not have reached the service');
    assert.deepEqual(await runAdapterProcess('commit-with-lost-reply'), {pending:1,calls:1});
    const durableMarker = fs.readFileSync(markerFile(durable));
    assert.deepEqual(await runAdapterProcess('recover'), {pending:0,calls:1});
    assert.deepEqual(fs.readFileSync(markerFile(durable)), durableMarker, 'recovery after real lost reply remains idempotent');
    assert.deepEqual(await runAdapterProcess('recover'), {pending:0,calls:0});
    assert.deepEqual(document(durable), durableBefore);

    // Abrupt termination at the three durable boundaries, not just clean exits.
    for (const phase of ['kill-after-enqueue','kill-after-service-commit','kill-after-cleanup-write']) {
      const task = await create(phase), taskBefore = document(task);
      fs.writeFileSync(kvFile, JSON.stringify({taskProgressSessionsByHostSession:{host:task.session_id}}));
      if (phase !== 'kill-after-enqueue')
        assert.deepEqual(await runAdapterProcess('enqueue-offline'), {pending:1,calls:1});
      await runAdapterProcess(phase, 'SIGKILL');
      const committed = fs.existsSync(markerFile(task)) ? fs.readFileSync(markerFile(task)) : null;
      assert.equal(Boolean(committed), phase !== 'kill-after-enqueue');
      const replay = await runAdapterProcess('recover');
      assert.deepEqual(replay, {pending:0,calls:phase === 'kill-after-cleanup-write' ? 0 : 1});
      if (committed) assert.deepEqual(fs.readFileSync(markerFile(task)), committed);
      assert.deepEqual(document(task), taskBefore);
    }
    const link = await post('/widget/open', {});
    const catalogue = await fetch(origin + '/widget/catalogue', {
      headers:{Authorization:'Bearer ' + link.url.split('#')[1]}, signal:AbortSignal.timeout(4000)}).then(response => response.json());
    assert.equal(catalogue.tasks.length, 6);
    assert(catalogue.tasks.every(task => task.archived && !task.closed));
    console.log('PASS: adapter → real HTTP → marker → widget; active-host restart, lost reply and SIGKILL after enqueue/commit/cleanup');
  } finally {
    if (service.exitCode === null) {service.kill('SIGTERM'); await once(service, 'exit');}
    fs.rmSync(root, {recursive:true,force:true});
  }
})().catch(error => {console.error(error);process.exitCode = 1;});
