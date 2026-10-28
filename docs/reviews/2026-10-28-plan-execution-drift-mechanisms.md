# 主会话计划与执行偏离：机制、检测与恢复方案

日期：2026-10-28，Asia/Shanghai。性质：只读研究及方案建议，**没有实施本报告建议、修改真实业务任务、安装插件、提交或推送**。本报告只拥有此文件。

研究基线为 `codex/fix-thread-state-compat` 工作区：HEAD `8a9b6dc4e2d18fa1bcc03350859ece364d4ee6a2` 加当日未提交修复。对照 `v2` 分支 `247071efdf0007508c332a5bed4a07f7bba0ee68` 仅使用 `git show/git grep`，未切分支。源码行号指研究时工作区，后续编辑可能移动。

## 1. 主要判断

当前插件能保证“调用进来以后，不能把未验收结果当作解决、不能用旧 attempt 覆盖新结果”等**账本安全性**，尚不能保证“主会话一定回来执行下一项、回收结果、逐项验收”的**推进性**。真实命令、改文件、派发和对话都在插件外发生；若主会话不调用任务工具，插件既不能核验这次工作属于哪个节点，也不能要求它立即消费待验收结果。更详细的说明、更多状态门禁，可以降低错误记账，不能单独建立执行闭环。

最需要改进的是一次可恢复的驱动循环：**对账实际执行 → 回收和验收 → 处理范围变化 → 领取下一动作 → 执行 → 结算 → 再领取**。稳定目标、需求覆盖、真实执行回执及明确等待原因是循环的数据基础。统一入口可减少模型记住十余种调用顺序和标识的负担。当前宿主已有可用生命周期 hooks，第一阶段可在插件侧扩展接入，不必先重写宿主；覆盖范围仍需实际验证。

证据能够确认流程遗漏和账实分离，不能证明模型为何在内部选择了某条路线。“上下文负担增加”“新工作比收尾更显眼”是与证据相容的机制解释，尚未由对照实验确定其贡献比例。

## 2. 使用的证据与时间边界

| 证据 | 本报告使用的内容 | 边界 |
| --- | --- | --- |
| [会话审查](2026-10-28-visual-diff-thread-state-audit.md) | 真实完成回执、失败调用、重复范围、独立验收积压 | 目标会话 `01a16cc6-d9c6-7c60-8600-19166106f7a5`；最终 revision 677，10:23:16 采集 |
| [history-index.json](2026-10-28-visual-diff-thread-state-evidence/history-index.json) | 26 个任务实例、42 个计划快照、跨 session/存储分叉 | 历史索引，不代表 26 个目标都应重新执行 |
| [goal-and-reinitialization.json](2026-10-28-visual-diff-thread-state-evidence/goal-and-reinitialization.json) | 用户持续目标及“第6/7/8轮”初始化原文 | 已筛选原文；没有重新扫描原始大型日志 |
| [dispatch-comparison.json](2026-10-28-visual-diff-thread-state-evidence/dispatch-comparison.json) | 三次记录 fork none/计划别名与真实 fork all/执行者 ID 的差异 | 历史调用；不能据此宣称当日修复后的新 spawn 仍会预绑定 |
| [live-summary.json](2026-10-28-visual-diff-thread-state-evidence/live-summary.json) | revision 666→677，待验收 7→9、运行 2→1、ready 始终 0 | 有时间边界的快照，未重新读写目标会话 |
| [历史子审计](2026-10-28-visual-diff-thread-state-evidence/history-audit.md) | 多个 waiting_synthesis 归档、语义依赖未入图、重复 SDK 范围 | 初始 revision 666；最终审查修正了其中部分过强职责判断 |
| [当日修复交付](2026-10-28-thread-state-fixes.md) | 当前已修复点、安装及测试边界；修复后业务节点仍待处理 | 文档称 revision 700 有 9 待验收、3 运行；不是本报告实时重查 |
| 工作区源码 | 当前实际可强制规则、hook 范围、next_actions 机制 | 静态审查，不重新运行代码测试 |
| `v2` 的设计和实现 | 动作认领、两阶段主会话回报、范围/需求字段 | 候选方案对照，不作为当前已安装行为 |
| [OpenAI 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks) | 当前可用事件、拦截/续跑方式及覆盖例外 | 2026-10-28 读取；文档能力不等同本机插件已接入并通过覆盖测试 |

