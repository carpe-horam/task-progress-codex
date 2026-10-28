const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const base='/Users/machengling/.codex/worktrees/3e36/task-process';
const dir='/tmp/task-progress-thread-audit-20261028';
const lines=fs.readFileSync(base+'/plugins/task-progress/app.html','utf8').split('\n');
const context={console,TextEncoder,Uint8Array,ArrayBuffer,Date,Map,Set,Number,String,Math,JSON};vm.createContext(context);
// Evaluate only exact shipped pure collector/renderer functions. No DOM, network, server or shared service.
vm.runInContext('let sessionId="";\n'+lines.slice(189,196).join('\n')+'\n'+lines.slice(382,387).join('\n')+'\n'+lines.slice(412,1027).join('\n')+'\nglobalThis.audit={collectExportData,buildVisualHtml};',context);
const report={};
for(const [name,file] of [['actual','progress-snapshot.json'],['all_pending','contract-pending-fixture.json']]){
 const p=JSON.parse(fs.readFileSync(dir+'/'+file,'utf8'));const d=context.audit.collectExportData(p);const html=context.audit.buildVisualHtml(d);
 fs.writeFileSync(dir+'/contract-'+name+'-export.html',html);
 const delivery=html.match(/class="rp-number rp-delivery">([^<]+)/)?.[1];
 report[name]={sourceStatus:p.status,rootStatus:p.root_status,pendingReviews:p.pending_review_count,exportPlanStatus:d.currentPlan.status,delivery,leafCount:d.leafCount,resolved:d.resolvedLeafCount,ended:d.terminalLeafCount};
 if(name==='actual'){assert.equal(d.leafCount,21);assert.equal(d.resolvedLeafCount,11);assert.equal(d.terminalLeafCount,18);assert.equal(delivery,'尚未完成');}
 else{assert.equal(p.status,'awaiting_review');assert.equal(delivery,'待汇总验收');}
}
fs.writeFileSync(dir+'/contract-export-evidence.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
