/* Extract actual pure display functions without starting a real host session. */
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const file = path.resolve(__dirname, '../../../plugins/task-progress/app.html');
const source = fs.readFileSync(file, 'utf8');
const start = source.indexOf('  const exportExecutor =');
const end = source.indexOf('  const metadataEvidence =', start);
const context = {}; vm.createContext(context);
vm.runInContext('const list=v=>Array.isArray(v)?v:[]; const printable=String;\n'+source.slice(start,end)+'\nglobalThis.probe=responsibilityFields;', context);
for (const record of [
  {assignment:{execution_mode:'main_session'},execution_status:'finished',outcome:'resolved',children:[]},
  {assignment:{execution_mode:'main_session'},execution_status:'finished',outcome:'resolved',children:['a'],attempts:[]}
]) console.log(JSON.stringify({record,result:context.probe(record)},null,2));