已完成修复包括：初始化不能覆盖 running/pending-review；新 spawn 别名不能充当绑定回执；默认历史精简、SQLite 只读兼容；面板同时显示运行与待验收；全部待验收不再误标待汇总。本报告不将这些旧问题重复列为当前尚未修复的漏洞。

## 3. “未执行”需要分成六种账务事实

| 分类 | 足以确认的证据 | 不能据此判定 | 正确恢复 |
| --- | --- | --- | --- |
| 真实漏项 | 有效需求仍在范围内，责任节点无有效产物；执行记录经对账确认没有开始；无合法等待 | pending 很久或没有插件完成记录，本身不足以证明没做 | 补明确 owner/验收标准，安排当前实例内的执行节点 |
| 已执行但漏记 | 宿主完成回执/命令结果/产物明确指向原范围，插件仍 running/pending | 有同名文件、模型口头说完成，不足以验证版本和范围 | 引用原 attempt 与真实观察补记；不能伪造历史 accepted 或把主会话后续工作塞进子 agent attempt |
| 计划已变，旧项失效 | 用户范围修订或新证据明确替代旧方案，保存替代关系与未迁移缺口 | 换标题、初始化新实例、换 epoch 均不自动免除旧需求 | 记录 scope revision、superseded/withdrawn 理由和覆盖映射；保留历史 |
| 执行已结束，待验收 | submission 存在，review_status=pending | finished/100% 执行结束不能解释为业务交付 | 主会话检查范围/文件/验证后 accept 或 reject；reject 后修订或重试 |
| 外部等待或前置阻塞 | 已知 run、心跳、认证状态、待输入或未验收前驱，保存观察时间 | 主机离线不是调查节点失败；ready=0 不是死锁 | 等待明确事件，或先处理 review/认证/用户输入；到期核实而非自动失败 |
| 证据不足，待对账 | 外部调用可能成功但响应丢失、身份映射不明、历史来源冲突 | 不能当作真实漏项，也不能自动重派 | 先按 provider/run/action 查询；未知保留 unknown，确认未创建后才重试 |

其中“已执行但漏记”修的是账，“真实漏项”补的是工作，“待验收”补的是责任判断。三者不应共用一个“重跑”按钮。审计成功但结论是“不能安全迁移/认证未知”，可以是该调查节点 resolved，仍使其对应业务需求 open。

## 4. 证据—机制—可检测信号—修复方式

