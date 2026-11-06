# 智能体协作优化：S0 试点基线与记录模板

日期：2026-11-06。定义版本：`pilot-v1`。

状态：本文件固定试点方法、口径和模板；负责人已确认以 FIX-1、INV-2、MOD-1 各跑 A/B/C 一轮，共 9 轮校准。三项请求、限定证据/状态 fixture 和 evaluator-only oracle 已部分备齐并逐字节记 hash；A/B/C 逐轮运行包、实际宿主配置、reset 快照尚未冻结，数值墙钟预算也未确认，**尚不满足实测开跑条件**。正式 36 轮仍未授权。实测均待采集，不能据此宣称收益或最终采用。缺失测量统一写 `unknown`，不能填零或从其他会话推断。

依据：[优化方案 S0、验收矩阵与试点标准](../design/2026-11-06-agent-collaboration-optimization-plan.md)。本文件只负责 S0；不新增监控服务，不运行产品测试，不改变派发、验收或依赖规则。

## 1. 基准版本与现有证据

基准仓库：`https://github.com/carpe-horam/task-progress-codex.git`。

基准 ref：`ef7f37f1a3502f17c1c69aec95b801250a82b23d`，本次只读核对的 HEAD 与方案基点一致。基准插件清单版本为 `0.2.19`；版本号不证明当前宿主已加载相同字节。采集时仍须记录实际服务与技能版本。

以下 SHA-256 针对 `git show <基准 ref>:<路径>` 的字节，不随共享工作区后续实现变动：

| 输入 | SHA-256 |
| --- | --- |
| [server.py](../../plugins/task-progress/server.py) | `d8787370aa1409467cd5fa79637b894a89e4f714742bd4e8d99a19115b2934d1` |
| [app.html](../../plugins/task-progress/app.html) | `c211d4f1721d008263b9c9635d353b95248f0a40e18ebfe37b048500297d0c48` |
| [插件清单](../../plugins/task-progress/.codex-plugin/plugin.json) | `907ec01baa3f19760262b53833c4ac2c0de9c9e1e23bf240f11a4b4b5bd220ec` |
| [track-progress skill](../../plugins/task-progress/skills/track-progress/SKILL.md) | `ef1ed7f6ec9e11301b26496d076f3f301e4d4cea21279ab18c6e708670ff99f3` |
| [delegation.md](../../plugins/task-progress/skills/track-progress/references/delegation.md) | `0564bbaee387e1ba191cd3d10cafecbdbf2eeea540d60d6d10a253071f3c5cf0` |
| [htn-dispatch.md](../../plugins/task-progress/skills/track-progress/references/htn-dispatch.md) | `cd8d2a77aea21584d3ebd966def9d548060b7065671861ef8682186ed4383a7d` |

设计方案在首次读取时为已有未跟踪文件，其 SHA-256 为 `2477aff06e09e6a988b37c433dd1d2e202c641239d73333cdee3ab4fb214b3ea`；正式执行前固定包含本定义和方案的文档 commit 或归档 manifest。不得把未提交文档描述为上述基准 ref 已包含的文件。

### 当前行为的源码级基线

基准 `server.py` 的 `_normalize_assignment` 只允许明确的 `fork_turns="none"`，有派发字段时自动补 none；子智能体启动要求模型、推理强度，正式派发还要求工具和完整文本。`_metadata_provenance` 将模型等标为调用方输入、未经宿主验证。基准派发技能要求 `start → spawn → bind`，支持以已知真实 agent ID 的 follow-up 复用执行者。它们是当前实现/指导的事实，不能冒充实际宿主执行结果。

静态解析基准 `TOOLS` 初始列表、直接 append 与两处声明循环，共 **35 个 Task Progress MCP 工具定义**；不是每轮工具调用数，也不是宿主暴露的全部工具数。可复核入口为上述固定 ref 的 `server.py` 中 `TOOLS` 及其 append。实际宿主 `tools/list` 数量和响应快照：`unknown`。

### 已有源码测试基线（独立于工作任务试点）

主会话提供的执行结果：

```text
ref: ef7f37f1a3502f17c1c69aec95b801250a82b23d
route: local
command: pytest -q tests/test_responsibility_contract.py tests/test_delegation_policy.py tests/test_execution_lifecycle.py tests/test_hook_runtime.py
result: 50 passed, 27 subtests passed
elapsed_s: 3.65
source: main-session supplied execution result, 2026-11-06
raw_log_uri: unknown
```

这条记录是主会话提供、本文作者未重跑的测试基线；原始日志位置和精确开始时间未提供。主会话同时报告执行前源码干净，仅两份既有设计文档未跟踪。这些测试说明相关回归入口可用；不能替代真实宿主适配、质量 oracle、成本或 36 轮工作任务测量。[测试说明](../../tests/README.md)明确区分模拟宿主与真实宿主。

