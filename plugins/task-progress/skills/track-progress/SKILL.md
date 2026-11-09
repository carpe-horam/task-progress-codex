---
name: track-progress
description: 在 Codex 主会话与 agent 开始实质工作前先做粗粒度规划；适用于实现、修复、研究、审查、合并 worktree、验证和提交 PR。多阶段任务先建 HTN 计划，执行中动态拆分和补充。
---

# Track Progress — 任务进度跟踪与展示

Task Progress 是会话的计划与执行账本，不代替实际工作、派发或验收。用户、宿主及更高优先级指令优先。

本文件给出操作顺序。进入适用的步骤或条件分支前，读取该步骤或分支指定的引用；引用中的参数、状态转换和例外同样适用。只读当前需要的材料，不必一次加载全部引用。

## 1. 判断如何开始

实质工作前，先明确目标、验收、阶段、依赖和执行者。准备性读取与必要澄清可以先做；编辑、验证和派发须在规划后进行。

| 情况 | 动作 |
|---|---|
| 简单问答 | 无需任务树。 |
| 单步查询或简单操作 | 简短规划即可；不强制初始化。若需要跟踪不可再分的单步任务，初始化时填写具体 `atomic_reason`。 |
| 两个或以上可辨识阶段 | 默认使用 HTN 跟踪，即使只能顺序执行。先读 [开工前短流程](references/startup.md) 与 [HTN 初始化与分解](references/htn-planning.md)。 |
| 已有任务继续、压缩恢复或用户补充 | 先按第 5 节恢复；不要重新初始化已有任务。 |
| 用户要求打开任务进度 | 读取本会话已有进度并按需展示；这不代表开始新任务。 |
| 用户明确要求线性步骤，或恢复旧线性任务 | 使用 [旧版线性 API](references/legacy-linear.md)。 |

## 2. 建立任务并启动

1. 首次建任务调用 `init_task_network`。已有任务时，同一目标的继续、归档旧计划或新一轮规划使用 `rebuild_task_network`，即使旧计划已收束也不创建新实例。仅独立新目标且符合切换条件时复用 `session_id` 初始化，并填写 `task_relationship="independent"` 和 `new_task_reason`；“归档并新启计划”本身不是独立目标的依据。
2. 多阶段任务优先一次传入至少两个有独立结果边界的 `tasks`，每项填写计划 `assignment.execution_mode`。尚未提供完整阶段时，立即调用 `decompose_task`；已有完整阶段则无需重复分解。
3. 确认任务结构、依赖和执行者，再请求展示：主会话首次规划后调用一次 `render_progress`。回执不代表宿主已显示；普通更新、继续和恢复不重复调用，用户明确要求重开时除外。
4. 调用 `get_ready_tasks`，只启动依赖满足的 ready 叶节点。未规划的空 root 不能开工；使用 `atomic_reason` 初始化的原子 root 是可启动的叶节点。不要为凑数量制造子任务或单子项分组。

保存工具返回的 `session_id`、`task_instance_id`、`epoch`、`root_id` 和启动后的 `attempt_id`。当前任务的写操作传入当前实例和所需的尝试 ID；不要推算 ID。写回执只证明身份与状态，不包含完整证据。需要查询范围、依赖证据或分页时读 [查询与展示入口](references/query-and-display.md)。

### 身份与失败处理

- 所有 ID 都是 opaque 值，只能逐字复用插件成功回执或本会话已确认记录中的值。`session_id`、`task_instance_id`、`epoch`、`task_id`、`attempt_id` 和执行 ID 含义不同；不得用 Codex thread ID、另一个会话的 ID、UUID 片段、递增/补齐字符或猜测值相互替代。首次初始化可以省略 `session_id` 让工具生成；收到回执后立即保存并在后续调用中原样传回。
- 继续既有任务但找不到准确的 `session_id` 或当前 `task_instance_id` 时，不要试探候选值或反复初始化。先从本会话最近一次成功回执、已保存的任务上下文或明确的宿主记录中恢复；仍无法确认时，说明缺少哪个 ID 并暂停依赖该 ID 的操作。只有确认是新的独立目标时才初始化新实例。
- 每次工具失败后，先读该次确切错误，核对最近一次成功回执里的 ID、epoch、task 和 attempt，再决定是否能按错误信息做一次有依据的更正。禁止原样重试失败调用、轮换或篡改 ID 碰运气；一次有依据的更正仍失败，就停止该调用路径，保留错误并检查适用的 skill/工具契约，或报告缺少的信息。不要把失败查询当作状态证据。
- 写操作成功后先保存回执中的身份与状态；仅在需要核实具体任务内容、证据、依赖或下一步时调用对应只读查询。不要因收到成功回执就查询 full，也不要在没有状态变化或新问题时重复刷新。