| 证据 | 机制解释 | 可检测信号及强度 | 修复方式 |
| --- | --- | --- | --- |
| 当前 hook 只处理 SessionStart 和 UserPromptSubmit，输出 additionalContext；源码明确 non-blocking/fail-open | 只有提醒，没有读取当前态，也没有在改文件、派发、结果返回、turn 结束时核对任务归属 | **可确定**：当前 hook 无这些事件处理；**不可确定**：某次漏项是否由提醒失效造成 | 驱动循环绑定动作；有宿主能力时接入 tool/agent/turn 生命周期；无能力时在结果边界主动对账，不能宣称全面强制 |
| next_actions 按节点遍历生成自然语言列表；运行项建议“等待”，待验收项建议 review | 主会话仍需自行辨别优先级、选择工具、拼身份字段及执行完整链；列表不是一个被领取的责任单元 | pending review 持续存在同时派发新任务；相同非法操作反复被拒绝；只有读取无有效转移 | 返回有类型动作、原因、参数上下文、前置版本和恢复方式；统一 claim/report，先收尾再扩张 |
| 09:58 两次重复 update_task(finished) 被拒，9 份提交没有 review；最早约等待 65 分钟 | 新旧完成语义不一致：执行完成被当作验收完成；拒绝保护住账，却没有把执行器带入正确的下一动作 | **确认遗漏**：submission.pending + 无 review；错误码/失败工具结果可触发明确纠正 | 错误返回结构化 `required_action=review` 与当前 scope；领取 REVIEW；不得用新节点或 retry 绕开 |
| remaining-sdk-optional-tools 启动失败后另建同范围 sdk-optional-tools-audit，原节点仍 pending | 模型把“门禁拒绝”当作另起工作入口，节点 ID 去重无法识别语义重复 | 相同 requirement/验收范围有多个活动节点；新节点紧随依赖拒绝；标题相似仅是弱信号 | 创建前做需求覆盖检查，要求 duplicate/supersedes/independent_scope 声明；前驱未验收则先验收；独立审计明确和外部消费验收的差异 |
| 第6/7/8轮分别 init，持续用户目标没有相应新边界；多份旧根 waiting_synthesis 被归档 | 用任务实例表达计划阶段，目标连续性断裂；未收尾项目沉入历史 | 同 goal 的短期连续 init；旧根未验收；原需求在当前树找不到；session 数增加 | 保存稳定 goal/requirements，继续用同实例；新增范围追加或重规划；真正新目标才 init，旧项必须逐项交代 |
| 执行者 09:47:41 final，账本继续 running；主会话后续 Docker 工作写入原 subagent attempt | 执行生命周期靠人工双写，记录没有订阅真实完成；“任务还没完全解决”被误用为“原执行者仍运行” | **需宿主观察**：executor terminal && task running；scope/产物与派发范围不一致 | completion 生成 COLLECT/RECONCILE；保留原始 final，主会话后续另建/接管明确节点；身份不可变 |
| 三次 spawn 配置与记录不同；当前修复已禁止新 spawn 别名预绑定 | 计划、请求、真实回执原先共用字段；补录机制仍由调用方申报 | tool request/receipt 与 recorded fork/ID/config 不一致；bound 无 receipt_ref | 保存 requested 与 observed 分层；原生回执 ID、工具调用 ID、时间和来源；未知模型允许 unknown，不猜 |
| 审计发现部分汇总声明使用 transport/OpenSpec 成果而 depends_on=[] | 图上的依赖并不覆盖语义上的消费；服务端只检查已声明的边 | evidence/input_refs 引用另一任务结果但无输入版本/依赖；**需核实**独立复验是否成立 | 消费结果记录 result_ref/version；若主会话独立复验，记录独立证据而非补造 review；只补真实消费依赖 |
| 当前长任务提醒仅从 started_at 计算；summary 中出现“拆分复核：已评估”等模式即永久不提醒 | 计时与真实进展分离，文本标记没有评估时点/下次检查；不能保证持续复核 | 长时间无 observation_seq 增长；split_review 标记无时间；读取很多次状态不变 | 结构化 last_progress、last_observed、reviewed_at/next_review_at；只发状态待确认，不自动结束 |
| 26 实例/42 计划；初始每次读近 1MB，精简后减少约80%；历史存储分叉 | 恢复时要从大量历史重建目标与责任；当前修复减小输入量，但语义连续性仍需索引 | 同一会话多个 session、不同存储互相引用缺失；读输出截断；未解决需求只在旧历史 | 紧凑当前摘要+按需历史，带来源的会话索引；恢复包列出当前 scope、未验收、在途、未覆盖需求，不靠全文重读 |

可解释这组症状的反馈链为：**验收未消费 → 下游合法阻塞 → 主会话继续插件外工作/创建替代节点 → 更多结果和范围进入树 → 恢复更难 → 更多漏记和未验收**。审计确认链条中的事件及重复范围；不据此声称所有新工作都是逃避门禁。

## 5. 当前 API 能覆盖什么，缺什么事件

“可校验”指成功调用到服务端时可拒绝非法变化；“可发现”指已有数据足以判断。二者都不等于可以强制宿主调用。

