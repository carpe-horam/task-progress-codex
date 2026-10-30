/* Pure derivation regressions: read packaged source only, never touch session data. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../plugins/task-progress/app.html'),'utf8');
const context=vm.createContext({});
const stamp=source.slice(source.indexOf('  const timestamp ='),source.indexOf('  function appendElapsed('));
const model=source.slice(source.indexOf('  function executionTiming('),source.indexOf('  function dependencyConstraints('));
vm.runInContext(stamp+'\nconst list=value=>Array.isArray(value)?value:[];\n'+model+'\nglobalThis.timing=executionTiming;',context);
const timing=context.timing;
const T=seconds=>new Date(Date.UTC(2026,9,25,0,0,seconds)).toISOString();
const attempt=(id,start,end,status='finished',extra={})=>({attempt_id:id,kind:'execution',started_at:start==null?null:T(start),finished_at:end==null?null:T(end),execution_status:status,...extra});
const node=(extra={})=>({id:'a',execution_status:'pending',started_at:null,finished_at:null,attempts:[],...extra});
const frozen=JSON.stringify;
let value=node();assert.equal(timing(value).timeStartState,'未开始');assert.equal(timing(value).timeEndState,'尚未开始');
value=node({execution_status:'running',attempt_id:'a',started_at:T(10)});
let result=timing(value,new Map(),new Map(),T(25));assert.match(result.cumulativeTime,/15秒（运行中截至快照）/);assert.match(result.overallTime,/15秒/);assert.equal(result.timeEndState,'尚未结束');
assert.deepEqual(timing(value,new Map(),new Map(),T(25)),result,'snapshot metrics cannot depend on wall clock');
value=node({execution_status:'finished',finished_at:T(30)});result=timing(value);assert.equal(result.timeStart,'');assert.equal(result.timeStartState,'历史未记录');assert.equal(result.cumulativeComplete,false);assert.match(result.overallTime,/边界未记录/);
value=node({execution_status:'finished',attempt_id:'retry',started_at:T(100),finished_at:T(120),attempt:2,attempts:[attempt('first',0,10,'cancelled'),attempt('retry',100,120,'finished')]});
let before=frozen(value);result=timing(value);assert.equal(result.completedExecutionSeconds,'30');assert.match(result.overallTime,/120 秒/);assert.equal(frozen(value),before,'derived model must not mutate source');
// Duplicate same identity in current/attempts/invalidated_results counts once.
value.invalidated_results=[attempt('first',0,10,'cancelled',{invalidated_by:'upstream'})];result=timing(value);assert.equal(result.completedExecutionSeconds,'30');
value.invalidated_results.push(attempt(null,0,10,'cancelled'));result=timing(value);assert.equal(result.completedExecutionSeconds,'40');assert.equal(result.cumulativeComplete,false);assert.match(result.cumulativeTime,/可能重复/);
// Different overlapping IDs remain distinct executor work, never pretend wall-clock sum.
value=node({attempts:[attempt('one',0,30),attempt('two',10,40)]});result=timing(value,new Map(),new Map(),T(60));assert.equal(result.completedExecutionSeconds,'60');assert.match(result.cumulativeTime,/含重叠尝试/);assert.match(result.overallTime,/60 秒/);assert.equal(result.timeStart,'','historical attempts cannot become the current attempt');
for(const bad of [attempt('missing',null,20),attempt('missing-end',10,null),attempt('reverse',30,20),{...attempt('invalid',0,20),started_at:'2026-02-30T00:00:00Z'}]){result=timing(node({attempts:[bad]}));assert.equal(result.cumulativeComplete,false);assert.equal(result.completedExecutionSeconds,'0');assert.match(result.cumulativeTime,/记录不完整/);}
value=node({attempts:[attempt('old',0,10,'cancelled')],attempt:1});result=timing(value,new Map(),new Map(),T(100));assert.equal(result.timeStart,'');assert.match(result.timeStartState,/当前尝试未开始/);assert.match(result.overallTime,/100 秒/);assert.match(result.overallTime,/快照/);
const a=node({id:'a',execution_status:'finished',started_at:T(10),finished_at:T(30)}),b=node({id:'b',execution_status:'finished',started_at:T(20),finished_at:T(40)}),group=node({id:'g',execution_status:'finished',attempt_id:'synthesis',started_at:T(100),finished_at:T(101),attempts:[attempt('synthesis',100,101,'finished',{kind:'synthesis'})]});
const byId=new Map([['a',a],['b',b],['g',group]]),children=new Map([['g',['a','b']]]);
result=timing(group,byId,children,T(110));assert.equal(result.timeStart,T(10));assert.equal(result.timeEnd,T(101));assert.equal(result.timeKind,'derived');assert.match(result.timeSource,/推导/);assert.equal(result.completedExecutionSeconds,'0');assert.match(result.cumulativeTime,/分组不累加/);assert.equal(group.started_at,T(100),'synthesis remains independent');
b.execution_status='pending';b.started_at=null;b.finished_at=null;b.attempts=[attempt('invalidated',0,200)];result=timing(group,byId,children,T(210));assert.equal(result.timeStart,T(0));assert.equal(result.envelopeStart,T(0));assert.equal(result.envelopeEnd,T(210));assert.equal(result.timeEnd,'');assert.equal(result.timeEndState,'子任务尚未全部结束');assert.match(result.timeSource,/不完整/);
// Real historical shape: 3 open/cancelled attempts; pending reset has no final end.
value=node({attempt:3,attempts:[attempt('real-1',0,53),attempt('real-2',54,75),attempt('real-3',603,609,'cancelled')]});result=timing(value,new Map(),new Map(),T(1535));assert.equal(result.completedExecutionSeconds,'80');assert.match(result.overallTime,/1535 秒/);assert.equal(result.timeEnd,'');
console.log('time lifecycle model: pending/running/partial/retry/cancel/invalidation/duplicates/overlap/group/frozen history passed');
// Synthesis is excluded without claiming that a historical execution is missing.
const synthesisOnly=timing(group,byId,children,T(210));assert.equal(synthesisOnly.cumulativeComplete,true);
// A nested group's synthesis can outlast its leaves and a stale ancestor end.
const outer=node({id:'outer',execution_status:'finished',started_at:T(45),finished_at:T(50)});
const inner=node({id:'inner',execution_status:'finished',started_at:T(70),finished_at:T(80)});
const leaf=node({id:'leaf',execution_status:'finished',started_at:T(10),finished_at:T(30)});
const nestedMap=new Map([outer,inner,leaf].map(n=>[n.id,n])),nestedChildren=new Map([['outer',['inner']],['inner',['leaf']]]);
const outerTiming=timing(outer,nestedMap,nestedChildren,T(100)),innerTiming=timing(inner,nestedMap,nestedChildren,T(100));
assert.equal(outerTiming.envelopeStart,innerTiming.envelopeStart);assert.equal(outerTiming.envelopeEnd,innerTiming.envelopeEnd);assert.equal(outerTiming.timeEnd,T(80));
assert.equal(timing(node(),new Map(),new Map(),T(100)).envelopeEnd,'','never-started tasks have no fake snapshot interval');
const unloaded=timing(node({attempt:3,attempt_count:3,records_included:false}),new Map(),new Map(),T(100));assert.match(unloaded.cumulativeTime,/历史未加载/);assert.match(unloaded.timeStartState,/当前尝试未开始，历史待加载/);assert.match(unloaded.timeEndState,/当前尝试尚未开始/);assert.doesNotMatch(unloaded.cumulativeTime,/累计执行耗时：未开始/);
const fallback=timing(node({execution_status:'cancelled',started_at:T(10),finished_at:T(10),time_boundary_sources:{started_at:'unknown_start_fallback',finished_at:'archive_fallback'}}));
assert.equal(fallback.completedExecutionSeconds,'0');assert.equal(fallback.cumulativeComplete,false);assert.match(fallback.cumulativeTime,/暂无可计算的完整执行区间/);assert.match(fallback.timeSource,/非实际开始/);assert.match(fallback.timeSource,/结束时间为关闭时保底/);
// Former leaf with matching invalidation must keep execution kind and 80 s total.
const former=node({id:'g',attempt:3,attempts:[attempt('one',0,53),attempt('two',566,587),attempt('three',603,609,'cancelled')],invalidated_results:[{...attempt('one',0,53),kind:undefined,invalidated_by:'upstream'}]});
let formerTiming=timing(former,byId,children,T(1535));assert.equal(formerTiming.completedExecutionSeconds,'80');assert.match(formerTiming.overallTime,/25分35秒（1535 秒）/);
Object.assign(former,{attempt_id:'synth',execution_status:'finished',started_at:T(1600),finished_at:T(1601)});formerTiming=timing(former,byId,children,T(1700));assert.match(formerTiming.overallTime,/分组综合验收 \/ 收尾（非叶执行终态）/);assert.doesNotMatch(formerTiming.overallTime,/→ 当前执行结束/);
if(process.env.TASK_PROGRESS_REFERENCE_SNAPSHOT){
 const referencePath=process.env.TASK_PROGRESS_REFERENCE_SNAPSHOT;
 const raw=fs.readFileSync(referencePath,'utf8'),reference=JSON.parse(raw),progress=reference.structuredContent?.progress||reference.progress||reference;
 let checked=0;const observations=[];
 const rounds=[...progress.history,progress];
 for(const round of rounds){const nodes=Array.isArray(round.nodes)?round.nodes:Object.values(round.nodes||{}),byId=new Map(nodes.map(n=>[String(n.id),n])),children=new Map(nodes.map(n=>[String(n.id),(n.children||[]).map(String)]));
  for(const item of nodes){const original=JSON.stringify(item),result=timing(item,byId,children,round.closed_at||round.updated_at||'');assert.equal(JSON.stringify(item),original);checked++;
   if(item.id==='e30-sumi-hook'){assert.equal(result.completedExecutionSeconds,'80');assert.match(result.overallTime,/1535 秒/);assert.equal(item.started_at,null);assert.equal(item.finished_at,null);observations.push([item.id,result.cumulativeTime,result.overallTime]);}
   if(item.id==='e41-fix-qname'){assert.equal(result.completedExecutionSeconds,'229');assert.match(result.overallTime,/758 秒/);observations.push([item.id,result.cumulativeTime,result.overallTime]);}
   if(item.id==='e41-rebase-reviews'){assert.equal(result.completedExecutionSeconds,'912');assert.match(result.overallTime,/1698 秒/);observations.push([item.id,result.cumulativeTime,result.overallTime]);}
  }
 }
 assert.equal(fs.readFileSync(referencePath,'utf8'),raw);assert.ok(checked>=420);assert.equal(observations.length,3);console.log(JSON.stringify({referenceNodesChecked:checked,observations}));
}

// Unknown identity is conservative even when timestamps differ; never claim an exact total.
const ambiguous=timing(node({attempts:[attempt('known',0,10),attempt(null,20,30)]}),new Map(),new Map(),T(40));assert.equal(ambiguous.completedExecutionSeconds,'20');assert.equal(ambiguous.cumulativeComplete,false);assert.match(ambiguous.cumulativeTime,/可能重复/);
for(const invalid of ['2026-10-25T00:00:10','2026-02-30T00:00:10Z','2026-10-25T24:00:00Z','2026-10-25T00:60:00Z']){const result=timing(node({attempts:[{...attempt('bad',0,10),started_at:invalid}]}));assert.equal(result.cumulativeComplete,false);assert.equal(result.completedExecutionSeconds,'0');}