## 2. 三组定义与可比条件

| 组 | 固定定义 | 版本 |
| --- | --- | --- |
| A：单执行者 | 一个执行者完成工作；同一外部 oracle 在终稿上核验；不调用 Task Progress 协调工作链、不派发 worker | 最新安装 v0.2.21 的 Codex 插件目录仍暴露 Task Progress；单执行者提示可以准备，但工具隔离未验证 |
| B：当前用法 | 基准插件与基准技能，按实际支持能力派发和手工绑定，不预先采用 S1–S3 行为 | 基准服务代码为 v0.2.19（本地缓存 `server.py` SHA 已记于 [selected-candidates-v1](inputs/selected-candidates-v1.md)）；v0.2.19 在当前 Codex 中的独立安装/加载未验证 |
| C：优化用法 | 同一工作任务、oracle 和预算，启用已验收的 S1–S2；S3 自动绑定仅限真宿主已验证的适配器 | 当前 marketplace 固定到 v0.2.21，插件已安装/启用，共享服务已 loaded/healthy；本会话实际读取 overview 并收到 `assignment_sources_v1`、`fork_context_v1`、`acceptance_contract_v1`，证明当前 MCP 服务可用。会话内技能目录是否刷新仍未知。历史 selected-candidates v1 冻结的插件/技能 SHA 针对 v0.2.20，不能直接作为 v0.2.21 轮次配置。 |

工作任务源码起点与插件源码版本分开记录。三组使用相同任务输入和 oracle；不把 C 已修复后的工作任务源码用作 B 的输入。组间重置独立输入状态和 agent context；组内允许按对应策略复用原 owner。A 的试验分组是测量方案，不授权违反实际任务或宿主规定；执行前确认宿主能合法提供三种配置，不能做到的组标 `unsupported` 并解释，不冒充已完成对照。

同任务不同组尽量固定模型请求配置、宿主版本、工具权限、最大并发、测试路由、资源限制和时间/token 预算。模型实际值未知时照实记录来源；若无法验证配置匹配，保留该轮并标记混杂，禁止给出受控因果结论。任何组不支持的配置都记录失败/未运行，不能为过关猜值。

## 3. 六个任务选择与固定输入

先选任务，后执行。任务应来自可复现的真实缺陷、调查或变更需求，有有限范围、可冻结输入、明确产物和可独立判定的质量标准。不得按运行后速度挑选任务；不得用纯 sleep、模拟编码或仅增加记账操作代表真实工作。任务难度需能在统一预算内交付，选择理由和排除理由一并保存。

| task_id | 类别 | 必须具备的代表性 | 具体需求/输入与 oracle |
| --- | --- | --- | --- |
| FIX-1 | 紧密耦合缺陷修复 | 调查、实现、局部核验共享上下文；需一次预设退回或相邻边界检查 | `unknown` |
| FIX-2 | 紧密耦合缺陷修复 | 与 FIX-1 不同风险路径；能检查重试与最终产物绑定 | `unknown` |
| INV-1 | 可并行调查 | 至少两条可独立调查证据链；有可判真伪的最终结论 | `unknown` |
| INV-2 | 可并行调查 | 不同输入来源；包含预设矛盾或中途补充信息 | `unknown` |
| MOD-1 | 多模块改动 | 至少两个模块及明确接口；能检查文件 owner 与集成结果 | `unknown` |
| MOD-2 | 多模块改动 | 不同模块组合；包含旧输入、边界与相邻正常场景 | `unknown` |

“预设”指执行前由评估者固定，在三组相同事件序号/触发条件注入；具体缺陷答案不泄露给执行者。预设缺陷、输入中断和反馈必须有固定字节、触发条件及预期判定；不得看到结果后新增失败标准。需要真正独立验证的任务，验证者与实现者的实际 agent ID 不同；同模型不证明错误独立。

每个槽位复制以下 manifest。六份完整且可校验后才称“任务输入已冻结”；当前只有槽位，尚未选定六个真实任务。

```yaml
task_id: unknown
category: unknown # coupled_fix | parallel_investigation | multi_module
selection_reason: unknown
request_uri: unknown
request_sha256: unknown
task_repo: unknown
task_base_ref: unknown
input_manifest_uri: unknown
input_manifest_sha256: unknown
inputs: # 必须列全；无附件也显式记录经核验的空列表
  - {uri: unknown, sha256: unknown, purpose: unknown}
environment_manifest_uri: unknown # OS/架构/工具版本/权限/依赖与可用服务
environment_manifest_sha256: unknown
initial_state_uri: unknown # 源码、插件状态、临时 HOME/缓存策略、未运行后台句柄
initial_state_sha256: unknown
scope_and_file_owners: unknown
deliverable_definition: unknown
oracle_manifest_uri: unknown
oracle_manifest_sha256: unknown
seeded_defect_manifest_uri: unknown # 评估者持有，不交付给执行者
seeded_defect_manifest_sha256: unknown
feedback_event_manifest_uri: unknown
feedback_event_manifest_sha256: unknown
reset_procedure: unknown
reset_verification: unknown
frozen_by: unknown
frozen_at: unknown
```