| 需要的事实或事件 | 当前 API / hook | 覆盖结论 | 建议补充 |
| --- | --- | --- | --- |
| 恢复/用户新指令 | SessionStart/UserPromptSubmit 提醒 | 有触发提示，无当前态读取、无范围判断事实 | 驱动先 read；记录 continuation/scope change/new goal 的依据 |
| 节点已规划且依赖满足 | init/decompose/get_ready/update_task | 可确定、可强制；祖先依赖也校验 | 保留，避免重写成熟图门禁 |
| 本次真实工具工作属于哪项 | 无跨工具归属协议 | 不可发现插件外工作，除非调用方报告 | action/task scope 附在 tool/run correlation；非实质对话豁免 |
| 派发请求已发但回执丢失 | start/bind 间人工处理 | pending binding 可见；无法分辨未发和已发未知 | dispatch_begin/receipt、request digest、external id；未知先对账 |
| 真正 agent/run ID 与配置 | bind 为调用方填写 | 可校验一次绑定，不认证真实身份 | 宿主返回观察带 source/call_id；requested/observed 分开 |
| agent 实际完成 | 无完成订阅 | 仅根据 update_task；漏记无法自行发现 | wait/read snapshot 或宿主 completion 生成 COLLECT |
| 子任务结果待主会话验收 | submission/review_task | 可确定、拒绝覆盖/绕过；不保证及时验收 | REVIEW 队列、等待年龄/关键路径优先；单次有界结果包 |
| 所有子结果可综合 | next_actions + group update | 可确定、可拒绝不满足条件的 resolved | 显式 SYNTHESIZE，带输入结果版本 |
| 新节点是否重复工作 | ID 唯一、无环 | 只检身份与结构，不检语义范围 | requirement/acceptance coverage、supersedes 与重复确认理由 |
| 范围新增/撤回/方案替代 | decompose/rebuild/new_task_reason | 有原因文本，无需求差异和覆盖守恒 | scope_revision、requirement disposition、映射及批准来源 |
| 真正外部等待/暂停/状态未知 | summary 文本，running/open 等 | 等待原因不结构化；不能从 running 判断持续工作 | activity/wait_reason、provider/run、last_observed、resume condition |
| 修改上游使结果过期 | retry 失效消费者及祖先 | 仅在已声明图内可校验；隐式消费不覆盖 | result_revision 与 input_refs，并保留现行失效门禁 |
| 命令结果/证据是否支持业务结论 | evidence 字符串、review 主会话声明 | 可要求非空，不能证明测试真实覆盖范围 | 源版本、输入、命令/run、验收项引用；实质判断仍由验收者 |
| turn 结束/compaction 时是否留尾项 | resume 提醒，无 turn-end 对账 | 不能拦住“说完就走”或丢失未结算动作 | 恢复检查点+turn-end 状态摘要；无法接事件时驱动手动调用 |
| 宿主终止后继续推进 | MCP 被动服务 | 不具备自主唤醒能力 | 若另获授权接调度/heartbeat；不以 API 命名冒充后台能力 |

### 已有宿主 hooks 与插件当前未接入的区别

官方文档已提供 PreToolUse/PostToolUse，覆盖 shell、apply_patch、MCP、本地函数和 code mode 内对应调用；Pre 可 deny。SubagentStop/Stop 可以请求继续，Stop 应检查 stop_hook_active 防循环。Hosted 与部分专门工具路径例外；write_stdin 不再次触发 Pre。Post 无法撤销已发生的效果。[OpenAI 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)

所以，当前只配置两个提醒是插件的覆盖缺口，不能解释为 Codex 必须增加新宿主 API 才能改进。建议在插件侧接入后，用 Pre 检查本次实质工作是否持有当前任务动作；Post 按工具调用 ID 记录观察，SubagentStop 触发提交/回收，Stop 处理明确且可行动的漏记和未验收项。该方案是本报告的工程建议，尚未实施。

必须给任务工具本身、恢复读取、用户暂停/取消以及确属简单问答保留通路，避免“没有 claim 不能调用 claim”的自锁。只禁止可确定的身份/依赖/重复效果违规，不用正则猜测任意 shell 的全部业务含义。恢复动作应由已持久化事件驱动；不要为了 hook 对账再扫整份聊天或打印凭据。

Stop 不能因存在任何 open 项就无限续跑：用户要求停止、合法外部等待、需用户输入或本次没有可推进动作时应结束并保存恢复点；仅对已确认的可行动尾项触发有界继续，检查重入标记和无进展次数。SubagentStop 只对获派节点做结算提醒，不能让子 agent 验收父节点。这些是插件策略，不是官方文档自动提供的业务判断。

该闭环可对受支持工具实现比“提醒”更强的前置门禁；完整强保证仍需覆盖矩阵、事件去重、身份绑定、故障策略及实际宿主测试。不能根据文档列出 hooks 就宣称所有插件外工作已被拦截。

## 6. 建议状态模型和转移

避免增加一个混合所有含义的枚举。保留执行、解决程度、验收，补充活动、有效性和范围处置：

