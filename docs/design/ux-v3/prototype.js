'use strict';
// Offline UX fixture only. No MCP, network requests, agent calls or real task mutations.
const $ = id => document.getElementById(id);
const esc = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const state = {scenario:'running', plans:[], answer:null, expanded:{}};
const names = ['复现坐标轴问题','确认修复方案','实施代码修复','检查相关回归','检查兼容表现','整理交付说明'];
const demoSessionId = 'demo-session';
const currentTaskId = 'demo-axis-fix';
// Separate collections: archived task instances vs old plans of this task.
const archivedTasks = [
  {session_id:demoSessionId,task_instance_id:'demo-reading',title:'整理本周读书笔记',archived_at:'今天 13:40',outcome:'resolved',summary:'读书笔记已按主题整理，摘要与原文出处已保存。',nodes:[{title:'整理笔记材料',status:'resolved'},{title:'归纳主题并核对出处',status:'resolved'}],plans:[]},
  {session_id:demoSessionId,task_instance_id:'demo-downloads',title:'清理下载目录的重复文件',archived_at:'今天 11:20',outcome:'resolved',summary:'已按文件内容核对重复项，保留清理清单。',nodes:[{title:'按内容校验识别重复项',status:'resolved'},{title:'清理已确认重复文件',status:'resolved'}],plans:[{epoch:0,reason:'仅按文件名判断不可靠，改为按内容校验。',nodes:[{title:'按文件名识别重复项',status:'open'},{title:'清理重复文件',status:'cancelled'}]}]}
];
function hasReplan(){return state.plans.length>0;}
const labels = {resolved:'已解决',running:'进行中',pending:'待前序完成',open:'仍未解决',cancelled:'已取消',unknown:'状态待确认',paused:'等待继续'};
const scenarios = {
  running:{label:'正在核验',tone:'blue',summary:'修复已完成，正在检查兼容表现。',current:'检查兼容表现',reason:'回归检查已通过，正在复核导出文件。',next:'整理交付说明',condition:'兼容核验通过后开始',worker:'验证助手 · 已运行 2 分 14 秒'},
  simple:{label:'正在整理',tone:'blue',summary:'已读完两份方案，正在归纳主要差异。',current:'归纳差异与建议',reason:'把功能差异与实际使用成本整理成对照表。',next:'检查结论与来源',condition:'对照表形成后开始',worker:'主会话 · 已运行 46 秒'},
  input:{label:'需要你选择',tone:'amber',summary:'执行范围需要一个选择，后续核验暂未开始。'},
  submitted:{label:'等待继续执行',tone:'blue',summary:'你的选择已提交，正在等待执行器继续。'},
  pausing:{label:'暂停收尾中',tone:'amber',summary:'已暂停新增工作，等待 1 项在途核验结束。',current:'检查兼容表现',reason:'该核验仍在运行。现有结果会保留，下一项暂不启动。',next:'整理交付说明',condition:'已暂停，不会自动开始',worker:'验证助手 · 仍在运行'},
  paused:{label:'已暂停',tone:'neutral',summary:'当前执行已结束，4 项已解决结果已保留。'},
  stopping:{label:'正在停止',tone:'amber',summary:'停止请求已提交，仍有 1 项执行等待确认。',current:'检查兼容表现',reason:'等待执行器确认终止。不会释放其占用或重复启动。',next:'保存已有结果',condition:'确认停止后结算',worker:'验证助手 · 停止待确认'},
  cancelled:{label:'已取消',tone:'neutral',summary:'所有执行已结算，保留 4 项已完成结果，2 项已取消。'},
  unknown:{label:'执行状态待确认',tone:'amber',summary:'最近一次确认在 5 分钟前，当前是否仍在执行尚未知。',current:'检查兼容表现',reason:'没有收到新的执行观察，暂不重复派发这项工作。',next:'核对现有执行',condition:'确认状态后再决定后续',worker:'验证助手 · 最近确认于 14:27'},
  synthesis:{label:'正在最终验收',tone:'blue',summary:'6 项工作已结束，正在核对整体目标与交付证据。',current:'核对最终交付',reason:'逐项确认修复、兼容表现与交付材料是否满足目标。',next:'形成最终结论',condition:'所有要求通过后才能完成',worker:'主会话 · 正在综合验收'},
  done:{label:'已完成',tone:'green',summary:'修复与兼容核验均已通过，交付材料已整理完成。'},
  open:{label:'仍有未解决项',tone:'amber',summary:'本轮执行已结束，导出兼容仍有 1 项未满足。'},
  replan:{label:'正在调整方案',tone:'blue',summary:'保留上一轮 2 项证据，按新的 5 项计划继续核验。',current:'复核已有证据',reason:'已保留复现材料和回归结果；新方案仍需独立验证。',next:'检查替代实现',condition:'已有证据复核后开始',worker:'主会话 · 计划第 2 轮'},
  empty:{label:'暂无任务',tone:'neutral',summary:''},
  error:{label:'状态待更新',tone:'amber',summary:'以下为上次有效快照，暂时无法获取新的执行状态。',current:'检查兼容表现',reason:'上次确认：回归检查已通过，正在复核导出文件。',next:'整理交付说明',condition:'当前状态需重新确认',worker:'验证助手 · 上次确认于 14:32'}
};
function nodes(){
  const s=state.scenario;
  if(s==='simple')return ['阅读两份方案','归纳差异与建议','检查结论与来源'].map((title,i)=>({id:i,title,status:['resolved','running','pending'][i]}));
  if(hasReplan())return ['复核已有证据','检查替代实现','实现兼容修复','验证导出表现','整理交付'].map((title,i)=>{
    let status=i===0?'running':'pending';
    if(['input','submitted'].includes(s))status='pending';
    if(s==='paused'&&i===0)status='paused';
    if(s==='cancelled')status='cancelled';
    if(s==='unknown'&&i===0)status='unknown';
    if(['done','synthesis'].includes(s))status='resolved';
    if(s==='open')status=i===3?'open':'resolved';
    return {id:i,title,status};
  });
  return names.map((title,i)=>{
    let status=i<4?'resolved':i===4?'running':'pending';
    if(['input','submitted'].includes(s))status=i<3?'resolved':'pending';
    if(s==='paused')status=i<4?'resolved':i===4?'paused':'pending';
    if(s==='cancelled')status=i<4?'resolved':'cancelled';
    if(s==='unknown'&&i===4)status='unknown';
    if(['done','synthesis'].includes(s))status='resolved';
    if(s==='open')status=i===4?'open':'resolved';
    return {id:i,title,status};
  });
}
function count(){const n=nodes();return {total:n.length,ended:n.filter(x=>['resolved','open','cancelled'].includes(x.status)).length,resolved:n.filter(x=>x.status==='resolved').length,open:n.filter(x=>x.status==='open').length};}
function title(){return state.scenario==='simple'?'比较两份实现方案':'修复图表坐标轴显示';}
function pill(text,tone='neutral'){return `<span class="pill ${tone}">${esc(text)}</span>`;}
function button(text,action,cls='',extra=''){return `<button type="button" class="${cls}" data-action="${action}" ${extra}>${text}</button>`;}
function notify(text){$('announcer').textContent=text;$('toast').textContent=text;$('toast').hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>$('toast').hidden=true,4500);}
function setScenario(next){
  if(next==='replan'&&!hasReplan())state.plans.push({task_instance_id:currentTaskId,epoch:0,closed_at:'今天 14:30',reason:'原方案未满足导出兼容要求，调整实现路线。',summary:'保留复现材料和回归证据；旧方案不作为新方案已通过的证明。',nodes:names.map((title,i)=>{const n={id:i,title,status:i===4?'open':'resolved'};return {...n,record:nodeRecord(n)};})});
  state.scenario=next;$('scenario').value=next==='submitted'?'input':next;render();
}
function resetScenario(next){state.answer=null;state.plans=[];state.expanded={};setScenario(next);}
function render(){
  const s=scenarios[state.scenario],c=count(),empty=state.scenario==='empty';
  const actionBar=empty?'':`<div class="header-actions">${button('导出','export','small')}${button('刷新','refresh','small')}${button('•••','more','small icon-button','aria-label="更多操作"')}</div>`;
  const progress=empty?'':`<div class="task-title"><h2>${esc(title())}</h2>${pill(s.label,s.tone)}</div><div class="progress-line"><strong>${Math.round(c.ended/c.total*100)}<small>%</small></strong><span>${c.ended} / ${c.total} 项执行结束</span></div><div class="segments" role="progressbar" aria-label="当前计划任务执行结束比例" aria-valuemin="0" aria-valuemax="${c.total}" aria-valuenow="${c.ended}" aria-valuetext="${c.ended} 项执行结束，不代表整体目标已验收">${nodes().map(n=>`<span class="${n.status==='resolved'?'ended':n.status==='open'?'unresolved':n.status==='cancelled'?'cancelled':''}"></span>`).join('')}</div><div class="compact-meta"><span>计划第 ${state.plans.length+1} 轮</span><span>已解决 ${c.resolved}</span><span>进行中 ${nodes().filter(n=>n.status==='running').length}</span><span>${c.open?'未解决 '+c.open:'待执行 '+nodes().filter(n=>['pending','paused'].includes(n.status)).length}</span></div>`;
  $('card').innerHTML=`<header class="card-header"><div class="brand-row"><span class="brand"><span class="brand-dot" aria-hidden="true"></span>Task Progress</span>${actionBar}</div>${progress}</header>${empty?emptyView():`${attention()}<section class="tree-section" aria-label="当前任务树"><div class="section-heading"><h3>当前任务</h3><span>${c.total} 个子任务 · 点击行展开详情</span></div><details id="task-root" class="task-root" data-keep="root" ${state.expanded.root===false?'':'open'}><summary><span class="root-icon" aria-hidden="true">◇</span><strong>${state.scenario==='simple'?'方案比较':hasReplan()?'替代方案实施与验证':'修复与验证'}</strong><span>${c.ended} / ${c.total}</span></summary><ol class="task-tree">${nodes().map(nodeRow).join('')}</ol>${rootSummary()}</details></section>${historySections()}<footer class="card-footer"><span>${state.scenario==='unknown'?'执行确认于 14:27 · 状态待核对':state.scenario==='error'?'上次快照 14:32 · 读取失败':'执行状态更新于 14:32'}</span>${['done','open','cancelled'].includes(state.scenario)?'<span>记录已保存</span>':state.scenario==='paused'?'<span>当前无在途执行</span>':['pausing','stopping','unknown','error'].includes(state.scenario)?'':button('暂停新增工作','pause','text-button small')}</footer>`}`;
  const observable=['pausing','stopping','unknown','synthesis','submitted'].includes(state.scenario);
  $('observe').hidden=!observable;
  $('observe').textContent=state.scenario==='stopping'?'模拟已确认停止':state.scenario==='unknown'?'模拟执行已结束':state.scenario==='synthesis'?'模拟最终验收通过':state.scenario==='submitted'?'模拟任务继续':'模拟在途任务已结束';
}
function emptyView(){return `<section class="empty-state"><h3>当前会话暂无任务</h3><p>任务开始后，进度与任务树会显示在这里。</p>${button('查看任务示例','example','small')}</section>`;}
function attention(){
  const lines={
    input:['需要你选择验收范围，已有结果保留。','选择范围','question','amber'],
    submitted:['选择已提交，等待执行器继续。','','','neutral'],
    pausing:['已暂停新增工作，1 项执行仍在收尾。','请求停止','stop','amber'],
    paused:[`已暂停；${count().resolved} 项结果保留，当前执行报告尚待验收。`,'继续推进','resume','neutral'],
    stopping:['停止请求已提交，等待 1 项执行确认终止。','核对状态','reconcile','amber'],
    cancelled:[`${count().resolved} 项结果已保留，另 ${nodes().filter(n=>n.status==='cancelled').length} 项已取消。`,'查看保留内容','root-summary','neutral'],
    unknown:['执行状态停留在 14:27，未重复派发。','核对状态','reconcile','amber'],
    synthesis:['全部子任务已结束，正在最终验收。','查看整体说明','root-summary','blue'],
    done:['最终验收通过，交付材料已保存。','查看交付','delivery','green'],
    open:['仍有 1 项未解决：导出标签间距。','查看未完成项','issue-node','amber'],
    replan:['新计划 5 项，保留旧证据并重新验收。','查看变化','changes','blue'],
    error:['显示 14:32 快照；暂时无法更新状态。','重试读取','refresh','red']
  };
  const item=lines[state.scenario];if(!item)return '';
  return `<div class="attention ${item[3]}" role="status"><span>${item[0]}</span>${item[1]?button(item[1],item[2],'text-button small'):''}</div>`;
}
function rowSubtitle(n){
  if(n.status==='running')return `${(state.scenario==='simple'||hasReplan())?'主会话':'验证助手'} · ${state.scenario==='stopping'?'等待终止确认':state.scenario==='pausing'?'仍在运行':'进行中'}${state.scenario==='simple'?' · 46 秒':hasReplan()?'':' · 2 分 14 秒'}`;
  if(n.status==='pending')return ['input','submitted'].includes(state.scenario)&&n.id===(hasReplan()?0:3)?(state.scenario==='submitted'?'选择已提交，等待继续':'等待：验收范围选择'):`等待：${nodes()[n.id-1]?.title||'开始执行'}`;
  if(n.status==='unknown')return '最近确认于 14:27，当前状态未知';
  if(n.status==='paused')return '执行报告已返回，等待继续核验';
  if(n.status==='open')return '导出后的标签间距仍不符合要求';
  return '';
}
function nodeRow(n){
  const info=rowSubtitle(n),name='node-'+n.id;
  const icon=n.status==='resolved'?'✓':n.status==='running'?'●':n.status==='open'?'!':n.status==='cancelled'?'×':n.id+1;
  const label=n.status==='resolved'?'已解决':n.status==='pending'?'等待':labels[n.status];
  return `<li class="task-item ${n.status}"><details id="${name}" data-keep="${name}" ${state.expanded[name]?'open':''}><summary class="node-row"><span class="node-icon ${n.status}" aria-hidden="true">${icon}</span><span class="node-copy"><strong>${esc(n.title)}</strong>${info?`<small>${esc(info)}</small>`:''}</span><span class="node-status ${n.status}">${label}<span class="chevron" aria-hidden="true">›</span></span></summary><div class="node-detail">${nodeBody(n)}</div></details></li>`;
}
// Each fixture describes only the work owned by that row. Never infer success from an artifact.
const nodeContent = {
  '复现坐标轴问题':['复现问题并记录触发条件。','相同输入能重复触发问题。','已确认触发条件并记录复现步骤。','复现步骤.md','重复复现检查'],
  '确认修复方案':['明确修改范围和兼容影响。','方案覆盖已确认的问题。','已确认修改位置和兼容检查范围。','修复方案.md','方案范围核对'],
  '实施代码修复':['按确认方案修改相关代码。','修改符合方案，能进入后续检查。','已完成相关代码修改，后续测试另行记录。','代码修改说明.md','修改范围核对'],
  '检查相关回归':['检查此次修改是否影响已有功能。','相关回归未发现新增失败。','相关回归检查已通过。','回归检查记录.md','相关回归检查'],
  '检查兼容表现':['检查编辑器与导出文件的显示。','两处表现均满足本轮范围。','编辑器与导出文件的兼容检查已通过。','兼容检查记录.md','导出标签间距检查'],
  '整理交付说明':['整理修改说明并引用各项工作记录。','说明完整，未完成事项明确。','已整理交付说明，并列出各项工作的结论。','交付说明.md','说明完整性核对'],
  '阅读两份方案':['阅读并整理两份方案的要点。','要点对应各自的原文出处。','两份方案的要点与出处已整理。','方案阅读笔记.md','原文出处核对'],
  '归纳差异与建议':['归纳方案差异及适用场景。','比较项和建议有明确理由。','差异与建议已整理。','方案对照表.md','比较项与建议核对'],
  '检查结论与来源':['核对比较结论与引用。','没有遗漏影响选择的条件。','结论和引用已完成核对。','结论核对记录.md','结论来源核对'],
  '复核已有证据':['确认旧材料可用于新方案的部分。','标明可复用范围，不继承旧验收。','旧材料的适用范围已确认。','旧材料复核说明.md','旧材料适用范围检查'],
  '检查替代实现':['检查替代实现的可行性。','实现路线满足当前约束。','替代实现的可行性已确认。','替代实现说明.md','替代方案核对'],
  '实现兼容修复':['按新方案实现兼容修复。','实现符合本轮方案。','新方案实现已完成，等待对应检查。','兼容修复说明.md','新方案修改范围核对'],
  '验证导出表现':['验证新方案的导出显示。','导出标签间距满足要求。','新方案的导出表现检查已通过。','导出检查记录.md','导出标签间距检查'],
  '整理交付':['整理新方案交付说明。','说明与本轮检查记录一致。','本轮交付说明已整理。','新方案交付说明.md','交付内容核对']
};
function nodeRecord(n){
  const [goal,acceptance,done,output,check]=nodeContent[n.title];
  const status=n.status,complete=status==='resolved',hasWork=['resolved','running','open','paused','unknown'].includes(status);
  const summary=complete?done:status==='open'?'已完成检查，但导出标签间距仍不符合要求。':status==='pending'?'尚未开始，没有产出。':status==='cancelled'?'本项已取消，没有已确认产出。':status==='paused'?'执行记录已返回，尚待核对；没有确认本项完成。':status==='unknown'?'以下是最近保存的草稿，当前执行是否结束尚未确认。':'正在处理，已有内容仍是草稿。';
  const remaining=complete?null:status==='open'?'修复导出标签间距后，重新检查。':status==='pending'?rowSubtitle(n):status==='cancelled'?'本项未完成，取消后不再继续。':status==='unknown'?'先确认现有执行状态，再决定后续。':status==='paused'?'继续后核对执行记录和完成条件。':'完成本项工作并核对完成条件。';
  return {scope:{task_instance_id:currentTaskId,epoch:state.plans.length,node_id:n.id},goal,acceptance,summary,remaining,
    outputs:hasWork?[{id:'output',title:output,state:complete?'已保存':status==='open'?'部分完成':status==='unknown'?'最近保存的草稿':'草稿',content:summary}]:[],
    checks:hasWork?[{id:'check',title:check,status:complete?'已通过':status==='open'?'未通过':status==='unknown'?'待确认':'待检查',detail:complete?acceptance:status==='open'?'观察到导出标签间距不符合要求。':'尚无本项通过的结论。'}]:[]};
}
function nodeBody(n){
  const prev=nodes()[n.id-1],r=nodeRecord(n),owner=`data-node="${n.id}" data-epoch="${state.plans.length}"`;
  return `<p>${esc(r.summary)}</p><dl class="detail-grid"><dt>目标</dt><dd>${esc(r.goal)}</dd><dt>完成条件</dt><dd>${esc(r.acceptance)}</dd></dl>${r.outputs.length?`<div class="node-outputs"><h4>产出</h4>${r.outputs.map(o=>`<button class="node-output" data-action="artifact" ${owner} data-ref="${o.id}"><span>${esc(o.title)}</span><small>${esc(o.state)} ↗</small></button>`).join('')}</div>`:''}${r.remaining?`<p class="node-remaining"><strong>还没完成：</strong>${esc(r.remaining)}</p>`:''}${r.checks.length?`<div class="node-checks"><h4>检查记录</h4>${r.checks.map(c=>`<button class="node-output" data-action="evidence" ${owner} data-ref="${c.id}"><span>${esc(c.title)}</span><small>${esc(c.status)} ↗</small></button>`).join('')}</div>`:''}<div class="detail-actions">${prev?button('查看前序','previous','text-button small',`data-prev="${prev.id}"`):''}${n.status==='open'?button('调整方案','replan','text-button small'):''}${hasReplan()&&n.id===0?button('查看旧方案材料','changes','text-button small'):''}</div>${techDetails()}`;
}
function rootConclusion(){
  const c=count(),s=state.scenario;
  return s==='done'?'整体任务已确认完成，各项产出保存在对应任务项中。':s==='synthesis'?'各项工作已结束，整体任务仍在最终确认。':s==='open'?`本轮已结束，仍有 ${c.open} 项未解决，详见对应任务项。`:s==='cancelled'?`任务已停止，${c.resolved} 项已解决结果保留，其余工作未完成。`:hasReplan()?'当前方案仍在推进。旧方案材料只作参考，不代表本轮完成。':s==='simple'?'方案比较仍在推进，结论尚未最终确认。':'当前任务仍在推进，整体结论尚未确认。';
}
function rootSummary(){
  const links=nodes().filter(n=>['resolved','open','cancelled'].includes(n.status));
  return `<details id="root-summary-details" class="root-summary" data-keep="root-summary" ${state.expanded['root-summary']?'open':''}><summary>整体说明 <span>${state.scenario==='done'?'已完成':'尚未确认整体完成'}</span></summary><div class="snapshot-body"><p>${rootConclusion()}</p>${links.length?`<p class="small-text muted">展开对应任务项查看具体内容：</p><div class="root-node-links">${links.map(n=>button(n.title,'open-node','text-button small',`data-node="${n.id}"`)).join('')}</div>`:''}</div></details>`;
}
function openNode(id){
  const root=$('task-root'),el=$('node-'+id);if(!root||!el)return;
  root.open=true;state.expanded.root=true;el.open=true;state.expanded[el.id]=true;
  el.querySelector('summary')?.focus();el.scrollIntoView?.({block:'nearest'});
}
function showNodeRecord(kind,target){
  if(!target||Number(target.dataset.epoch)!==state.plans.length){notify('这条记录属于旧方案，请从历史计划查看。');return;}
  const n=nodes().find(n=>n.id===Number(target.dataset.node));if(!n)return;
  const r=nodeRecord(n),item=(kind==='artifact'?r.outputs:r.checks).find(item=>item.id===target.dataset.ref);if(!item)return;
  showDialog(`${esc(item.title)} · 演示`, `<p><strong>所属任务项：</strong>${esc(n.title)}</p><p>当前计划第 ${r.scope.epoch+1} 轮 · ${esc(item.state||item.status)}</p><p>${esc(item.content||item.detail)}</p><p class="small-text muted">这是设计预览，没有真实文件或测试运行。正式版本在这里打开该任务项保存的对应记录。</p>`);
}
function questionDialog(){
  showDialog('选择本轮验收范围',`<p>是否包含导出文件？选择会影响后续核验，已有结果保留。</p><form id="scope-form" class="inline-form"><label class="choice"><input type="radio" name="scope" value="编辑器与导出文件" required><span><strong>编辑器与导出文件</strong><small>覆盖完整使用流程。</small></span></label><label class="choice"><input type="radio" name="scope" value="仅编辑器"><span><strong>仅编辑器</strong><small>本轮不包含导出兼容验收。</small></span></label><div class="dialog-actions">${button('暂不选择','close-dialog')}<button type="submit" class="primary">提交选择</button></div></form>`,' ');
}
function techDetails(){return `<details class="tech-details"><summary>执行详情与追溯标识</summary><dl class="tech-grid"><dt>执行方式</dt><dd>${(state.scenario==='simple'||hasReplan())?'主会话':'验证助手（演示角色）'}</dd><dt>模型配置</dt><dd>未连接宿主，实际值未获取</dd><dt>执行身份</dt><dd>预览数据，不对应真实 agent 或 run</dd><dt>任务 / 尝试</dt><dd><code>demo-task / demo-attempt</code></dd><dt>来源版本</dt><dd>示例快照 · 14:32</dd></dl></details>`;}
function snapshotTree(items){return `<ol class="snapshot-tree">${items.map(n=>`<li>${n.record?`<details><summary>${esc(n.title)} <small>${esc(labels[n.status]||n.status)}</small></summary><p>${esc(n.record.summary)}</p>${n.record.remaining?`<p>当时未完成：${esc(n.record.remaining)}</p>`:''}${n.record.outputs.map(o=>`<p>产出：${esc(o.title)} · ${esc(o.state)}</p>`).join('')}${n.record.checks.map(c=>`<p>检查：${esc(c.title)} · ${esc(c.status)}</p>`).join('')}</details>`:`<span>${esc(n.title)}</span><small>${esc(labels[n.status]||n.status)}</small>`}</li>`).join('')}</ol>`;}
function historicalPlan(plan,key){return `<details class="history-entry" data-keep="${key}" ${state.expanded[key]?'open':''}><summary>第 ${plan.epoch+1} 轮计划 <span>已被替换</span></summary><div class="snapshot-body"><p class="small-text muted">${plan.closed_at?esc(plan.closed_at)+' · ':''}只读旧方案</p><p><strong>调整原因：</strong>${esc(plan.reason)}</p>${plan.summary?`<p>${esc(plan.summary)}</p>`:''}${snapshotTree(plan.nodes)}</div></details>`;}
function historySections(){
  const plans=state.plans.length?`<details id="plans-details" class="fold-section" data-keep="plans" ${state.expanded.plans?'open':''}><summary>当前任务的历史计划 <span>${state.plans.length} 轮</span></summary><div class="fold-body"><p class="small-text muted">同一任务调整方案前的旧计划，按轮次查看。</p>${state.plans.slice().reverse().map(p=>historicalPlan(p,'current-plan-'+p.epoch)).join('')}</div></details>`:'';
  const tasks=`<details id="history-details" class="fold-section" data-keep="history" ${state.expanded.history?'open':''}><summary>历史任务 <span>${archivedTasks.length} 项</span></summary><div class="fold-body"><p class="small-text muted">当前会话此前创建的其他任务。</p>${archivedTasks.map(t=>`<details class="history-entry" data-keep="${t.task_instance_id}" ${state.expanded[t.task_instance_id]?'open':''}><summary>${esc(t.title)} <span>已完成</span></summary><div class="snapshot-body"><p class="small-text muted">${esc(t.archived_at)} · 归档任务 · 只读</p><p>${esc(t.summary)}</p>${snapshotTree(t.nodes)}${t.plans.length?`<details class="archived-plans" data-keep="${t.task_instance_id}-plans" ${state.expanded[t.task_instance_id+'-plans']?'open':''}><summary>此任务的历史计划 · ${t.plans.length} 轮</summary>${t.plans.map(p=>historicalPlan(p,t.task_instance_id+'-plan-'+p.epoch)).join('')}</details>`:''}</div></details>`).join('')}</div></details>`;
  return plans+tasks;
}
function exportSnapshot(scope){
  const base={demo:true,session_id:demoSessionId,scope,snapshot:'2026-10-24T14:32:00+08:00'};
  if(scope==='history')return {...base,title:'本会话历史任务',archived_tasks:archivedTasks};
  const current={...base,task_instance_id:currentTaskId,title:title(),scenario:state.scenario,status:scenarios[state.scenario].label,summary:rootConclusion()};
  if(scope==='plans')return {...base,task_instance_id:currentTaskId,title:'当前任务的历史计划',plans:state.plans};
  if(scope==='full')Object.assign(current,{conclusion:rootConclusion(),nodes:nodes().map(n=>({...n,record:nodeRecord(n)})),plans:state.plans});
  return current;
}
let dialogSource=null;
function showDialog(heading,body,actions=''){if(!$('dialog').open)dialogSource=document.activeElement;$('dialog-content').innerHTML=`<h2 id="dialog-title">${heading}</h2>${body}<div class="dialog-actions">${actions||button('关闭','close-dialog')}</div>`;if(!$('dialog').open)$('dialog').showModal();}
function closeDialog(){if($('dialog').open)$('dialog').close();}
$('dialog').addEventListener('close',()=>{if(dialogSource?.isConnected)dialogSource.focus();});
function handle(action,target){
  if(['root-summary','history','plans'].includes(action)){const el=$(action+'-details');if(el){if(action==='root-summary'){$('task-root').open=true;state.expanded.root=true;}el.open=true;state.expanded[action]=true;el.querySelector('summary')?.focus();}return;}
  if(action==='pause'){setScenario('pausing');notify('已暂停新增工作；1 项在途核验仍在运行。');}
  if(action==='resume'){setScenario('running');notify('演示：已核对执行状态，继续推进当前任务。');}
  if(action==='question')questionDialog();
  if(action==='refresh')notify(state.scenario==='error'?'演示：读取仍然失败，已保留上次有效快照。':state.scenario==='unknown'?'页面已读取；执行状态仍需核对，未刷新执行心跳。':'演示快照未变化；没有触发新的执行。');
  if(action==='reconcile')notify('演示：仍未确认执行是否结束，没有重复派发。');
  if(action==='previous')openNode(target.dataset.prev);
  if(action==='open-node')openNode(target.dataset.node);
  if(action==='delivery')openNode(nodes().at(-1).id);
  if(action==='issue-node'){const issue=nodes().find(n=>n.status==='open');if(issue)openNode(issue.id);}
  if(action==='execution')showDialog('执行详情',`<p>当前活动：${esc(scenarios[state.scenario].current||'任务已结算')}。</p><p>所有身份均为预览示例。正式版本从宿主读取实际执行状态和配置，未知值不会猜测。</p>${techDetails()}`);
  if(action==='more')showDialog('任务操作',`<p>选择要查看或操作的内容。</p><div class="dialog-menu">${button('导出任务记录','export')}${button('执行详情与来源','execution')}${['done','cancelled','open','error','empty'].includes(state.scenario)?'':button('请求停止任务','stop','danger')}</div>`);
  if(action==='stop'){const active=nodes().filter(n=>['running','unknown'].includes(n.status)).length;showDialog('请求停止任务？',active?`<p>将停止新增工作，并向当前 ${active} 项执行发送停止请求。已有结果和证据会保留。</p><p>收到真实终止确认前，仍显示“正在停止”。</p>`:'<p>当前没有在途执行，将取消尚未完成的工作。已有结果和证据保留。</p>',button('返回','close-dialog')+button('确认请求停止','confirm-stop','danger'));}
  if(action==='confirm-stop'){const active=nodes().some(n=>['running','unknown'].includes(n.status));closeDialog();setScenario(active?'stopping':'cancelled');notify(active?'已提交停止请求，尚未确认执行终止。':'没有在途执行，剩余任务已取消。');}
  if(action==='replan')showDialog('调整兼容实现方案','<p>保留：复现材料、相关回归证据。</p><p>新增：替代实现检查、导出验证。上一轮导出验收不会继承为通过。</p><p>这是演示中的方案选择，不会启动真实工作。</p>',button('先不调整','close-dialog')+button('采用此方案','confirm-replan','primary'));
  if(action==='confirm-replan'){closeDialog();setScenario('replan');notify('演示：同一任务已采用第 2 轮计划，旧方案已保存到当前任务的历史计划；历史任务数量不变。');}
  if(action==='changes')handle('plans');
  if(['artifact','evidence'].includes(action))showNodeRecord(action,target);
  if(action==='example')showDialog('在聊天中描述你的目标','<p>例如：请比较这两份方案，说明差异、适用场景和你的建议。</p><p>任务开始后，插件会在同一会话展示当前活动、下一步和结果。</p>');
  if(action==='export')showDialog('导出任务记录',`<p>先选择范围。预览只导出演示数据，不读取真实历史。</p><form id="export-form" class="dialog-form"><label>导出范围<select name="scope"><option value="summary">当前任务摘要</option><option value="full">当前任务完整演示记录</option><option value="plans">当前任务的历史计划</option><option value="history">本会话历史任务</option></select></label><label>格式<select name="format"><option value="md">Markdown 预览</option><option value="json">JSON 预览</option></select></label><p>包含：所选快照的状态与结果摘要。正式产品的 HTML/Excel 导出在后续实现。</p><div class="dialog-actions">${button('取消','close-dialog')}<button class="primary" type="submit">导出演示快照</button></div></form>`, ' ');
  if(action==='close-dialog')closeDialog();
}
document.addEventListener('click',event=>{
  const target=event.target.closest('button');if(!target)return;
  if(target.dataset.action)handle(target.dataset.action,target);
});
document.addEventListener('toggle',event=>{const key=event.target.dataset?.keep;if(key)state.expanded[key]=event.target.open;},true);
document.addEventListener('submit',event=>{
  if(event.target.id==='scope-form'){event.preventDefault();state.answer=new FormData(event.target).get('scope');closeDialog();setScenario('submitted');notify('选择已提交，等待执行器继续。');}
  if(event.target.id==='export-form'){event.preventDefault();const data=new FormData(event.target),scope=data.get('scope'),format=data.get('format');const payload=exportSnapshot(scope);const text=format==='json'?JSON.stringify(payload,null,2):'# 演示快照 · '+payload.title+'\n\n仅用于 UX 预览，不是实际执行证据。\n\n```json\n'+JSON.stringify(payload,null,2)+'\n```\n';const link=document.createElement('a');const url=URL.createObjectURL(new Blob([text],{type:format==='json'?'application/json':'text/markdown;charset=utf-8'}));link.href=url;link.download='task-progress-ux-demo.'+format;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);closeDialog();notify('已生成演示快照下载；不包含真实任务数据。');}
});
$('scenario').addEventListener('change',event=>{resetScenario(event.target.value);});
$('width').addEventListener('change',event=>$('preview').classList.toggle('narrow',event.target.value==='narrow'));
$('theme').addEventListener('change',event=>document.body.dataset.theme=event.target.value);
$('reset').addEventListener('click',()=>{resetScenario($('scenario').value);notify('当前演示场景已重置。');});
$('observe').addEventListener('click',()=>{const next={pausing:'paused',stopping:'cancelled',unknown:'synthesis',synthesis:'done',submitted:'running'}[state.scenario];if(next){setScenario(next);notify('已模拟一条新的执行观察；没有操作真实任务。');}});
render();
