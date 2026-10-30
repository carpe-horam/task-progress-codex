/* Entirely simulated product work; no user task state or execution identity is reused. */
module.exports = base => {
 const clone=x=>JSON.parse(JSON.stringify(x));
 const at=time=>time?'2026-10-30T'+time+':00+08:00':null;
 const assignment={execution_mode:'main_session',self_execution_reason:'模拟执行者；仅用于导出验证'};
 const attempt=(id,start,end,status='finished')=>({attempt_id:id,kind:'execution',execution_status:status,outcome:status==='finished'?'resolved':null,started_at:at(start),finished_at:at(end),assignment,summary:'模拟执行记录',evidence:['fixture://simulated-only']});
 const node=(id,parent,title,start,end,status='finished',summary='按计划完成。',tries=[])=>{
  const current=tries.at(-1)||((start||end)?attempt(id+'-1',start,end,status):null);
  return {id,parent_id:parent,title,children:[],depends_on:[],dependency_policy:'resolved',execution_status:status,outcome:status==='finished'?'resolved':null,started_at:at(start),finished_at:at(end),attempt_id:current?.attempt_id||null,attempt:tries.length||(current?1:0),attempts:tries.length?tries:current?[current]:[],invalidated_results:[],summary,evidence:['fixture://simulated-only'],assignment,responsibility_version:1,review_status:null};
 };
 const tree=nodes=>{
  const map=Object.fromEntries(nodes.map(n=>[n.id,n]));
  for(const n of nodes)if(n.parent_id)map[n.parent_id].children.push(n.id);
  for(const n of nodes)if(n.children.length)for(const a of n.attempts)a.kind=n.execution_status==='cancelled'?'group_termination':'synthesis';
  return map;
 };
 const old=tree([
  node('first-root',null,'兼容性修复 · 原方案','09:48','09:50','cancelled','基线发生变化，保留排查结论并重新规划。'),
  node('reproduce','first-root','复现三类兼容性问题','09:00','09:12','finished','复现图片、文字和导入问题。'),
  node('analysis','first-root','定位问题与评估修复方案','09:12','09:32','finished','明确三个可独立修复的模块。'),
  node('old-fix','first-root','在原基线上尝试修复','09:32','09:48','cancelled','远端基线更新，停止当前实现。')
 ]);
 const nodes=tree([
  node('root',null,'兼容性修复 · 更新基线后交付',null,null,'pending','修复已完成，正在执行最终验证。'),
  node('baseline','root','同步基线与环境检查','10:24','10:25','finished','基线与环境检查完成。'),
  node('sync','baseline','同步最新 main','10:00','10:08'),
  node('baseline-test','baseline','确认基线测试通过','10:08','10:22'),
  node('fixes','root','并行修复三个模块','11:12','11:15','finished','三项修复与交叉验证已验收。'),
  node('images','fixes','修复图片裁切偏移','10:10','10:32'),
  node('text','fixes','修复文字布局与换行','10:40','10:52','finished','首轮失败；等待依赖后重试通过。',[
   attempt('text-1','10:15','10:27','failed'),attempt('text-2','10:40','10:52')]),
  node('import','fixes','修复文件导入兼容性','10:20','10:55'),
  node('cross-check','fixes','交叉验证与截图复核','11:09','11:10','finished','验证与截图复核通过。'),
  node('cases','cross-check','运行关键回归用例','10:55','11:05'),
  node('screenshots','cross-check','核对修复前后截图','10:58','11:08'),
  node('gate','root','最终检查与交付准备',null,null,'pending','构建和评审完成；验证重试中。'),
  node('build','gate','构建产物','11:20','11:32'),
  node('reviews','gate','汇总代码评审','11:25','11:50'),
  node('runtime','gate','最终运行验证','12:00',null,'running','首轮环境超时；12:00 开始重试。',[
   attempt('runtime-1','11:35','11:48','failed'),attempt('runtime-2','12:00',null,'running')]),
  node('delivery','root','提交交付与远端核对',null,null,'pending','等待最终运行验证通过。')
 ]);
 nodes.delivery.depends_on=['gate'];
 const progress={...clone(base),task_title:'兼容性修复 · 执行过程演示（模拟数据）',created_at:at('09:00'),updated_at:at('12:15'),epoch:1,root_id:'root',nodes,task_history:[],history:[{epoch:0,root_id:'first-root',task_title:'兼容性修复 · 原方案',nodes:old,created_at:at('09:00'),updated_at:at('09:50'),closed_at:at('09:50'),reason:'main 基线更新；先同步基线，再并行修复三个模块，并增加交叉验证。'}]};
 delete progress.closed_at;delete progress.archived_at;
 return {progress,expected:{axis:[at('09:00'),at('12:15')],ranges:{'兼容性修复 · 原方案':['09:00','09:50'],'兼容性修复 · 更新基线后交付':['10:00','12:15'],'同步基线与环境检查':['10:00','10:25'],'并行修复三个模块':['10:10','11:15'],'修复文字布局与换行':['10:15','10:52'],'交叉验证与截图复核':['10:55','11:10'],'最终检查与交付准备':['11:20','12:15'],'最终运行验证':['11:35','12:15']},at}};
};