| 维度 | 建议语义 |
| --- | --- |
| execution | pending / prepared / running / finished / failed / cancelled，反映真实执行阶段 |
| review | none / pending / accepted / rejected；主会话直接执行可按既有协议保留独立说明，不伪装子 agent review |
| outcome | resolved / open，仅正常结果的解决程度；failed/cancelled 不意味着需求被满足 |
| activity | active / waiting_worker / waiting_external / waiting_user / waiting_review / paused / state_unknown；带原因和恢复条件 |
| validity | current / stale，结果被哪些输入版本支持 |
| scope disposition | active / superseded / withdrawn，记录替代者、范围修订和依据；不等同于执行 cancelled |

该模型是概念建议，不要求立刻改变现有 schema 或把历史全部转换。对旧数据无法核实的维度保持 unknown/legacy。

| 触发 | 前置/守卫 | 结果 | 恢复注意 |
| --- | --- | --- | --- |
| claim EXECUTE/DISPATCH | 当前 scope/输入有效，依赖满足，没有相同有效 claim | prepared、唯一 action/attempt、预留必要资源 | claim 本身不说明真正开始 |
| begin effect | 当前 fence、动作类型匹配、记录将调用工具的请求指纹 | effect_started / dispatching | 此后中断先对账，不能租约到期就重复外部操作 |
| 真实 started/receipt | 正确 action/attempt、真实来源与 executor/run | running、绑定回执；或 queued/waiting_external | 请求配置与观察配置分别保留 |
| final report | 当前 identity，原始报告不可覆盖 | finished + submission、review pending | agent final 不自动 accepted |
| review accept/reject | 主会话核实当前 submission 与范围、证据 | accepted 采用原 outcome；rejected 为 open | 不将审计成功升级为被审功能成功 |
| synthesize | 所有要求的子结果有效且已验收；缺口明确处理 | 父级 current result；resolved/open | 100% ended 不替代此步 |
| 外部状态 unknown | 丢失响应/观察过期，尚无停止证据 | state_unknown / RECONCILE | 不释放可能仍使用的资源，不盲目重发 |
| 等待输入或外部事件 | 保存 reason/owner/关联 ID/恢复条件 | waiting_user/external | 等待不是 failed，也不是虚构一直 active |
| retry/reopen | 无受影响在途未知执行；计算消费者影响 | 新 attempt，旧结果 stale，祖先验收失效 | 已有现行实现应复用；语义消费者需显式输入引用 |
| scope change | 当前新指令/证据依据，逐项需求处置 | 同实例 scope_revision/新 epoch 或小范围追加 | 不静默遗失原需求；旧在途先结算/明确移交 |
| 真正新目标 | 有目标边界依据且旧在途/待验收已处理 | 新 task_instance，旧结果按来源归档 | 新目标不等于当前阶段改名 |

## 7. 分散工具与“领取下一动作”的比较

当前常见链条为 `get_progress → get_ready_tasks → update_task(running) → spawn → bind → wait → update_task(finished) → review_task → get_ready_tasks → group update`。这些独立操作各有合理职责，但模型必须保持 scope、ID、依赖、执行者、提交/验收语义及异常恢复一致；中途压缩、用户插入、失败回执容易令链条断开。

建议保留低层 API 作为领域操作和兼容层，在其上提供统一入口，而非用一个万能 update 绕开门禁：

```text
advance(preview) -> typed candidates + blocked reasons
advance(claim, request_id, expected_revision, capabilities)
  -> action_id + fence + target scope + exact input refs + required response schema
report(action_id, fence, phase, observation/result)
  -> durable receipt + next bounded summary
```

优先顺序建议为：**STOP/RECONCILE → COLLECT/REVIEW → 已可 SYNTHESIZE → 必需 scope change/plan → 新执行/派发 → WAIT/COMPLETE**。不要求整树清空 review 后才能派发所有独立工作；应优先处理关键路径 review，并采用有界公平策略，防止持续收尾任务饿死长工作。claim 不自动 accept；主会话仍检查内容。