## 3. 分工和派发

主会话负责目标、计划、协调、复核和根任务验收。获派子智能体只更新自己的节点，不建立重复 root，也不验收自己的结果。

- 在用户和宿主允许委派时，复杂分析、实现、专门验证及独立评审默认委派。紧密耦合的一条结果链优先复用同一 worker 完成调查、实现与局部核验；阶段切换本身不要求新 agent。主会话设定结果边界、依赖、资源与最终验收，worker 自主安排范围内步骤，发现新独立结果或范围变化时回主会话调整。串行依赖本身不是主会话包办的理由。
- 主会话自执行叶任务时记录具体 `self_execution_reason`；已记录 `atomic_reason` 的原子 root 除外。共享文件先明确 owner 并串行交接。分工与改派规则见 [协作与职责](references/delegation.md)。
- 实际派发前，读取 [协作与职责](references/delegation.md)、[模型选择](references/model-selection.md) 和 [HTN 派发](references/htn-dispatch.md)。遵守宿主允许的模型、强度和委派规则；不能获得的配置不得虚构。
- 子智能体启动先读 overview 的 `protocol_capabilities`。当含 `assignment_sources_v1` 时，模型与强度来源可分别记录为 `explicit`、`inherited` 或 `unknown`；继承/未知不带具体值，也不阻止宿主已支持的委派。没有能力标志时沿用旧服务端流程，不发送新字段。不要猜测模型或配置值。
- 展示派发信息并提供自包含的背景、目标、约束、路径及验收条件。执行顺序是：`update_task` 启动 attempt → 调用实际协作工具 → `bind_task_agent` 绑定返回的真实 ID。`send_message` 只投递上下文，不证明 idle worker 被唤醒；要启动/唤醒 owner 使用 `followup_task`。记录 assignment 不等于实际派发或宿主执行回执。
- 每次 attempt 记录执行角色。主会话模型、强度及宿主 ID 未明确提供时保持未知，不从配置或目录猜测；详见 [会话元数据](references/session-metadata.md)。

## 4. 执行、复核和调整

遇到以下情况时，执行对应动作；多项同时发生时，分别处理。

- **仅保存当前时点或用户要求归档快照** → `snapshot_task_network`，使用稳定 `snapshot_id`；允许进行中与待验收，不停止执行、不切换 epoch。快照只含当前实例当前计划及其记录，不复制历史计划或其他实例。
- **只需修改未开始分支的依赖、分组或同级顺序** → `revise_task_plan`，携带当前 `epoch`、`revision` 和原因；读取依赖结果后判断策略，不能为了放行而跳过真实前置。重分组仅限受影响分组及消费者均未开始，不能原位改写执行事实。
- **需要大范围重构同一目标的计划** → 协调并结束运行尝试、完成待验收，再用 `rebuild_task_network`。保留旧计划，逐项承接未解决事项；不因保存快照而重建。
- **独立新目标** → 按收尾及请求路由规则，初始化时明确 `task_relationship="independent"` 与原因；不得把同一目标的新计划当新任务。

