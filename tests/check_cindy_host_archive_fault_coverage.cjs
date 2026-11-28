'use strict';
/* Negative controls: real tests must detect isolated faults in real main.js. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const sourcePath = path.join(root, 'plugins/task-progress/cindy/main.js');
const original = fs.readFileSync(sourcePath, 'utf8');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-fault-coverage-'));
const faults = [
  {
    name:'truthy success flag', file:'test_cindy_host_archive_edges.cjs', pattern:'invalid acknowledgment.*truthy',
    before:'if (response?.ok !== true || response.result?.session_id !== sid || response.result?.archived !== true)',
    after:'if (!response?.ok || response.result?.session_id !== sid || response.result?.archived !== true)',
    finding:'invalid reply must not authorize cleanup'
  },
  {
    name:'bypass binding check', file:'test_cindy_host_archive_edges.cjs', pattern:'enqueue waits for persistence',
    before:'if (!syncedHostArchives.has(key) && current.taskProgressSessionsByHostSession?.[host] !== sid) {',
    after:'if (false) {', finding:'serialized binding repair must be respected before dispatch'
  },
  {
    name:'skip durable cleanup', file:'test_cindy_host_archive_consistency.cjs', pattern:'one runtime serializes concurrent',
    before:'await acknowledgeHostArchive(key, entry);', after:'pendingHostArchives.delete(key);',
    finding:'confirmed send must remove the durable intent'
  },
  {
    name:'erase replaced queue record', file:'test_cindy_host_archive_consistency.cjs', pattern:'replacement queue generation',
    before:"if (stored.observed_at !== entry.observed_at) throw new Error('Archive queue entry changed');",
    after:'// negative control: ignore replacement record',
    finding:'old acknowledgment must preserve the replacement intent'
  }
];
try {
  const cleanEnv = {...process.env}; delete cleanEnv.TASK_PROGRESS_TEST_SOURCE;
  const baseline = spawnSync(process.execPath, ['--test','--test-reporter=tap',
    'tests/test_cindy_host_archive_edges.cjs','tests/test_cindy_host_archive_consistency.cjs'],
    {cwd:root,env:cleanEnv,encoding:'utf8',timeout:30000});
  assert.equal(baseline.status,0,baseline.stdout + baseline.stderr);
  assert.match(baseline.stdout,/# pass 19\b/);
  console.log('PASS: unmodified production source, 19 test cases');
  for (const [index, fault] of faults.entries()) {
    assert.equal(original.split(fault.before).length - 1,1,'mutation target must be unique: '+fault.name);
    const file = path.join(temporary, 'main-' + index + '.js');
    fs.writeFileSync(file,original.replace(fault.before,fault.after));
    const result = spawnSync(process.execPath,['--test','--test-reporter=tap',
      '--test-name-pattern='+fault.pattern,'tests/'+fault.file],
      {cwd:root,env:{...cleanEnv,TASK_PROGRESS_TEST_SOURCE:file},encoding:'utf8',timeout:15000});
    assert.equal(result.error,undefined,'negative control must finish without process timeout');
    assert.equal(result.status,1,'test must reject injected fault: '+fault.name+'\n'+result.stdout+result.stderr);
    assert((result.stdout+result.stderr).includes(fault.finding),
      'fault must fail the intended assertion, not an unrelated setup path: '+fault.name+'\n'+result.stdout+result.stderr);
    console.log('DETECTED: '+fault.name+' → '+fault.finding);
  }
  assert.equal(fs.readFileSync(sourcePath,'utf8'),original,'production source must remain unchanged');
  console.log('PASS: all 4 negative controls detected; production source unchanged');
} finally {fs.rmSync(temporary,{recursive:true,force:true});}