| 负担 | 当前分散 API | 统一动作可减少 | 仍不能消除 |
| --- | --- | --- | --- |
| 选下一工具 | 模型从自然语言列表和状态推断 | 明确 REVIEW/SYNTHESIZE 等类型和输入 | 模型仍可能忽略入口 |
| 传递身份/版本 | 多次手工拼 task/instance/attempt | 单一 scope/fence/result refs 随动作返回 | 外部工具不接受关联字段时仍需适配 |
| 中断后恢复 | start/spawn/bind 间自行核对 | 持久化 intent、receipt、unknown 和对账动作 | 无 provider 查询能力时仍不可确认是否已执行 |
| 并发重复启动 | 当前启动锁和 attempt 已能阻止部分重复；读取建议无领取所有权 | 动作唯一键、claim/fence 明确执行权 | 不能阻止人或插件外工具绕过 |
| 验收积压 | 自行从众多节点选择 | 可按年龄/关键路径领取，避免误用 finished | 证据理解和接受质量仍需模型/人判断 |
| 重复节点 | 只保证 ID 不重复 | scope 覆盖检查与创建前冲突提示 | 不同措辞相同目标的语义判断仍非完备 |
| 必须执行什么 | 节点标题/依赖不足以表达所有验收范围 | requirement_ids + acceptance + scope diff | 覆盖映射字段齐全不证明方案真的覆盖需求 |
| 任务数量与节奏 | 每次命令/调查可能膨胀成节点 | 一个交付节点内多次 assignment/report，不必增 HTN 节点 | 粗粒度边界需规划判断，不能靠固定数量最优化 |

### v2 可以借鉴，但不应直接宣称解决

`v2` 设计稿明确区分 prepared/真实开始、assignment、report、result_revision、activity、scope 和恢复；`task_advance`/`task_report` 是相符方向。其实现中确有原子 claim、request receipt、60 秒 lease、ORCHESTRATE claim 建 prepared attempt，以及 main_session_transition 两阶段开始/完成（`task_progress_v3.py:350–409,587–619`）。因此不是纯概念草案。

同时，静态核对发现以下采纳前必须评估的差距；本报告没有运行 v2，不能把它们写成已通过/已失败的动态用例：

1. `_candidates` 按 nodes 遍历依次加入 PLAN/ORCHESTRATE/COLLECT/SYNTHESIZE/DISPATCH；未见设计稿所述统一 STOP/RECONCILE 优先序或按验收年龄排序。因此“换成 advance”本身不能保证不再优先开新活。
2. 主会话 begin 要求非空 model 和合法 reasoning_effort；当前分支特意允许宿主未知时省略。直接搬用会引入新的记账阻塞，甚至诱导猜字段，应保留 unknown + 来源边界。
3. 60 秒 lease 需要长任务续租；开始外部效果后过期、主会话无并行续租机会、完成回报被拒的情况必须有对账恢复。租约不能被误用为真实进程生死或重发许可。
4. 设计稿的能力/容量/资源预算、未知派发恢复、范围守恒等，应逐项核对实现和测试；不能因存在 claim 表就推断全部已实现。派发仍由宿主执行，不能自行唤醒。

当前最稳妥的研究结论是“采用动作驱动作为收敛方向，保留现行已验证职责门禁与兼容边界”，不是立即切换 v2/SQLite 或再做一轮大重写。

## 8. 粒度、任务数量和范围变化

任务树表达有独立产出和验收边界的工作；命令、读取、重试探测表达在一个节点内的 execution/report。固定节点总量不能代表健康：拆分一个实际独立交付可以合理增加分母，几十个微步骤也可能掩盖只有一个未解决目标。

建议每次新增/拆分提交一份小范围差异：新增的需求/验收边界是什么、为何不能在现有节点内完成、替代哪个旧节点、保留哪些原缺口、是否正在消费其他结果。对每个需求记录 `covered_by` 和 `accepted_result`，允许多节点分别承担实现和验收，但不能都宣称独立实现同一范围而没有理由。父级必须仍覆盖原目标。

可采用以下检测指标，阈值是待试验参数而非当前错误判据：

- `uncovered_requirement_count`：有效需求没有当前责任节点或已接受结果；最直接的漏项候选。
- `duplicate_active_coverage`：同需求/验收项被多个活动实现节点覆盖；要求说明独立性或替代关系，不能仅按标题自动合并。
- `scope_churn`：同目标短期 init、epoch/节点创建速度和被替代比例；用于提示复盘，不按次数自动判失败。
- `review_age`、`review_blocks_critical_path`、`synthesis_age`：在途少却大量旧 review 时，先处理收尾。
- `ledger_lag`：宿主 final/产物提交与插件 submission 的时差；必须有实际观察来源。
- `off_plan_effect_count`：实质工具效果找不到 action/task；只有接入工具事件后才能计算，未知不算零。
- `remaining_scope_weight`：按验收项/交付边界观察，而非“已完成节点数/总节点数”；重规划时保留前后覆盖变化解释。