复用已有 worktree，禁止嵌套创建。轮次可在同一工作区顺序重置至已冻结快照；重置前保存本轮产物，核对所有权与未提交内容，不执行可能删除其他会话工作的清理。共享缓存、文件、服务和外部资源串行使用，或提供真正独立状态。每轮记录实际隔离证明；复用 agent、结果、补丁或上一轮答案跨组污染时该轮保留并标记 `contaminated`，另开新轮，不覆盖原记录。

## 4. 可重复的质量 oracle

oracle 在执行前固定，独立于“任务标为 accepted/resolved”和执行者自检。评估者针对最终产物 SHA/manifest 执行；若产物改变，旧 oracle 结果不得接受新文件。核验至少覆盖下表，按任务风险选择最小必要命令，遵循当时 `dagu-test-runner` 路由，不因试点扩大全量测试。

| criterion_id | 固定检查方法 | 通过规则 |
| --- | --- | --- |
| functional | 任务特定回归命令，或调查结论的确定性证据核对 | 原问题/正确结论成立，输出符合冻结期望 |
| compatibility | 冻结的旧输入、边界输入、相邻正常场景 | 所有重要样本通过，未破坏约定 |
| scope | 与任务起点比较最终 diff/产物清单 | 产物完整，无越界修改，接口与 owner 约定成立 |
| dependency | 检查真实账本/宿主事件轨迹 | 无 pending-review 提前放行、过期结果接受或误绑 |
| seeded_defect | 独立验证者读取固定反例，评估者核对发现报告 | 预设重要缺陷全部检出，且明确指出证据 |
| final_version | 重新读取最终源码/产物版本并比对核验记录 | 被核验版本与交付版本完全一致 |

每项任务填 `{criterion_id, required, method, command_or_procedure, expected_result, severity, evidence_uri}`，未使用项写 `not_applicable` 并给出冻结理由。命令成功本身不等于功能正确；调查类使用固定证据和答案 rubric，可不运行产品测试。oracle 的实际 verdict 为 `pass|fail|not_run|unknown`，不适用另列，不能把缺日志写 pass。

遗漏：冻结的重要需求或缺陷未被结果覆盖。错误接受：协作过程已接受/最终交付，但独立 oracle 存在 required fail，或接受过期产物/误绑/提前依赖放行。记录具体事件与影响，不能只记一个总分。最终质量报告 required 项通过数/总数、遗漏 ID、错误接受 ID、预设缺陷检出数/总数；任何重要项 fail/not_run/unknown 均不能判断正确性采用门通过。

## 5. 轮次、计数与时间口径

正式矩阵为 6 任务 × 3 组 × 2 独立重复，共 36 轮。每类可先选 1 个任务进行校准；校准单独标 `calibration`，不计入正式 36 轮。校准后方法变更须版本化、重新冻结，不能把旧规则下的数据混入新矩阵。

固定交错顺序如下；依次执行每格三轮，先完成全部任务 repeat 1，再 repeat 2。顺序是设计选择，不声称随机化或统计均衡。资源冲突导致改序时记录原因。

| task_id | repeat 1 | repeat 2 |
| --- | --- | --- |
| FIX-1 | A → B → C | C → B → A |
| FIX-2 | B → C → A | A → C → B |
| INV-1 | C → A → B | B → A → C |
| INV-2 | A → C → B | B → C → A |
| MOD-1 | B → A → C | C → A → B |
| MOD-2 | C → B → A | A → B → C |

run ID 为 `<task_id>-<A|B|C>-r<1|2>`；失败重跑追加 `-retry<n>`，保留原失败行，正式统计报告全部尝试及预算失败率，不能只挑成功最快结果。

