(() => {
'use strict';
const $ = id => document.getElementById(id);
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const scenarios = {
 feature:{title:'为应用加入深色模式',tasks:['确认主题与验收标准','梳理现有颜色体系','实现主题切换','验证页面与交互','整理交付记录'],owners:['主会话','分析 agent','实现 agent','验证 agent','交付 agent'],evidence:['主题方案与验收标准已确认。','现有颜色使用清单已整理，主题变量映射已检查。','实现主题变量及切换入口，等待后续交互检查。','检查桌面、移动端与键盘操作。','汇总变更、验证结果和剩余事项。']},
 bug:{title:'排查并修复导出异常',tasks:['复现并记录问题','定位异常输入','修复导出逻辑','运行针对性验证','整理修复证据'],owners:['复现 agent','分析 agent','实现 agent','验证 agent','交付 agent'],evidence:['在隔离样例中复现导出异常。','已定位边界输入与错误处理路径。','正在处理边界输入并补全错误提示。','重跑原问题样例，核对导出结果。','汇总复现步骤、修复说明与验证结果。']},
 parallel:{title:'三个子智能体，共同交付一个功能'},
 async:{title:'开发搜索，同时异步排查文档'},
 append:{title:'在交付分组中追加检查'},
 plans:{title:'重新规划数据看板的实现'},
 archive:{title:'归档前次任务，继续新目标'},
 split:{title:'把数据看板拆成可验收的小任务'},
 dependencies:{title:'等接口验收，再启动页面联调'},
 retry:{title:'退回不完整修复，带着证据重试'},
 review:{title:'协作检查一次功能变更',tasks:['确认检查范围','检查核心实现','核对文档与示例','复核各方检查结果','整理交付结论'],owners:['主会话','代码 agent','文档 agent','复核 agent','交付 agent'],evidence:['检查范围与验收条件已明确。','核心实现检查报告已提交并复核。','正在核对文档与实际行为是否一致。','分别读取并复核已提交的检查报告。','汇总检查结论并保留尚未解决事项。']}
};
let journeyStep=0;
let selected='feature', states=['done','done','running','pending','pending'], rootAccepted=false, timer=null, toastTimer;
function announce(text){$('event-message').textContent=text;}
function stopAuto(){clearInterval(timer);timer=null;$('autoplay').setAttribute('aria-pressed','false');$('autoplay').textContent='▷ 自动演示';}
// The iframe is a copy of plugins/task-progress/app.html; this page only simulates its host.
const demoFrame=$('plugin-demo'), demoSession='website-demo-only';
let nativeDemoSource,nativeDemoHeight=760,fitFrame=0,fitPass=0;
const demoStage=$('plugin-stage');
let bridgeReady=false,revision=0,snapshot=null,scenarioStarted=Date.now()-600000;
// Each frame is synthetic, deterministic task data consumed by the unchanged plugin UI.
// A group is never included in leaf progress, and submitted work is never counted as accepted.
const journeys = {
 parallel: {
  actions:['提交接口结果','提交页面结果','复核接口结果','提交文档结果','复核页面结果','复核文档结果','完成根任务综合验收'],
  notes:[
   '同一主任务已确认共同范围，3 个互不依赖的子任务分别绑定不同 agent，同时执行。这里是一个任务树，不会创建独立并行实例（模拟）。',
   '接口 agent 已提交结果，等待主会话复核；页面和文档 agent 继续运行。已验收仍为 1/4，不因提交而增加。',
   '页面 agent 也已提交；现在有 2 项待验收，文档 agent 仍在执行。每份结果独立保留证据。',
   '主会话只验收接口结果，比例从 25% 变为 50%；页面仍待复核，文档继续执行。',
   '文档 agent 已提交，页面与文档分别待验收；没有把其他 agent 的完成当作它们的验收。',
   '页面结果单独复核通过，已验收 3/4；文档仍待主会话复核。',
   '三个子智能体结果均已逐项验收，叶任务达到 100%；仍需要根任务综合验收。',
   '根任务综合验收通过。三个并行执行、分别提交与独立复核的记录均保留；页面没有调度真实 agent。'
  ],
  nodes(step){return [
   {key:'scope',title:'确认共同接口与验收范围',state:'done',owner:'主会话',offset:0},
   {key:'api',title:'实现数据接口',state:step>=3?'done':step>=1?'review':'running',owner:'接口实现 agent',offset:60,duration:120,evidenceText:'模拟接口证据：字段校验、空输入和错误码样例已核对。'},
   {key:'view',title:'实现页面交互',state:step>=5?'done':step>=2?'review':'running',owner:'页面实现 agent',offset:60,duration:180,evidenceText:'模拟页面证据：搜索交互、空状态和键盘导航已检查。'},
   {key:'docs',title:'补齐使用文档',state:step>=6?'done':step>=4?'review':'running',owner:'文档编写 agent',offset:60,duration:240,evidenceText:'模拟文档证据：安装命令、配置说明和使用示例已逐项核对。'}
  ];}
 },

 async: {
  actions:['插入独立异步任务','提交异步排查结果','提交主任务实现','复核异步排查结果','复核主任务实现','提交异步检查结果','复核异步检查结果','验收并归档异步任务','提交主任务检查','复核主任务检查','完成主任务综合验收'],
  notes:[
   '主任务正在开发搜索功能。下一步登记一个独立的异步任务，由另一个 agent 排查文档链接；不暂停主任务（模拟）。',
   '已登记独立并行实例，主任务和文档 agent 同时执行。顶部 33% 只属于主任务；并行任务在其原生详情中单独显示 0/2 项已验收。',
   '文档 agent 提交排查结果，仍待主会话复核；异步任务仍为 0/2，主任务继续执行。',
   '主任务实现也已提交。两个实例分别等待复核，不把执行结束当作验收完成。',
   '异步排查结果通过，链接检查 agent 开始执行。异步任务为 1/2；主任务仍为 1/3，比例不混算。',
   '主任务实现通过，主任务检查开始。主任务为 2/3，并行任务仍为 1/2；两个实例继续独立推进。',
   '异步检查已提交，等待主会话复核；主任务检查仍在执行。',
   '异步任务的两个叶任务已验收，仍需对它单独综合验收；主任务保持 2/3。',
   '异步任务综合验收后进入历史任务。它的完成不代表主任务完成；主任务检查仍在执行。',
   '主任务检查已提交，等待自己的复核。归档的异步结果保持不变。',
   '主任务叶任务全部验收；最后完成主任务综合验收。',
   '主任务与插入的异步任务均已分别综合验收。所有执行与宿主响应都是演示数据，页面不会调度真实 agent。'
  ],
  nodes(step){return [
   {key:'scope',title:'确认搜索功能范围',state:'done',owner:'主会话',offset:0},
   {key:'implementation',title:'实现搜索功能',state:step>=5?'done':step>=3?'review':'running',depends:['scope'],owner:'搜索实现 agent',offset:45,duration:150},
   {key:'verification',title:'检查搜索交互',state:step>=10?'done':step===9?'review':step>=5?'running':'pending',depends:['implementation'],owner:'交互检查 agent',offset:240,duration:90}
  ];}
 },

 append: {
  actions:['追加可访问性检查','提交使用说明','验收使用说明','启动追加任务','提交追加结果','验收追加结果','验收交付分组','完成综合验收'],
  notes:[
   '交付分组已有两个子任务：功能已验收，使用说明正在整理。',
   '在原分组内追加可访问性检查；叶任务从 2 项增为 3 项，已验收仍为 1 项，比例从 50% 调整为 33%。',
   '使用说明已提交，新增检查仍等待其验收结果。',
   '使用说明验收通过；追加检查依赖就绪，尚未开始。',
   '追加的可访问性检查开始执行，原有结果保持不变。',
   '追加结果已提交，等待复核。',
   '3 个叶任务均已验收；交付分组仍需综合验收。',
   '交付分组验收通过，还需要根任务综合验收。',
   '主任务综合验收通过，追加任务与原计划一起完整交付。'
  ],
  nodes(step){return [
   {key:'delivery',title:'准备功能交付',children:step?['feature','docs','accessibility']:['feature','docs'],state:step>=7?'done':step>=6?'synthesis':'group',offset:300},
   {key:'feature',parent:'delivery',title:'完成核心功能',state:'done',offset:0},
   {key:'docs',parent:'delivery',title:'整理使用说明',state:step>=3?'done':step===2?'review':'running',depends:['feature'],offset:45},
   ...(step?[{key:'accessibility',parent:'delivery',title:'补充可访问性检查',state:step>=6?'done':step===5?'review':step===4?'running':step===3?'ready':'pending',depends:['docs'],added:true,offset:150}]:[])
  ];}
 },
 plans: {
  actions:['重规划为新一轮','展开上一轮计划','提交接口结果','验收接口结果','提交界面结果','验收界面结果','提交集成结果','验收集成结果','完成综合验收'],
  notes:[
   '第 1 轮计划已收束执行：范围已确认，整体原型仍未解决。下一步将调整计划，保留这一轮的原始结果。',
   '当前进入第 2 轮，拆成接口、界面和集成检查；第 1 轮已保存在同一任务的「历史计划」中。',
   '已展开原生历史计划：旧计划仍有未解决结果，不会因为新计划推进就变成完成。',
   '第 2 轮接口结果已提交，等待验收。',
   '接口已验收，界面开始执行；上一轮计划保持只读。',
   '界面结果已提交，等待验收。',
   '界面已验收，集成检查开始执行。',
   '集成检查已提交，等待验收。',
   '第 2 轮叶任务全部验收；下一步综合验收当前计划。',
   '当前任务综合验收通过；历史计划仍保留第 1 轮未解决原型及重规划原因。'
  ],
  nodes(step){return step?[
   {key:'scope',title:'重新确认实现范围',state:'done',owner:'主会话',offset:150},
   {key:'api',title:'独立实现数据接口',state:step>=4?'done':step===3?'review':'running',depends:['scope'],offset:195},
   {key:'view',title:'独立实现页面交互',state:step>=6?'done':step===5?'review':step===4?'running':'pending',depends:['api'],offset:240},
   {key:'integration',title:'验证接口与页面集成',state:step>=8?'done':step===7?'review':step===6?'running':'pending',depends:['view'],offset:285}
  ]:[
   {key:'old-scope',title:'确认最初需求范围',state:'done',owner:'主会话'},
   {key:'prototype',title:'整体实现原型（旧计划）',state:'open',owner:'主会话',depends:['old-scope'],summary:'原型仍未解决；已收束本轮执行，后续改为接口、界面分别实现。'}
  ];}
 },
 archive: {
  actions:['验收归档并开始新任务','展开前次任务','查看历史验收记录','提交搜索结果','验收搜索结果','提交回归结果','验收回归结果','完成综合验收'],
  title(step){return step?'为新版本补充搜索能力':'整理上一次发布记录';},
  notes:[
   '前次发布任务的两个叶任务均已验收，等待最后的综合验收；它还没有归档。',
   '前次任务综合验收后归档。当前是一个独立的新任务，进度 33%；历史任务保持 100%，不会计入当前进度。',
   '已展开原生「历史任务」区域；这是前次独立任务，不是当前任务的历史计划。',
   '已展开前次任务的综合验收记录，可以继续点开叶任务的执行、验收与证据。',
   '当前搜索实现已提交，等待复核；前次任务记录保持不变。',
   '搜索实现验收通过，当前回归检查开始执行。',
   '当前回归检查已提交，等待验收。',
   '新任务的叶任务全部验收，仍需它自己的综合验收。',
   '新任务整体通过；前次发布与本次搜索分别保存各自的验收记录。'
  ],
  nodes(step){return step?[
   {key:'scope',title:'确认搜索交互范围',state:'done',owner:'主会话',offset:150},
   {key:'search',title:'实现搜索与筛选',state:step>=5?'done':step===4?'review':'running',depends:['scope'],offset:195},
   {key:'regression',title:'检查搜索回归样例',state:step>=7?'done':step===6?'review':step===5?'running':'pending',depends:['search'],offset:240}
  ]:[
   {key:'release-check',title:'核对上次发布结果',state:'done'},
   {key:'release-note',title:'整理上次发布说明',state:'done',depends:['release-check']}
  ];}
 },

 split: {
  actions:['拆分实现任务','提交接口实现','验收接口结果','提交界面实现','验收界面结果','验收实现分组','提交集成检查','验收集成检查','完成综合验收'],
  notes:[
   '实现任务尚未开始。下一步把它拆成接口与界面两个独立结果。',
   '已拆出两个嵌套子任务：叶任务从 3 项变为 4 项，已验收仍是 1 项，比例由 33% 变为 25%。',
   '接口已提交但尚未验收，界面子任务仍被依赖阻塞。',
   '接口验收通过，界面子任务开始执行。',
   '界面结果已提交，等待主会话复核。',
   '两个子结果均已验收；实现分组仍需综合验收，集成检查尚未放行。',
   '实现分组综合验收通过，依赖它的集成检查开始执行。',
   '集成检查已提交，尚未增加已验收比例。',
   '4 个叶任务全部验收通过，仍需对整体交付做综合验收。',
   '主任务综合验收通过；拆分、分组验收与整体交付形成完整模拟记录。'
  ],
  nodes(step){
   const nodes=[{key:'scope',title:'确认功能边界',state:'done',owner:'主会话'}];
   if(step===0)nodes.push({key:'build',title:'实现数据看板',state:'ready',depends:['scope']});
   else nodes.push(
    {key:'build',title:'实现数据看板',state:step>=6?'done':step>=5?'synthesis':'group',offset:240,depends:['scope'],children:['api','view']},
    {key:'api',parent:'build',title:'实现数据接口',state:step>=3?'done':step===2?'review':'running'},
    {key:'view',parent:'build',title:'实现看板界面',state:step>=5?'done':step===4?'review':step===3?'running':'pending',depends:['api']}
   );
   nodes.push({key:'check',title:'检查整体集成结果',offset:300,state:step>=8?'done':step===7?'review':step===6?'running':'pending',depends:['build']});
   return nodes;
  }
 },
 dependencies: {
  actions:['验收接口结果','启动已解锁任务','提交页面联调','验收联调结果','启动回归检查','提交回归结果','验收回归结果','完成综合验收'],
  notes:[
   '接口执行已结束，但仍待复核。页面联调依赖接口验收，因此不能启动。',
   '接口已验收；页面联调从「依赖阻塞」变为「依赖就绪」，尚未执行。',
   '页面联调已启动，回归检查继续等待它的验收结果。',
   '联调结果已提交；提交不等于验收，回归检查仍被阻塞。',
   '联调验收通过，回归检查依赖就绪，等待启动。',
   '回归检查开始执行。',
   '回归检查已提交，等待主会话复核。',
   '全部叶任务已验收；下一步是主任务综合验收。',
   '主任务综合验收通过，所有依赖都在对应结果验收后放行。'
  ],
  nodes(step){return [
   {key:'contract',title:'确认接口约定',state:'done',owner:'主会话'},
   {key:'api',title:'实现并提交数据接口',state:step>=1?'done':'review',depends:['contract']},
   {key:'integration',title:'页面联调',state:step>=4?'done':step===3?'review':step===2?'running':step===1?'ready':'pending',depends:['api']},
   {key:'regression',title:'回归检查',state:step>=7?'done':step===6?'review':step===5?'running':step===4?'ready':'pending',depends:['integration']}
  ];}
 },
 retry: {
  actions:['退回当前结果','发起第 2 次尝试','提交修正结果','验收修正结果','启动回归检查','提交回归结果','验收回归结果','完成综合验收'],
  notes:[
   '导出修复已提交，但尚未复核。下一步将发现遗漏的空数据边界。',
   '主会话退回结果：空数据样例仍失败。已验收比例不增加，回归检查继续阻塞。',
   '第 2 次尝试开始；第 1 次的退回原因和证据保留在执行与验收历史中。',
   '修正结果已重新提交，仍需复核；旧的退回记录没有被覆盖。',
   '第 2 次结果验收通过，回归检查依赖就绪。',
   '针对原问题与空数据样例运行回归检查（模拟）。',
   '回归结果已提交，等待验收。',
   '所有叶任务已验收，仍需主会话综合验收。',
   '整体验收通过；展开导出任务，可回看两次尝试及首次退回证据。'
  ],
  nodes(step){return [
   {key:'reproduce',title:'复现导出问题',state:'done'},
   {key:'export',title:'修复导出边界问题',state:step>=4?'done':step===3?'review':step===2?'running':step===1?'rejected':'review',depends:['reproduce'],retry:step>=2},
   {key:'regression',title:'检查原问题与空数据样例',offset:180,state:step>=7?'done':step===6?'review':step===5?'running':step===4?'ready':'pending',depends:['export']}
  ];}
 }
};
function makeNode(spec,index){
 const id=key=>`demo-${selected}-${key}`,state=spec.state,group=!!spec.children;
 const finished=['done','review','rejected','open'].includes(state),started=finished||state==='running';
 const main=group||spec.owner==='主会话';
 const owner=spec.owner||'执行 agent';
 const assignment={execution_mode:main?'main_session':'subagent',...(main?{self_execution_reason:group?'主会话综合验收分组结果（模拟）':'主会话直接确认范围与验收标准（模拟）'}:{agent_name:owner}),model_source:'unknown',reasoning_effort_source:'unknown'};
 const attempt=started?(spec.retry?2:1):0;
 const attemptId=started?id(spec.key)+`-attempt-${attempt}`:null;
 const start=started?new Date(scenarioStarted+(spec.offset??index*45)*1000+(spec.retry?60000:0)).toISOString():null;
 const finish=finished?new Date(Date.parse(start)+(spec.duration??30)*1000).toISOString():null;
 const rejection='空数据导出仍失败，需补齐边界处理并重新提交。';
 const summaries={open:'本轮执行已结束，结果仍未解决，后续计划继续处理。',done:`「${spec.title}」的结果已核对通过。`,running:`正在执行「${spec.title}」。`,review:`「${spec.title}」结果已提交，等待主会话复核。`,rejected:rejection,ready:'前置结果已验收，依赖就绪，尚未开始执行。',pending:'尚未开始，等待前序结果验收。',group:'已拆分为独立子任务，等待子结果完成。',synthesis:'子结果已验收，仍需主会话综合验收本分组。'};
 const summary=(spec.summary||summaries[state])+'（模拟）';
 const evidence=[`模拟证据 / ${selected} / ${spec.key} / 第 ${attempt||1} 次：${state==='rejected'?rejection:spec.retry?'空数据与原问题样例已检查，等待对应验收。':spec.evidenceText||spec.summary||'用于说明工作流程的合成结果。'}（非真实执行记录）`];
 const firstExport=selected==='retry'&&spec.key==='export'&&!spec.retry;
 const submission=finished&&!main?{attempt_id:attemptId,outcome:'resolved',summary:firstExport?'常规样例导出已恢复；空数据边界尚未覆盖（模拟）。':summary,evidence:firstExport?['模拟提交证据：常规导出样例通过，未提供空数据样例结果。']:evidence,submitted_at:finish}:null;
 const review=['done','rejected'].includes(state)&&!main?{attempt_id:attemptId,decision:state==='done'?'accept':'reject',summary:state==='rejected'?rejection:'已复核模拟结果与演示验收标准。',evidence,reviewer:{execution_mode:'main_session'},reviewed_at:finish}:null;
 const node={id:id(spec.key),parent_id:spec.parent?id(spec.parent):'demo-root',title:spec.title,children:(spec.children||[]).map(id),depends_on:(spec.depends||[]).map(id),dependency_policy:'resolved',responsibility_version:1,assignment,planned_assignment:assignment,accepted_resolved:state==='done',execution_status:finished?'finished':state==='running'?'running':'pending',status:({done:'completed',review:'awaiting_review',rejected:'open',open:'open',running:'in_progress',ready:'ready',pending:'blocked',group:'in_progress',synthesis:'waiting_synthesis'})[state],outcome:state==='done'?'resolved':['rejected','open'].includes(state)?'open':null,review_status:state==='done'&&!main?'accepted':state==='review'?'pending':state==='rejected'?'rejected':'none',attempt,attempt_id:attemptId,started_at:start,finished_at:finish,summary,evidence:finished?evidence:[],submission,review,records_included:true,title_revisions:[],invalidated_results:[],reassignment_history:[],coverage_records:[]};
 if(spec.added)Object.assign(node,{is_added:true,addition_reason:'交付前补充可访问性验收（模拟）'});
 if(!main)node.dispatch={status:started?'bound':'pending',attempt_id:attemptId,verification:'not_verified_by_codex_host',source:'website_demo',...(started?{agent_name:owner,bound_at:start}:{})};
 node.attempts=started?[{...node,children:undefined,attempts:undefined,kind:group?'synthesis':'execution'}]:[];
 if(spec.retry){
  const rejected=makeNode({...spec,state:'rejected',retry:false},index);
  node.attempts.unshift(rejected.attempts[0]);
 }
 return node;
}
function makeSnapshot(options={}){
 const data=scenarios[selected],journey=journeys[selected],step=options.step??journeyStep,accepted=options.accepted??rootAccepted,now=options.asOf||new Date().toISOString(),title=options.title||(journey?.title?journey.title(step):data.title),rootId=options.rootId||'demo-root';
 const specs=options.specs||(journey?journey.nodes(step):data.tasks.map((title,i)=>({key:String(i+1),title,state:states[i],owner:data.owners[i],summary:data.evidence[i],depends:i?[String(i)]:[]})));
 const nodes=specs.map(makeNode),leaves=nodes.filter(node=>!node.children.length);
 const total=leaves.length,done=leaves.filter(node=>node.accepted_resolved).length,ended=leaves.filter(node=>node.execution_status==='finished').length;
 const percent=Math.round(done/total*100),direct=nodes.filter(node=>node.parent_id===rootId);
 const root={id:rootId,parent_id:null,title,children:direct.map(node=>node.id),depends_on:[],responsibility_version:1,assignment:{execution_mode:'main_session',self_execution_reason:'主会话综合验收（模拟）'},accepted_resolved:accepted,execution_status:accepted?'finished':'pending',status:accepted?'completed':direct.every(node=>node.accepted_resolved)?'waiting_synthesis':'in_progress',outcome:accepted?'resolved':null,review_status:'none',summary:accepted?'所有演示子任务已复核，整体结果综合验收通过（模拟）。':'子任务验收比例与主任务综合验收分别记录。',evidence:accepted?['模拟综合验收：逐项复核结果及整体交付。']:[],records_included:true,attempts:[]};
 if(accepted){Object.assign(root,{kind:'synthesis',attempt:1,attempt_id:options.rootId?options.rootId+'-synthesis':'demo-synthesis',started_at:now,finished_at:now});root.attempts=[{...root,children:undefined,attempts:undefined}];}
 const progress={schema_version:2,session_id:demoSession,task_instance_id:options.instance||(selected==='archive'&&step===0?'website-demo-previous-task':'website-demo-instance'),epoch:selected==='plans'&&step>0?1:0,revision:options.historical?1:revision,task_title:title,root_id:root.id,status:root.status,nodes:[root,...nodes],total,completed:done,percent,accepted_resolved_leaf_count:done,accepted_resolved_percent:percent,ended_leaf_count:ended,ended_percent:Math.round(ended/total*100),created_at:options.createdAt||new Date(scenarioStarted).toISOString(),updated_at:now,planning_status:'planned',records_included:true,history_included:true,history_summary:{task_count:0,plan_count:0,legacy_task_count:0},task_history:[],history:[],plan_snapshots:[],plan_revisions:[]};
 if(!options.historical&&selected==='plans'&&step>0){
  const closedAt=new Date(scenarioStarted+120000).toISOString();
  const previous=makeSnapshot({step:0,accepted:false,historical:true,asOf:closedAt});
  previous.closed_at=closedAt;previous.reason='整体原型未解决；将接口、界面与集成分开实现和验收（模拟）。';previous.status='open';
  Object.assign(previous.nodes[0],{execution_status:'finished',status:'open',outcome:'open',summary:previous.reason});
  progress.history=[previous];progress.history_summary.plan_count=1;
 }
 if(!options.historical&&selected==='archive'&&step>0){
  const archivedAt=new Date(scenarioStarted+120000).toISOString();
  const previous=makeSnapshot({step:0,accepted:true,historical:true,asOf:archivedAt});
  Object.assign(previous,{archived_at:archivedAt,archive_kind:'completed',archive_reason:'前次发布任务已综合验收，开始独立搜索任务（模拟）。'});
  progress.task_history=[previous];progress.history_summary.task_count=1;
 }
 if(!options.historical&&selected==='async'){
  const mainStatus=accepted?'completed':nodes.some(node=>node.review_status==='pending')?'pending_review':'active';
  const foreground={request_id:'demo-main-request',task_instance_id:progress.task_instance_id,task_title:title,mode:'foreground',status:mainStatus,request_text:'开发搜索功能；独立文档排查可异步执行（模拟）。',context_brief:{goal:title,resume_note:'继续搜索功能的实现、检查及综合验收。'}};
  const requests=[foreground];progress.parallel_tasks=[];
  if(step>0){
   const peerDone=step>=8,archivedAt=new Date(scenarioStarted+300000).toISOString();
   const peer=makeSnapshot({historical:true,accepted:peerDone,instance:'website-demo-async-peer',rootId:'demo-async-peer-root',title:'异步排查文档链接',createdAt:new Date(scenarioStarted+60000).toISOString(),...(peerDone?{asOf:archivedAt}:{}),specs:[
    {key:'link-audit',parent:'peer-root',title:'排查失效文档链接',state:step>=4?'done':step>=2?'review':'running',owner:'文档排查 agent',offset:60,duration:90},
    {key:'link-check',parent:'peer-root',title:'核对链接修正结果',state:step>=7?'done':step===6?'review':step>=4?'running':'pending',depends:['link-audit'],owner:'链接检查 agent',offset:180,duration:90}
   ]});
   const peerStatus=peerDone?'completed':peer.nodes.some(node=>node.review_status==='pending')?'pending_review':'active';
   Object.assign(peer,{request_id:'demo-async-request',request_status:peerStatus,revision:peerDone?1:revision,state_revision:revision});
   const anchor={task_instance_id:progress.task_instance_id,task_id:progress.root_id,resume_note:'主任务持续推进，异步完成不代表主任务完成。'};
   requests.push({request_id:peer.request_id,task_instance_id:peer.task_instance_id,task_title:peer.task_title,mode:'parallel',status:peerStatus,request_text:'并行排查文档链接，交由独立agent执行（模拟）。',reason:'与搜索实现无依赖，可隔离并行（模拟）。',return_to:accepted?null:anchor,origin_return_to:anchor,context_brief:{goal:peer.task_title},acceptance_criteria:['排查与复查结果分别提交，再由主会话综合验收。']});
   if(peerDone){delete peer.state_revision;Object.assign(peer,{archived_at:archivedAt,archive_kind:'parallel',archive_reason:'独立异步任务已完成综合验收（模拟）。'});progress.task_history=[peer];progress.history_summary.task_count=1;}
   else progress.parallel_tasks=[peer];
  }
  progress.session_work={foreground,requests,can_finish_session:accepted&&step>=8,return_to:accepted?null:{task_instance_id:progress.task_instance_id,task_id:progress.root_id,resume_note:'继续主任务；异步结果不计入主任务百分比。'},next_actions:[step===0?'演示下一步将登记独立异步实例；不会调用真实宿主。':journeys.async.notes[step],'原生面板顶部只显示主任务比例；并行根的「本任务进度」显示独立验收计数。'],verification:'caller_declared_not_host_authenticated'};
 }
 return progress;
}

function result(instance){
 const progress=instance?[snapshot,...(snapshot.parallel_tasks||[]),...snapshot.task_history].find(task=>task.task_instance_id===instance):snapshot;
 if(!progress)return {isError:true,content:[{type:'text',text:'该模拟任务实例不存在。'}]};
 return {structuredContent:{session_id:demoSession,state_revision:revision,view_sequence:revision,progress}};
}
function send(message){demoFrame.contentWindow.postMessage({jsonrpc:'2.0',...message},location.protocol === 'file:' ? '*' : location.origin);}
function publish(){if(bridgeReady)send({method:'ui/notifications/tool-result',params:result()});}
window.addEventListener('message',event=>{
 if(event.source!==demoFrame.contentWindow||(location.protocol === 'file:' ? event.origin !== 'null' : event.origin !== location.origin)||event.data?.jsonrpc!=='2.0')return;
 const {id,method,params={}}=event.data;
 const reply=value=>send({id,result:value});
 if(method==='ui/initialize')reply({protocolVersion:'2026-01-26',hostInfo:{name:'Task Progress website demo',version:'1'},hostCapabilities:{},hostContext:{theme:'dark',displayMode:'inline',availableDisplayModes:['inline']}});
 else if(method==='ui/notifications/initialized'){bridgeReady=true;send({method:'ui/notifications/tool-input',params:{arguments:{session_id:demoSession}}});publish();}
 else if(method==='ui/notifications/size-changed'){const height=Number(params.height);if(Number.isFinite(height)&&height>0&&height<100000){nativeDemoHeight=Math.ceil(height);demoFrame.style.height=`${nativeDemoHeight}px`;scheduleDemoFit();}}
 else if(method==='tools/call'){
  const args=params.arguments||{};
  if(params.name==='get_progress_view')reply(result(args.task_instance_id));
  else if(['register_ui_view','check_ui_view','unregister_ui_view'].includes(params.name))reply({structuredContent:{session_id:demoSession,view_id:args.view_id,status:'latest',is_latest:true}});
  else reply({isError:true,content:[{type:'text',text:params.name==='save_export_files'?'网页演示宿主不支持文件导出；请安装插件后导出真实任务记录。':'网页演示不支持此宿主操作。'}]});
 }else if(id!==undefined)send({id,error:{code:-32601,message:'网页演示宿主不支持此操作；请在 Codex 中使用完整插件。'}});
});
// Never shrink expanded details to fit: budget against the ordinary 584px card.
// The 88% floor keeps desktop text legible; shorter windows scroll naturally.
function fitDemo(){
 fitFrame=0;
 let scale=1;
 if(innerWidth>1000){
  const demo=$('demo').getBoundingClientRect(),stage=demoStage.getBoundingClientRect();
  const end=document.querySelector('.demo-disclaimer').getBoundingClientRect();
  const nav=document.querySelector('.nav-wrap').getBoundingClientRect().height;
  const surrounding=(stage.top-demo.top)+(end.bottom-stage.bottom);
  const budget=innerHeight-nav-18-surrounding;
  scale=Math.min(1,Math.max(.88,budget/584));
 }
 scale=Math.round(scale*1000)/1000;
 const height=Math.ceil(nativeDemoHeight*scale),changed=demoStage.style.height!==`${height}px`||demoStage.style.getPropertyValue('--demo-scale')!==String(scale);
 demoStage.style.setProperty('--demo-scale',scale);
 demoStage.style.height=`${height}px`;
 if(changed&&++fitPass<3)fitFrame=requestAnimationFrame(fitDemo);
}
function scheduleDemoFit(){fitPass=0;if(!fitFrame)fitFrame=requestAnimationFrame(fitDemo);}
addEventListener('resize',scheduleDemoFit,{passive:true});
if('ResizeObserver'in window){const sizing=new ResizeObserver(scheduleDemoFit);sizing.observe(document.querySelector('.playground-controls'));sizing.observe(document.querySelector('#demo .section-top'));}
function render(){
 revision++;snapshot=makeSnapshot();publish();
 const journey=journeys[selected];
 $('scenario-step').textContent=journey?`SCENE / ${String(journeyStep+1).padStart(2,'0')} OF ${journey.actions.length+1}`:'DEMO CONTROLS';
 if(journey)$('event-message').textContent=journey.notes[journeyStep];
 const allDone=states.every(s=>s==='done'),waiting=states.includes('review');
 scheduleDemoFit();
 $('advance').disabled=rootAccepted;$('advance').innerHTML=rootAccepted?'整体已验收 ✓':journey?journey.actions[journeyStep]+' <span>→</span>':allDone?'完成综合验收 <span>→</span>':waiting?'验收当前结果 <span>→</span>':'推进一步 <span>→</span>';
}
// Open native disclosures only at the specific demo action; the plugin still owns rendering.
function revealNative(selectors,expectedStep){
 let doc;try{doc=demoFrame.contentDocument;}catch{return;}if(!doc)return;
 const expectedScenario=selected;let timeout;
 const observer=new MutationObserver(reveal);
 function finish(){observer.disconnect();clearTimeout(timeout);}
 function reveal(){
  if(selected!==expectedScenario||journeyStep!==expectedStep){finish();return;}
  const elements=selectors.map(selector=>doc.querySelector(selector));
  if(elements.some(element=>!element))return;
  finish();elements.forEach(element=>{if(!element.open)element.querySelector(':scope > summary')?.click();});
 }
 observer.observe(doc.body,{childList:true,subtree:true});timeout=setTimeout(finish,1500);reveal();
}
function revealSceneAction(){
 if(selected==='async'&&journeyStep===1)revealNative(['#steps > li[data-root-role="parallel"] > details','#steps > li[data-root-role="parallel"] .root-metadata'],1);
 if(selected==='async'&&journeyStep===8)revealNative(['#task-history-section','#task-history-list > li > details','#task-history-list .task-tree > li > details'],8);
 if(selected==='split'&&journeyStep===1)revealNative(['#steps > li > details','details[data-key$=":demo-split-build"]:has(details[data-key$=":demo-split-api"])'],1);
 if(selected==='append'&&journeyStep===1)revealNative(['#steps > li > details','details[data-key$=":demo-append-delivery"]:has(details[data-key$=":demo-append-accessibility"])'],1);
 if(selected==='plans'&&journeyStep===2)revealNative(['#history-section','#history-list > li > details','#history-list .task-tree > li > details'],2);
 if(selected==='archive'&&journeyStep===2)revealNative(['#task-history-section','#task-history-list > li > details','#task-history-list .task-tree > li > details'],2);
 if(selected==='archive'&&journeyStep===3)revealNative(['#task-history-section','#task-history-list > li > details','#task-history-list .task-tree > li > details','#task-history-list .root-metadata','#task-history-list .root-metadata .review-history'],3);
}
function advance(){
 if(rootAccepted)return;
 const journey=journeys[selected];
 if(journey){journeyStep++;rootAccepted=journeyStep===journey.actions.length;if(rootAccepted)stopAuto();render();revealSceneAction();announce(journey.notes[journeyStep]);return;}
 const reviewing=states.indexOf('review'),running=states.indexOf('running');
 if(reviewing>=0){states[reviewing]='done';const next=states.indexOf('pending');if(next>=0)states[next]='running';announce(`「${scenarios[selected].tasks[reviewing]}」已复核通过。${next>=0?'下一项任务开始执行。':'所有子任务已验收，仍需完成整体综合验收。'}`);}
 else if(running>=0){states[running]='review';announce(`「${scenarios[selected].tasks[running]}」执行结束，结果已提交，等待主会话验收。`);}
 else if(states.every(s=>s==='done')){rootAccepted=true;stopAuto();announce('主任务综合验收通过。计划、执行与验收形成完整记录（模拟）。');}
 render();
}
function reset(){stopAuto();journeyStep=0;states=['done','done','running','pending','pending'];rootAccepted=false;scenarioStarted=Date.now()-600000;render();announce(journeys[selected]?journeys[selected].notes[0]:'演示已重置：2 项已验收，1 项执行中，2 项待处理。');}
$('advance').addEventListener('click',advance);$('reset').addEventListener('click',reset);
$('autoplay').addEventListener('click',()=>{if(timer){stopAuto();announce('自动演示已暂停。可继续手动推进。');return;}if(rootAccepted)reset();$('autoplay').setAttribute('aria-pressed','true');$('autoplay').textContent='Ⅱ 暂停演示';timer=setInterval(advance,2000);announce('自动演示已开始，每两秒推进一个状态。');});
const tabs=[...document.querySelectorAll('[data-scenario]')];
function choose(tab){const resetHost=selected==='async'&&tab.dataset.scenario!=='async';if(resetHost)bridgeReady=false;selected=tab.dataset.scenario;tabs.forEach(t=>{const active=t===tab;t.setAttribute('aria-selected',active);t.tabIndex=active?0:-1;});$('task-panel').setAttribute('aria-labelledby',tab.id);reset();if(resetHost)mountDemo();announce(`已切换到「${scenarios[selected].title}」。${journeys[selected]?.notes[0]||'2 项已验收，1 项执行中，2 项待处理。'}`);}
tabs.forEach((tab,i)=>{tab.addEventListener('click',()=>choose(tab));tab.addEventListener('keydown',e=>{let next;if(['ArrowRight','ArrowDown'].includes(e.key))next=(i+1)%tabs.length;if(['ArrowLeft','ArrowUp'].includes(e.key))next=(i-1+tabs.length)%tabs.length;if(e.key==='Home')next=0;if(e.key==='End')next=tabs.length-1;if(next!==undefined){e.preventDefault();tabs[next].focus();choose(tabs[next]);}});});
function toast(message){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.add('visible');toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),2800);}
async function copyText(text){if(navigator.clipboard&&window.isSecureContext){try{await navigator.clipboard.writeText(text);return;}catch{}}
 const input=document.createElement('textarea');input.value=text;input.setAttribute('readonly','');input.style.cssText='position:fixed;top:0;left:-9999px';document.body.append(input);input.select();let ok=false;try{ok=document.execCommand('copy');}finally{input.remove();}if(!ok)throw new Error('clipboard unavailable');}