同目标新增范围：仍用当前实例，更新需求版本并追加有边界节点。方案失效：先处理在途与 pending review，再重规划，旧证据仅作为有来源的输入。用户撤回需求：保存明确撤回依据，不以 cancelled 伪装已满足。用户改为一个新目标：新实例及旧项处置说明。是否新目标应由用户语义和验收边界判断，不能让插件单靠标题相似度替用户决定。

## 9. 检测规则、反例与恢复动作

| 规则 | 反例/误报条件 | 恢复动作 |
| --- | --- | --- |
| 有 pending review 仍新派发，提示验收饥饿 | 新工作完全独立、主会话已经在验证证据；不能全局禁止并行 | 只优先关键路径/超龄 review；记录稍后复核边界 |
| executor 已 terminal 而节点 running，提示账实不符 | 主会话已明确接管的新工作仍在继续；不能把原 executor 的结束当整个目标结束 | 结算原 assignment，另记主会话 scope/attempt；不改原回执 |
| 相同需求有两个节点，提示重复覆盖 | 独立验证或不同平台/输入矩阵是有意交叉 | 标注实现/验收角色及平台输入；真正冗余节点 superseded，保留未覆盖消费测试 |
| ready=0 且目标未完，提示查看阻塞 | 正在运行、有 pending review、正常外部等待均合法 | 返回精确 next action；只在等待环结构证据下称死锁 |
| 长时间无状态变化，提示 stale | 合法长测试、宿主关闭、排队没有新事件 | 记录 run/queue/last_observed 和下次检查；不要自动 failed/retry |
| 结果 resolved 但业务仍不可用，提示范围混淆 | “核实为何不可用”的调查本就可 resolved | 验收调查产物；业务需求继续 open，显式 downstream readiness 条件 |
| command exit 0/文件存在，提示可以完成 | 输入/源码已变或验收范围不匹配 | 校验源版本、输入和 acceptance evidence；不能以存在性直接 accepted |
| init 被门禁拒绝，提示先收尾 | 用户确实新目标，但旧 agent 仍运行/待复核 | 保存新目标请求，协调旧执行者与 review；不另省略 session 创建旁路 |
| claim lease 过期，提示恢复 | 外部效果已发生，正常工作仍在运行 | RECONCILE 并继承可证明的原执行；确认未创建才重派，禁止租约驱动重复操作 |
| 所有叶都结束，提示可汇总 | pending review/open/failed 或依赖结果 stale | 分别 REVIEW/恢复/范围修订；必要时根 open，不能用 100% 假完成 |

## 10. 建议验证路线与本次交付边界

后续若用户决定实施，先用已保存的干净事件片段和隔离任务回放以下场景：重复 finished、final 漏记、spawn 响应丢失、同目标重复 init、独立审计与消费测试重叠、认证未知、scope 撤回、长任务 lease 过期、上游重试使消费证据过期、所有结果待验收。每场景断言应生成的下一动作、禁止的动作、最终需求覆盖和原始证据保留；不只断言页面计数。

可分层推进：第一阶段在插件侧同时补诊断摘要/恢复包、兼容现行状态机的 typed preview/claim/report，并接入上述已有生命周期 hooks，形成最小可验证闭环；后续再扩需求版本、资源和预算。对未覆盖事件明确显示 unknown。对动作循环做“建议被忽略、driver 停止、模型执行插件外命令”的负向测试，准确说明无法强制的范围，不必先重写宿主。

衡量方案是否有效应看：漏记时差、关键路径 review 等待、无解释重复覆盖、未覆盖需求和人工恢复次数是否下降；不能以节点变少、全部绿灯或 API 调用数增加作为成功。

本次仅写研究文档。未重新验收原会话的 Docker/PPTX/模型/SDK 业务成果；未接受其 9 项提交；未改其任务或历史；未运行插件测试，因为没有实现变更，所需证据已由当日审查与修复报告提供。最终业务状态应由原会话负责人依据其当前输入和真实执行证据确认。