| 指标 | 固定口径 |
| --- | --- |
| 协调调用 | 记录所有 Task Progress MCP 调用与 collaboration 调用，分开计数并给出工具名分布；一次实际调用算一次，失败/重试/空查询也计入。批量调用按实际底层工具次数计数，轮询计入。每个调用保留原始 ID，重放展示不重复计数 |
| 其他调用 | shell、浏览器等实际工作工具另计；用户自然语言更新不计工具调用。不得把工具定义数 35 当作调用数 |
| 绑定 | 人工 `bind_task_agent`、自动观察绑定分别计数；不适配自动绑定的样本不并入“人工 bind 降为零”分母 |
| owner 切换 | 同一结果责任从一个真实执行者转给另一个计一次；原 agent 的 follow-up/新 attempt 不算。独立验证者加入不是实现 owner 切换。切换注明必要/非必要及预设判定依据 |
| 深度介入 | 主会话进入 worker 的调查/实现/局部核验而非仅派发、验收的独立事件；连续同一目的动作合并，保留起止与动作证据 |
| 重复转发 | 主会话转发已存在且仍适用于同范围 owner 的反馈，按实际消息计；记录原事件 ID 与接收者，首次必需交接单列 |
| 返工 | 首次可审查结果后因质量缺陷或退回而产生的修订周期；补预期字段也算，需求新增另列，不追溯改变 oracle |
| 人工决定/无效提醒 | 人类作出的独立选择次数；无新事实且无需动作的提醒次数，由固定 rubric 逐项复核 |
| 首次可审查耗时 | 完整输入交付执行者的 t0 至首次有可读产物版本、检查说明和证据入口的 t1；完整性不意味着正确 |
| 最终交付耗时 | t0 至最终产物通过 oracle 且主会话完成综合验收的 t2；包括等待、复核、独立验证和返工。未完成则 elapsed 仅为截尾耗时，不当作完成时间 |

记录 UTC ISO 8601 墙钟时间与同一宿主单调时钟 elapsed；跨宿主不能直接相减未校时的时钟。外部资源等待、用户等待、执行、复核分别记录区间；总耗时为 t2−t0，不直接相加可能重叠的 agent 时长。首轮准备/reset 单列开销，组特有的协调初始化须纳入 t0 后时间。

## 6. 配置来源、预算与成本

每轮固定并记录主会话、每个 worker、验证者的 requested model/effort、实际宿主返回值、来源 URI、fork 参数与上下文策略（fresh/inherit/reuse）、真实 agent ID、插件源码/服务 ref、技能 hash、能力标志、并发上限和机器环境。请求值与实际值分列。声明来源仅为 `caller_declared`，宿主结构化回执记 `host_observed`，都不自动升级为认证。缺失的模型或 effort 为 `unknown`，不读取默认配置猜实际值。

执行预算目前全部 `unknown`，因此本文不启动试点。开跑前由任务负责人冻结：

| 预算项 | 单轮上限 | 校准上限 | 正式 36 轮总上限 | 依据/来源 |
| --- | --- | --- | --- | --- |
| 墙钟分钟 | `unknown` | `unknown` | `unknown` | 校准历史、任务复杂度与资源限制 |
| token（input/output/cache-read 分项） | `unknown` | `unknown` | `unknown` | 宿主计量字段与固定请求配置 |
| 实际费用/币种 | `unknown` | `unknown` | `unknown` | 账单或宿主 cost 回执；费率来源与日期 |
| 最大并发与外部运行容量 | `unknown` | `unknown` | `unknown` | 当时可用容量与共享资源约束 |

先用校准估算三组每类成本，再以各轮上限求和登记总体上限及预留额；估算必须标 `estimate`，不能记为 actual。到达上限时保存当前产物与轨迹、记录 `budget_exhausted`，不自动追加预算、不把未完成记成功。费用不可见时仍可按已固定的墙钟/token 上限执行，但实际费用指标始终 `unknown`，不能通过费用效率门。

token 记录每 actor 的 input、output、cache-read、cache-write（若提供）、单位和来源，不同宿主 input 可能已含 cache，先固定字段语义，避免二次相加。真实总费用优先原始账单；使用费率计算仅为估算并与 actual 分列。缺少角色或轮次粒度的账单不能摊分伪装实际值。报告已知覆盖率和不可比较轮次，不用 unknown 参与中位数或填零。

## 7. 每轮空白记录模板

下列行是待采集模板，不是实测记录。每轮复制一次，附原始轨迹/产物 manifest；大文件只记不可变引用和 hash。

| run_id | task/group/repeat | status | input/oracle hash | task ref / plugin ref / skill hash | t0 / t1 / t2 | first / final 秒 | 协调/其他调用 | owner 切换/深度介入/转发 | 返工/人工决定/无效提醒 | required pass/total；遗漏；错误接受 | 预设检出/total；最终版本匹配 | input/output/cache-read token | actual 费用/币种/来源 | 缺失原因/混杂/证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `unknown` | `unknown` | `not_run` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | `unknown` | 待采集 |

每个 run 附以下明细表，未采集的值保持 `unknown`：

| 明细 | 必填列 |
| --- | --- |
| 配置 | actor ID、角色、请求模型/effort、请求来源、实际模型/effort、实际来源、fork、context 策略、宿主/OS/架构、预算 ID、能力/开关 |
| 调用 | event/call ID、时间、actor、工具、目标 task/attempt、成功/失败、人工/自动、原始轨迹 URI/hash |
| 交接与返工 | event ID、原/新 owner、目标结果、原因、必要性判定、关联输入事件、前/后 artifact hash、返工起止 |
| oracle | criterion ID、required、verdict、checker ID、target task/attempt、产物 URI/version、实际输出 URI/hash、遗漏/错误接受 ID |
| 成本 | actor、provider、计量区间、input/output/cache-read/cache-write、字段包含关系、actual/estimate、币种、来源 URI/hash、缺失原因 |
| 时间与中断 | t0/t1/t2、单调时钟来源、等待/复核/执行区间、注入事件 ID、预算退出、污染或宿主失败原因 |

