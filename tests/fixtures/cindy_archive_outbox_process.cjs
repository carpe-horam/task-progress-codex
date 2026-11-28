/* Restart fixture: host KV is a real temporary file; archive target is real HTTP. */
const fs = require('node:fs');
const {harness} = require('../test_cindy_host_archive.cjs');
let input = '';
process.stdin.on('data', data => {input += data;});
process.stdin.on('end', async () => {
  try {
    const config = JSON.parse(input);
    const readState = () => JSON.parse(fs.readFileSync(config.kvFile, 'utf8'));
    const fetchState = async (_url, opts) => {
      if (opts?.method === 'PUT') {
        const temp = config.kvFile + '.tmp';
        fs.writeFileSync(temp, opts.body, {mode:0o600});
        fs.renameSync(temp, config.kvFile);
        const queued = Object.keys(JSON.parse(opts.body).taskProgressHostArchiveOutbox || {}).length;
        if ((config.phase === 'kill-after-enqueue' && queued > 0) ||
            (config.phase === 'kill-after-cleanup-write' && queued === 0))
          process.kill(process.pid, 'SIGKILL');
      }
      return {ok:true,json:async () => readState()};
    };
    let sequence = 0;
    const client = harness({}, {host:'active'}, {fetch:fetchState, archiveRequest:async request => {
      const response = await fetch(config.origin + '/mcp', {method:'POST', headers:{
        Authorization:config.authorization, 'Content-Type':'application/json', Accept:'application/json'},
        body:JSON.stringify({jsonrpc:'2.0',id:++sequence,method:request.method,params:request.params}),
        signal:AbortSignal.timeout(4000)});
      const result = await response.json();
      if (response.status !== 200 || result.error) throw new Error('Archive HTTP failed');
      if (config.phase === 'kill-after-service-commit') process.kill(process.pid, 'SIGKILL');
      if (config.phase === 'commit-with-lost-reply') throw new Error('Reply lost after real commit');
      return {ok:true,result:result.result};
    }});
    await client.drain();
    if (['enqueue-offline','kill-after-enqueue'].includes(config.phase)) {
      client.fail(true);
      await client.dispatch({type:'event',name:'did-session-archived',data:{sessionId:'host'}});
      await client.drain();
    }
    process.stdout.write(JSON.stringify({pending:Object.keys(readState().taskProgressHostArchiveOutbox || {}).length,
      calls:client.calls.length}));
  } catch (_) {process.stderr.write('Outbox process fixture failed\n');process.exitCode = 1;}
});