document.querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{const text=$(button.dataset.copy).textContent.trim();try{await copyText(text);toast('已复制，粘贴即可开始。');}catch{toast('复制未成功，请选择并复制下方文本。');}}));
const revealElements=[...document.querySelectorAll('.reveal')];
if('IntersectionObserver'in window){if(!reduced.matches)document.documentElement.classList.add('js-motion');const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('visible');observer.unobserve(entry.target);}}),{threshold:.12});revealElements.forEach(el=>observer.observe(el));}
if(matchMedia('(pointer:fine)').matches)document.querySelectorAll('.feature').forEach(el=>el.addEventListener('pointermove',e=>{const r=el.getBoundingClientRect();el.style.setProperty('--mx',`${e.clientX-r.left}px`);el.style.setProperty('--my',`${e.clientY-r.top}px`);}));
const canvas=$('particles'),ctx=canvas.getContext('2d');let frame=0,particles=[],last=0,w=0,h=900;
function resize(){w=innerWidth;const dpr=Math.min(devicePixelRatio||1,1.5);canvas.width=w*dpr;canvas.height=h*dpr;ctx?.setTransform(dpr,0,0,dpr,0,0);particles=Array.from({length:Math.min(44,Math.floor(w/28))},(_,i)=>({x:(i*191.7)%w,y:(i*137.3)%h,s:.6+(i%3)*.3,v:.05+(i%4)*.025}));}
function draw(time){if(reduced.matches||document.hidden||scrollY>1100){frame=0;return;}if(time-last>40&&ctx){last=time;ctx.clearRect(0,0,w,h);for(let i=0;i<particles.length;i++){const p=particles[i];p.y=(p.y-p.v+h)%h;ctx.fillStyle='#9cb8ef';ctx.beginPath();ctx.arc(p.x,p.y,p.s,0,Math.PI*2);ctx.fill();for(let j=i+1;j<particles.length;j++){const q=particles[j],d=Math.hypot(p.x-q.x,p.y-q.y);if(d<130){ctx.strokeStyle=`rgba(115,153,217,${(1-d/130)*.16})`;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();}}}}frame=requestAnimationFrame(draw);}
function start(){if(!frame&&!reduced.matches&&!document.hidden&&scrollY<=1100)frame=requestAnimationFrame(draw);}
resize();start();addEventListener('resize',()=>{resize();start();},{passive:true});addEventListener('scroll',start,{passive:true});
document.addEventListener('visibilitychange',()=>{document.body.classList.toggle('page-hidden',document.hidden);if(document.hidden){stopAuto();if(frame)cancelAnimationFrame(frame);frame=0;}else start();});
reduced.addEventListener('change',()=>{document.documentElement.classList.toggle('js-motion',!reduced.matches);if(reduced.matches){stopAuto();if(frame)cancelAnimationFrame(frame);frame=0;ctx?.clearRect(0,0,w,h);revealElements.forEach(el=>el.classList.add('visible'));}else start();});
render();
// file:// documents cannot inspect a separate local iframe. srcdoc inherits the
// demo page's origin, preserving native disclosure controls without changing app HTML.
function mountDemo(){
 bridgeReady=false;
 if(location.protocol==='file:'){
  if(nativeDemoSource){demoFrame.srcdoc=nativeDemoSource;return;}
  const source=document.createElement('script');source.src='assets/demo-app-source.js';
  source.onload=()=>{nativeDemoSource=window.taskProgressDemoSource;demoFrame.srcdoc=nativeDemoSource;delete window.taskProgressDemoSource;};
  source.onerror=()=>announce('原生演示资源未加载，请确认 assets 文件夹与页面一起保存。');
  document.head.append(source);
 }else demoFrame.src=demoFrame.dataset.src;
}
mountDemo();
})();