正式矩阵当前状态如下；每个单元格表示 r1、r2 两轮，`unknown` 为尚未采集的数值，不表示执行失败：

| task_id | A r1/r2 | B r1/r2 | C r1/r2 |
| --- | --- | --- | --- |
| FIX-1 | 待采集 / 待采集 | 待采集 / 待采集 | 待采集 / 待采集 |
| FIX-2 | 待采集 / 待采集 | 待采集 / 待采集 | 待采集 / 待采集 |
| INV-1 | 待采集 / 待采集 | 待采集 / 待采集 | 待采集 / 待采集 |
| INV-2 | 待采集 / 待采集 | 待采集 / 待采集 | 待采集 / 待采集 |
| MOD-1 | 待采集 / 待采集 | 待采集 / 待采集 | 待采集 / 待采集 |
| MOD-2 | 待采集 / 待采集 | 待采集 / 待采集 | 待采集 / 待采集 |

## 8. 分析与采用门

先报告每轮原始值、失败/缺失与覆盖率，再按三类任务分别报告组内中位数、范围和任务内配对差值；不把跨类别聚合当作唯一结果。每个统计量明确有效 n、缺失 n 和分母；基线为零时百分比变化为 `unknown`，报告绝对差。B→C 是优化效果的主要对照，A 是单执行者参照。36 轮是探索性试点，不做显著性或普遍最优声明。

正确性门优先：所有重要 oracle 项通过，零错误提前放行/过期结果接受/误绑，预设重要缺陷无漏检。失败先修复；不能以更快抵消失败。

仅对预先标记可自动绑定的样本检查人工 bind 为零；复用场景不应有无必要 owner 切换。按类别比较 B→C 的协调调用中位数，目标减少至少 20%；完成时间与已知实际费用中位数不得退化超过 10%。费用未知或样本不足时，费用结论为 `unknown`，不宣称全部效率目标通过；适配器保持可选。效率阈值是本项目采用目标，非已证效果。

S5 事件直通另需真实重复转发成本与宿主投递/唤醒/停止契约证据，本试点不自动授权实现或启用。

## 9. S0 逐项验收与执行前缺口

| 检查项 | 本文状态 | 开跑前补齐项 |
| --- | --- | --- |
| 固定基准 ref 与源码行为 | 已固定基准 ref、hash、源码行为；测试基线按主会话提供登记 | 实际加载服务/技能证明、原始测试日志 URI |
| 六个代表任务 | 选择准则、六槽位和 manifest 模板已固定 | 六个真实需求、固定源码/附件/状态及 hash |
| 可重复质量判断 | oracle 类别、结果与版本绑定口径已固定 | 每任务具体命令/期望、缺陷种子、独立评估者与冻结签认 |
| 36 轮顺序与隔离 | 矩阵、顺序、失败保留及 reset 要求已固定 | reset 可执行步骤、独立 context 和资源隔离证明 |
| 配置与来源 | 请求/实际/未知分离规则已固定 | A/B/C 提示与技能 manifest、C 最终 ref、真实宿主能力与 actor 配置 |
| 预算 | 估算方法、上限表与退出规则已固定 | 校准及单轮/总体上限、计量可用性、负责人签认 |
| 工具数、耗时、返工及成本 | 静态工具定义数与回归测试耗时已登记；每轮模板已固定 | 36 轮真实工具调用、耗时、返工与账单；当前全部待采集 |
| 采用决定 | 判定门和缺失处理已固定 | 实测与独立复核；当前不能作采用结论 |

因此 S0 的**定义文档交付可复核，实测准备与基线采集仍 open**。冻结后若任务、oracle、预算、配置或顺序发生变化，登记定义新版本与理由；受影响旧轮保留在原版本，不能悄悄改口径。后续 S4 使用本模板补齐实测，不把本轮开发和回归测试反向包装为工作任务试点。

## 10. 历史任务候选池（待复核，不是冻结输入）

为推进 S0 准备，从用户此前的 Task Progress 请求和已合并改动中筛出下列候选。**这不是六个冻结任务清单**：当前记录不足以证明每个原始请求、附件、初始状态和 reset procedure 都能无损复现；已公开的 PR 也包含答案，可能污染盲测。所有候选必须先还原并校验原始请求/fixture、独立 oracle 与隔离快照，再由负责人确认是否纳入。