- **任务开始、状态变化、等待、阻塞或完成** → 及时更新对应节点。真实工作仍在运行时保持 `running`，不要把等待记成完成。
- **叶任务失败或以 open 收尾** → 原因明确时填写 `failure_reason`，不明确时不猜测。重试回执出现 `retry_review_due` 时，结合最近尝试的原因和摘要判断问题是否相同或相关；必要时评估优化或重新规划，记录判断和下一步；详见 [重试与重规划](references/htn-retry.md)。
- **主会话切换到其他工作** → 检查原叶节点是否有独立继续的实际工作。若没有，先以 `finished/open` 收束并记录剩余工作；恢复时用新 attempt 重试。真实运行中的工作可保持 `running`。详见 [规划与执行生命周期](references/planning-and-lifecycle.md)。
- **主会话完成自执行叶任务** → 按实际结果收束，不把未解决工作记为成功。
- **子智能体报告 `finished`** → 这只是待复核提交。先读 [HTN 验收](references/htn-review.md)；主会话读取 submission 和 evidence，再调用 `review_task` 接受或退回。待复核结果不能放行依赖。
- **准备验收有子任务的父节点或 root** → 由主会话综合验收。只有所有直接子结果已解决且无待复核项时才可 `resolved`；未解决、失败或取消须如实保留。原子 root 按自执行叶任务收束。详见 [HTN 验收](references/htn-review.md)。
- **主要阶段结束，准备开始下一阶段** → 检查 `stage_inventory_due` 和 `stage_inventory_due_has_more`。无有效提醒时不调用盘点工具；有提醒时按 [生命周期](references/planning-and-lifecycle.md) 找全提醒和未解决叶节点，并用 `review_stage_inventory` 逐项记录理由。没有未解决叶节点时提交空 `decisions`；盘点不改变任务状态。
- **HTN 叶节点超过 10 分钟，或收到拆分复核门禁** → 多个独立结果使用 `decompose_task`；唯一剩余结果用 `review_task_split` 提交理由和证据，最多有效 10 分钟且绑定当前执行。摘要文字不能豁免。不要为拆分中断实际运行。旧线性步骤仍按 [生命周期](references/planning-and-lifecycle.md) 复核。
- **启动命令或外部运行** → 已授权的本地命令优先通过 `start_task_execution` 获得自动退出回执；项目要求走 Dagu 时仍走 Dagu，并用 `record_task_execution` 将实际 run ID 和终态绑定当前 attempt。每 attempt 仅一轮执行；失败或阻塞后必须收尾并用 `retry=true` 取得新 attempt，不能在 running 上覆盖 run ID。成功命令仍须验收任务，外部回执缺失保持未知。
- **范围或依赖变化** → 先调整计划，再启动新增工作。追加、重试、结果失效和重规划的规则见 [生命周期](references/planning-and-lifecycle.md) 和 [HTN 重试与重规划](references/htn-retry.md)。

## 5. 恢复、分流和收尾

- **继续已有任务或压缩后恢复** → 先读 `get_progress(view="overview")`，再读 `view="context"` 和全部请求分页；按需查询 tree/tasks。复用现有实例，不要重新初始化来恢复。
- **新消息补充或修正原目标** → 更新上下文，按需调整计划，继续原任务。
- **新消息是状态询问或简短问答** → 回答后继续原任务，通常不另建任务树。
- **新消息是独立请求，而当前任务尚未收束** → 用 `route_request` 排队；只有资源可隔离、宿主允许且有合适的子智能体容量时，才考虑隔离并行实例。并行实例的叶任务须由子智能体执行；没有合适执行者时排队。不要覆盖原任务。队列、压缩恢复和移交详见 [请求分流与压缩恢复](references/request-routing.md)。
- **用户明确停止、取消或替换目标** → 服从用户指令，协调在途执行并如实收束；不要因队列要求继续工作。
- **准备最终收尾** → 查询根结果和全部请求分页，检查剩余工作并如实汇报。未解决任务不能伪装成成功；收尾与验收见 [HTN 验收](references/htn-review.md)。
- **任务完成或视图关闭** → 保留进度，不自动清理。展示、历史和会话身份细节见 [展示、恢复与清理](references/session-display-cleanup.md)。
- **用户明确要求清理** → 先按 [清理技能](../cleanup-task-progress-history/SKILL.md) 核对明确归档依据和删除范围。`clear_progress` 会清除该会话的全部进度；未满足清理条件时不调用。