| slot | 类别 | 历史请求/范围线索 | 可追溯基线 | 当前缺口 |
| --- | --- | --- | --- | --- |
| FIX-1 | 紧密耦合修复 | 主会话同一实例只允许一个 running leaf；其他工作排队或显式交给独立子智能体 | [PR #17](https://github.com/carpe-horam/task-progress-codex/pull/17)，base `8fb28a063d3fa9f5e14e217f8312c88ce9196d70`，head `47003587737663d85781ff8ecc5dc121adaa5547` | 原始用户提示与按持久化 attempt 时间重建的初始任务树已保存，见 [`fix-1-user-request.md`](inputs/fix-1-user-request.md) 和 [`fix-1-initial-state.json`](inputs/fix-1-initial-state.json)；归档不含原 task_instance_id，状态不是当时原始 API 响应；oracle 已草拟但 A/B/C 运行输入尚未冻结 |
| FIX-2 | 紧密耦合修复 | “所有仍未解决的任务”继续保持可见、可重试；旧 `continue/covered/obsolete` 不应遮蔽当前未解决结果 | [PR #20](https://github.com/carpe-horam/task-progress-codex/pull/20)，base `ce51d982ac1591a8dae2bef57716c3ff2c50c365`，head `ce3f35f37f1fa417fef7e6ca07c69f0ef8d3aaa9` | 原始请求与含历史 inventory 决策的最小状态快照/hash、精确注入条件待恢复 |
| INV-1 | 可并行调查 | 审查三个 NSlide 会话的任务拆分、依赖和实际进展是否匹配；历史请求来自 Task Progress Task 6 | 历史线程 `01a1a0c0-5b92-78f0-8337-e826b62033e5` | 会话当时状态和引用材料需导出快照；提示必须剥离已知结论，独立 oracle/rubric 需冻结 |
| INV-2 | 可并行调查 | 解释 `review_stage_inventory` 的 exact-set 报错是否符合规则，并区分规则正确性与本次失败原因 | 历史线程 `01a1a559-8d96-7a51-b85c-4a1bf3385f22`；原始错误提及线程 `01a19f04-f0e7-75f3-a8ab-9b6b11e75cf1`；提示与规则证据见 [`inv-2-user-request.md`](inputs/inv-2-user-request.md) 和 [`inv-2-evidence.md`](inputs/inv-2-evidence.md) | 被引用线程的提交时任务树、inventory binding/fingerprint 与失败调用材料仍缺；只可评估规则语义与可归因边界，不能判断原失败具体漏项/多项 |
| MOD-1 | 多模块改动 | 移除无用的外部前置 unknown 状态，保留图依赖与旧历史数据；跨服务端、UI、HTML/XLSX | [PR #23](https://github.com/carpe-horam/task-progress-codex/pull/23)，base `050dd3175e0e4dde9b6cb09da9bcd1f51c173b87`，head `4e4f2595609ab3649e7a3a38c8caabaa4b01a9a5` | 原始请求、基于旧 schema 的**合成**兼容 fixture 与 evaluator-only oracle 已整理；fixture 不是历史真实存储快照，且 PR 已公开，需控制答案泄漏 |
| MOD-2 | 多模块改动 | 原位修订计划时保留完整 before/after task tree，旧历史可读且不从现状伪造；跨服务端、历史 UI、导出 | [PR #19](https://github.com/carpe-horam/task-progress-codex/pull/19)，base `f517788feff00e35e9bf4ad77c7d23a8e5f8520d`，head `f3afeb1c22d35cf2e9e45955b33861a700d90bd2` | 原始请求、修订前/后 fixture 与旧 revision fixture 的 URI/hash，以及版本 oracle 待登记 |

候选来源说明：PR 描述用于定位历史基线和已知产物，不作为执行者输入；INV 槽来源于既有用户调查任务，不得把上述线程摘要直接视为完整事实快照。候选池只减少搜集范围，不替代六份 manifest。

### 已选校准范围与可复现性预检（2026-11-06）

负责人已确认“按候选做 9 轮校准”。按计划中的每类一项，当前拟选 FIX-1、INV-2、MOD-1；运行矩阵为每个输入各跑 A/B/C 一次，共 9 轮，正式 36 轮仍需另行决定。这个确认授权 9 轮范围，不等同于预算、宿主配置或输入快照已冻结。

| 候选 | 已核实材料 | 尚未冻结/不可声称的内容 |
| --- | --- | --- |
| FIX-1 | PR #17 的 base/head 对象存在于本地对象库；改动范围可定位到主会话并发/排队行为；请求、状态及 evaluator-only oracle 由 [`selected-candidates-v1.md`](inputs/selected-candidates-v1.md) 固定 | 归档不含原 task_instance_id，状态是 archive-derived reconstruction；实际 A/B/C 宿主配置和九轮独立 reset 证明尚未冻结 |
| INV-2 | 原始调查问题、规则证据和 evaluator-only oracle 由 [`selected-candidates-v1.md`](inputs/selected-candidates-v1.md) 固定 | 原失败树、inventory binding/fingerprint 与失败调用材料仍未还原；只能评估规则语义与归因边界，不能评价是否查明当次根因；实际宿主配置/reset 证明尚未冻结 |
| MOD-1 | 原始请求、旧 schema 合成兼容 fixture 和 evaluator-only oracle 由 [`selected-candidates-v1.md`](inputs/selected-candidates-v1.md) 固定 | fixture 不是历史数据库记录；PR 已公开，须从 worker 输入中排除；实际宿主配置与九轮独立 reset 证明尚未冻结 |

已恢复用户请求的转录字节（用于审计，不代表完整任务输入包）：

| 候选 | 转录 | SHA-256 |
| --- | --- | --- |
| FIX-1 | [`fix-1-user-request.md`](inputs/fix-1-user-request.md) | `5c60469aa151147d1513bfbb9538fed7ebfef63fd12928eefd659e9684225bf0` |
| FIX-1 | [`fix-1-initial-state.json`](inputs/fix-1-initial-state.json) | `ec52ee606fb27993a243fb2f370f5a833ce6024ee697c1f4948d83679a39a527` |
| INV-2 | [`inv-2-user-request.md`](inputs/inv-2-user-request.md) | `d00cad84341ead977dcde2686433de1d345c11abbdecbb7e6bb92673133e35f3` |
| INV-2 | [`inv-2-evidence.md`](inputs/inv-2-evidence.md) | `cc98abf2fe72986b9b66de79b8547e6e340edbfa594f34ccfb9c0979a56f5538` |
| MOD-1 | [`mod-1-user-request.md`](inputs/mod-1-user-request.md) | `55d9b4d840109aef4dafbae2055502232895c2bea0f744aafdc3f5da71fb32b8` |
| MOD-1 | [`mod-1-legacy-fixture.json`](inputs/mod-1-legacy-fixture.json) | `1c24c2e23e86ba1bfabda0e0f967eaa32f94646855cf1de99e9b51bbdac65c24` |
| evaluator-only | [`fix-1-oracle.md`](evaluator-only/fix-1-oracle.md) | `0aa97188d336964f6b516d7fca0a6422aacfad476d5ec1b9eae0eace2f297226` |
| evaluator-only | [`inv-2-oracle.md`](evaluator-only/inv-2-oracle.md) | `afc98812e88608bc14ef8de400c199d009c8e2d40003750871ec65e08101540c` |
| evaluator-only | [`mod-1-oracle.md`](evaluator-only/mod-1-oracle.md) | `9ccc79031854b21169dc434cc3b774d931d211a982bdfe6ce690be7fba578113` |
| selected candidates | [`selected-candidates-v1.md`](inputs/selected-candidates-v1.md) | `34280ae9f704a51b317c0c656df0383a4ef1a4a34c44191bd242decd07f64463` |
| selected candidates | [`selected-candidates-v2.md`](inputs/selected-candidates-v2.md) | `fb337534af7cb1d0bfed120e44031f4b20428c60303013fa40a780f18d19c0c3` |

因此目前可确认校准范围与候选选择，且请求、边界证据/fixture、基线 ref、交付范围和 evaluator-only oracle 已收入 [`selected-candidates-v1.md`](inputs/selected-candidates-v1.md)。FIX-1 的状态仍是基于 attempt 时间的重建，原 task_instance_id 未保留；INV-2 不能归因原失败具体提交；MOD-1 fixture 是合成输入，不是历史样本。候选材料有 hash，不代表 A/B/C 三种 workflow、实际宿主配置和九轮 reset 快照都已冻结。任何运行都不得拿 PR 描述、实现 diff、历史最终答案作为 worker 上下文。

### v1 时建议的下一步预算闸门（历史快照）

建议预算：每轮最多 20 分钟、9 轮累计最多 180 分钟、每候选每配置只跑一次、不重试；外层串行以便隔离工作区状态。当前宿主没有可核验的逐轮 token/费用计量读数，token 上限与费用保持 `unknown`，不得估填或宣称受控。若负责人不接受 token 计量为 unknown，应先接入可核验计量，再启动。候选输入/oracle 和 A/B/C 宿主参数冻结完成后，才进入运行；校准仍不代表授权正式 36 轮。

### v1 冻结时的准备结论（历史快照）

- **已确认**：校准范围为 FIX-1、INV-2、MOD-1；每项 A/B/C 各一轮，共 9 轮。顺序串行、每配置不重试的设计可继续沿用。
- **尚未确认**：建议的 20 分钟/轮与 180 分钟总墙钟上限；token/费用保持 `unknown` 的计量边界也未得到负责人确认。因此不启动任何轮次。
- **配置快照变化**：selected-candidates v1 是 v0.2.20 配置时冻结的 hash 清单；PR #27 发布后当前 marketplace/服务为 v0.2.21。该旧 manifest 仍保留原 hash 作为历史快照，但不能充当 v0.2.21 校准配置证明；开跑前需创建新的候选配置快照并重核 A/B/C。
- **输入已冻结的部分**：三项请求、输入/证据 fixture、基线 ref、交付要求和 evaluator-only oracle 已由 selected-candidates v1 逐字节固定；FIX-1 历史状态是 reconstruction，INV-2 原失败 payload/binding 缺失，MOD-1 fixture 是 synthetic。
- **仍未冻结/确认**：A/B/C workflow 与实际可用宿主/插件/技能配置、九轮隔离 reset 快照及校验回执、每轮/总墙钟上限、重试策略与 token/费用 unknown 接受边界。公开 PR 与历史答案不进入执行者上下文。
- **恢复条件**：确认数值预算和 unknown 边界后，再按实际宿主能力冻结 A/B/C 配置、逐轮 reset 证明并复核输入 hash；任一配置 unsupported 则明确排除该组，不冒充完成对照。

### v2 当前准备结论（2026-11-07）

- 负责人确认的范围仍是 9 轮；selected-candidates v2 hash 为 `fb337534af7cb1d0bfed120e44031f4b20428c60303013fa40a780f18d19c0c3`，并引用不可变 v1 请求/fixture/oracle 清单。
- A/B/C 共用 Codex CLI 0.166.0、请求模型 `gpt-6-luna`、effort `high`；A 不带 Task Progress MCP 且禁用多智能体，B/C 分别使用直连 stdio 的 v0.2.19/v0.2.21 服务。两个版本 initialize 和 35-tool 列表预检通过。Codex `exec` 的实际工具可见性仍待确认。
- 九个隔离 worktree 均从候选 ref 创建并读回 clean；九份 blinded prompt 各有 SHA-256。当前 prompt manifest hash 为 `8584f2ebf3da3eee33297272b765c0a1fb4c768d89dcb03cd015923eefa43f4b`。
- FIX-1-A 已完成一次校准：395.116 秒，exit 0；输入 3,722,401 tokens（其中 cached 3,606,528，包含在 input 总数内），输出 19,099，费用 unknown。独立 oracle 对准质量门 F1–F6 pass、F7 fail，整体质量门 fail；准确结果及 artifact hash 见 `/private/tmp/task-process-s0-calibration-v2/results/FIX-1-A.oracle-evaluation.md`。该产物留在私有评估目录。
- 剩余 8 轮未启动。10 分钟/轮、90 分钟总墙钟及一次 prompt 暴露前重试是先前准备时采用的建议默认值，并未得到明确预算授权；FIX-1-A 产生的实际 token 用量也超过此前的可见预估。现等待负责人确认是否继续接受无 token/费用上限，或设定上限。

### 2026-11-07 宿主配置复核

- A/B/C 共用独立 Codex CLI 执行路线，避免依赖桌面会话里全局暴露的插件目录：A 用 `codex exec --ignore-user-config --disable multi_agent`；B/C 用相同 CLI 和 `--ignore-user-config`，以命令行 `mcp_servers.task_progress` 覆盖连接到各自版本的 stdio server。
- 当前 `codex plugin list --json` 显示桌面插件 0.2.21 已启用；共享服务 loaded/healthy，端口 53421。v0.2.19 本机服务 `server.py` SHA-256 为 `d8787370aa1409467cd5fa79637b894a89e4f714742bd4e8d99a19115b2934d1`，v0.2.21 为 `ce904cc9d01894f9be04bd40aeac19f24ac5c488c4956e03ee6bc97cee98da01`。两版本直接 stdio initialize 与 `tools/list` 均成功，各返回 35 个工具；不切换全局服务。
- 九份独立 Codex CLI prompt 与九个 clean detached worktree 已准备，具体输入、角色规则、plugin/skill refs、hash 和准备时预算见 [`selected-candidates-v2.md`](inputs/selected-candidates-v2.md)。FIX-1-A 的实际 `codex exec` 已验证 A 组 CLI 可启动并产出；本项不替代 B/C 各自工具可见性读回。
- 准备时写入的预算默认值为 10 分钟/轮、90 分钟总墙钟、prompt 暴露前最多一次基础设施重试、token/费用 `unknown`。FIX-1-A 记录到 3,722,401 input（含 3,606,528 cached）和 19,099 output tokens，费用 unknown；该轮已先行执行，但其余 8 轮暂不启动，等待负责人确认 token/费用边界。
- 没有切换全局插件、停止服务或复制凭据。v1 候选 manifest 保持不可变；配置快照 v2 保持原 hash，运行结果单独记录。
