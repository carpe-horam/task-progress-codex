---
name: track-progress
description: 在 Codex 主会话与 agent 开始实质工作前先做粗粒度规划；适用于实现、修复、研究、审查、合并 worktree、验证和提交 PR。多阶段任务先建 HTN 计划，执行中动态拆分和补充。
---

# Track Progress — 任务进度跟踪与展示

## 开工前的短流程

任何实质工作开始前先判断任务粒度并做粗粒度规划。简单问答无需任务树；一个可独立完成的单步任务用 `atomic_reason` 记录判断；其他任务先拆成有清晰结果边界的阶段。准备性读取、收集约束和必要澄清可以先进行，但不要先编辑、运行验证或派发子智能体。

新任务优先一次调用 `init_task_network`，传入至少两个阶段的 `tasks`，每项包含 `assignment.execution_mode`；如暂时无法一次确定，可先初始化再立即 `decompose_task`。服务端会把未规划 root 标为 `planning_status=required` 并拒绝直接开工。确为单步时传入具体 `atomic_reason`。可传稳定 `request_id` 安全重试同一个初始化调用。

继续当前任务先 `get_progress`，复用现有任务实例；不要重复初始化。“打开 task process / task progress / 任务进度”意味着读取当前会话已有进度并按需展示，不代表开始新任务。压缩恢复、用户补充范围、阶段切换时检查并更新任务树。子智能体只更新获派节点，不创建主会话 root 或单独展示主面板。

插件 hooks 会在会话启动和用户提交新指令时提醒这套规则。hooks 需经 Codex 信任审核才会生效，并且宿主覆盖不完整；服务端规划校验才是插件内的硬约束。

## 何时使用与模式选择

### 自动调用（Codex 主会话与 agent 均适用）

**任何任务开始前都先进行粗粒度规划。**在 Codex 主会话与 agent 开始实质工作前，先明确目标、主要阶段、依赖关系、执行者和验收结果；此要求覆盖所有任务，不依赖项目中存在 `AGENTS.md`/`agent.md`，也不要求用户点名。规划允许先按已知信息给出粗略版本，调查后再动态完善，不要为了等完整根因而先做实质实现。

任务包含 **2 个或以上** 可辨识阶段时，自动启用本 skill 并调用 Task Progress 跟踪，这是默认工作规则。先初始化或恢复任务网络，再把粗粒度阶段写入 HTN 子任务树；**不能只创建一个空的 root 节点就开始实现**。必须先调用 `decompose_task` 并确认主要阶段、依赖和执行者，再用 `get_ready_tasks` 判断哪些阶段可以启动。若任务确实是一个不可再分的简单操作，可保持为单一任务，不为满足形式而制造子项，但仍须在开始前形成简短计划。此规则覆盖主会话和 agent。

若 agent 收到的工作已由主会话的 Task Progress 任务树表示，沿用派发信息中提供的任务节点与尝试标识更新该节点，不另建重复根任务；若 agent 独立承担多阶段工作且没有可更新的父任务节点，则先粗略规划，再初始化、分解并维护自己的任务网络。单步查询或简单操作无需初始化任务树，但仍须先做一句话级别的粗规划。

多步骤新任务默认使用 HTN 任务网络：`init_task_network` → `decompose_task` → `get_ready_tasks` → `update_task`。不得因任务看起来简单、顺序执行或不需要并行而改用线性 API。`init_progress` 对两个及以上步骤默认会被服务端拒绝；只有用户明确要求线性步骤时才可传 `legacy_linear=true`。

- 任务有子任务分解、依赖、并行、委派或重规划时，继续使用 HTN 任务网络表达结构与状态。
- 仅当用户明确要求线性步骤，或恢复已有旧版线性任务时，使用 `init_progress` → `update_step`；恢复时先 `get_progress`，再按原数据模式继续，不要求把旧状态迁移成网络。需要按步骤显示执行者、模型和思考强度时，在启动步骤的 `update_step` 调用中附上 `assignment`。
- **10 分钟拆分复核点：**任一仍在执行的 HTN 叶节点或线性步骤超过 **10 分钟**时，主会话必须明确评估“是否应拆成更细的任务展示”，不能只更新耗时而跳过评估。检查其中是否已有可独立执行、并行推进、存在依赖边界或可单独验收的阶段：有则把未完成工作拆成子任务并记录依赖；若当前仍是线性步骤且任务已适合分层展示，则按线性转 HTN 的规则重规划。评估后若没有自然任务边界，或细分不会改善执行与进度可见性，就保留原粒度，并在进度摘要中记下“拆分复核：无需拆分—原因”。`get_progress` / `update_task` 返回 `split_review_due` 提醒时，必须在继续常规工作或再次等待前处理：拆出可独立阶段，或记录具体的不拆理由。长命令/Dagu/CI 正在运行且不能安全中断时，不要为了拆分而终止它；保留原 attempt，同时拆出不会干扰它的独立阶段，或说明当前没有安全边界。**超过 10 分钟是强制评估点，不是自动拆分条件**；任务范围后来变复杂时，即使尚未达到 10 分钟也应重新评估。
- 线性任务转 HTN 时，不要在同一任务实例混用 `update_step` 与 `update_task`。在同一 `session_id` 下初始化 HTN 网络会把旧线性任务归档为历史；新网络沿用原目标，摘要已完成工作并保留其证据，只将未完成工作分解为新节点，避免把已完成部分伪装成待重做。若不值得转换，就继续原线性任务。
- 每个 Codex 会话复用一个 `session_id`。首次初始化可让工具生成并返回 ID；保存它，之后本会话的新任务必须显式传入同一个 ID，不为每个任务生成新 ID。插件按 `session_id` 隔离历史，换成新 ID 就会新建另一套任务记录，旧任务不会出现在新卡片的历史中。
- 恢复当前任务时调用 `get_progress({"session_id":"<本会话 ID>"})`，根据返回的数据模式继续执行，并保存当前 `task_instance_id`；不要调用初始化工具来恢复。
- 开始新任务时默认调用 `init_task_network`。在同一 `session_id` 下重复初始化会先归档当前任务，再创建新任务并返回新的 `task_instance_id`；已打开的面板刷新后显示新任务，展开“历史任务”可查看旧任务。不要为新任务再生成 `session_id`，也不要用 `rebuild_task_network` 代替新任务初始化。
- 若环境提供内置计划工具，可同步维护可检查清单；它不能替代插件中的实际状态。

### 开工前粗拆分与执行中动态补充（必须）

- **开工前先拆主阶段。**先把用户目标整理成若干粗粒度、可检查的结果阶段，并标明已知依赖和预计执行者。基于当前信息先建立可执行的第一版计划；调查中发现新约束后再调整。不要把代码调查、根因分析、实现、集成、验证、交付等明显不同的工作全部塞进一个长期 `running` root/leaf，再期望 10 分钟提醒替代最初拆分。
- **先有结构，再启动阶段。**多阶段任务调用 `init_task_network` 后，立即用 `decompose_task` 建立主要阶段；完成后调用 `get_ready_tasks`，只启动依赖已满足的 ready 叶任务。除非任务确实原子且无合理边界，否则 `root.children` 为空不能作为已完成规划。
- **粒度保持粗而有效。**按可独立推进、可单独验收或有清晰依赖边界的工作拆分，不把每个命令和微步骤都建成节点。可将同一缺陷下的调查与修复合并，也可将不同缺陷、独立审查、共享门禁拆成有依赖关系的阶段；门禁或资源等待本身不抹掉已存在的阶段边界。
- **执行中持续重看计划。**每完成一个主要阶段、收到用户新要求、遇到阻塞或根因导致范围改变时，对照剩余工作检查是否出现新阶段、依赖或验收项。发现后先在对应未完成分组下用 `decompose_task` 追加子任务并修正依赖，再开始这部分新工作；不要另建重复 root，也不要只把新增范围写进摘要。
- **尊重正在运行的工作。**若要拆分当前 `running` 叶节点，先协调其执行者停止或移交，再按本 skill 的 attempt 规则分解；不得为更新计划而中断不能安全停止的长命令、Dagu 或 CI。可先把不干扰运行门禁的独立后续阶段加到合适的未完成分组下；没有安全边界时记录具体原因，并在门禁结束后的第一个安全点重新拆分。
- **复核点是动态调整的兜底。**10 分钟 `split_review_due` 必须立刻处理，但它不能替代开工前粗拆分，也不能成为长期保留单一大任务的理由。记录“不拆分”时必须说明该任务为何不可独立推进或验收，而不只是“正在等待门禁”。

### 执行期间持续维护进度

Task Progress 是执行过程中的状态账本，不是只在开工时生成一次的计划。初始化和分解后，主会话须在后续工作中持续使用同一个 `session_id` 和当前 `task_instance_id`，直到任务收尾。

- **状态变化时立即更新。**开始一个工作块、实际派发子智能体、切换到另一叶任务、收到阶段结果、启动/完成较长命令或门禁、遇到阻塞、改变计划时，及时更新对应节点的状态与摘要。不要等到整项工作结束才一次性补记，也不要只在普通消息里汇报而不更新任务树。
- **切换主会话焦点时收束旧尝试。**主会话是单一执行者；开始处理另一个 `main_session` 叶任务前，先判断当前运行中的主会话节点是否仍有真实工作在继续。若只是暂时搁置、准备稍后回来，或主会话已经转去推进兄弟任务，就结束当前 attempt 并标记 `finished/open`，摘要写清已完成部分、剩余工作和恢复条件；恢复时用 `retry=true` 创建新 attempt。只有命令、Dagu/CI 或其他可独立继续的实际工作仍在运行时，才保留 `running`，并在摘要中记录运行 ID、等待结果和下一检查点。不能仅因计划稍后续做，就让主会话节点一直显示 `running`。子智能体的确切执行仍在后台时可保持其节点运行，并注明执行者和等待对象。
- **等待期间如实留痕。**Dagu、CI、构建或子智能体仍在运行时，节点保持 `running`；摘要记录当前运行 ID、已完成阶段和正在等待的结果。收到有意义的新阶段或终态后再更新，等待中不要反复写没有变化的摘要，也不能把“已排队/仍运行”记成完成。
- **继续执行前重新锚定。**长时间等待返回、上下文压缩/恢复、用户补充要求后若原目标仍继续时，先 `get_progress` 读取当前任务实例和活动节点，再继续或调整计划。不要因此另建重复根任务；若用户明确开启新目标才调用初始化归档旧任务。
- **执行提交与接受结果分开。**子智能体只更新获派节点并提交结果；`finished` 上报进入 `awaiting_review`，主会话复核证据后另用 `review_task` 接受或退回。主会话负责派发、协调、验收及根节点。未解决、失败或被阻塞时如实保留 `open`/`failed`/`cancelled`。
- **汇报简洁但账本完整。**对用户只在关键变化时说明进展，避免重复播报；任务卡片则应保留足以恢复工作的当前动作、阻塞、验证状态和证据路径。

## 协作原则

### 主会话与子智能体的职责边界

- **主会话是任务负责人和验收者。**主会话负责目标、计划与拆分、选择和派发执行者、协调依赖与文件归属、跨任务决策、接受或退回结果、综合验收与用户汇报。集成责任包括判断接口和冲突处理方案；复杂集成实现和专门验证仍默认委派。分组/root 的综合验收只能声明 `assignment.execution_mode=main_session`，并提供非空摘要与可复核证据。
- **子智能体是受委派的执行者。**它可在明确派发范围内深入分析、实现、多文件改动、专门验证或评审，更新获派叶节点并提交结果与证据；不得重分配全局计划、接受自己的结果、验收父/root 或更新兄弟节点。完成报告写明改动文件、验证命令与结果、证据及剩余风险，由主会话单独复核。恢复或 fork 不转移父任务所有权；只沿用明确交接的节点，不另建重复主 root。
- **先做执行者分配，再开始工作。**`planned_assignment` 保留计划分工，`assignment` 记录当前 attempt；启动不能静默更换 `execution_mode`。pending 叶节点改变分工用 `reassign_task`，由主会话声明 reviewer 并记录具体 reason；运行中先协调停止，已结束任务要改变计划则显式重规划。
- **复杂工作默认委派，与能否并行无关。**开工规划时按工作性质和验证范围判断：复杂分析、实现、多文件改动、专门验证、独立评审默认由子智能体执行，只有一个 ready 节点或只能串行也适用。依赖链可整体交给同一 agent；共享文件指定单一 owner 并串行交接，不能以“有依赖”“文件冲突”“需要集成/上下文”作为主会话包办的通用理由。多个独立 ready 节点可并行派发。
- **主会话自执行是明确例外。**短小低风险操作、协调决策与验收可自执行；确实不可交接的复杂工作要记录具体限制（如环境没有可用委派工具及哪些能力无法交接）。在计划和启动的 assignment 中填写非空 `self_execution_reason`，可用 `complexity=simple|complex` 记录判断；已显式记录 `atomic_reason` 的原子 root 除外。失败或修改优先反馈给原 agent；主会话接管必须走显式分工变更，不能以重试偷偷改角色。不要用泛化原因或等执行十分钟后才决定是否委派。
- **显式记录每个子任务的预期执行者。**调用 `decompose_task` 时，每个子任务都必须提供 `assignment.execution_mode`；明确委派的叶节点设为 `subagent`，由主会话直接执行的叶节点设为 `main_session`。分组节点设为 `main_session`，表示主会话负责该组的协调与综合验收；其子节点仍可分别分配给子智能体。这里记录的是节点的计划执行者，不转移根任务的总负责人职责。准备实际启动时再在 `update_task` 中记录本次 attempt 的派发工具、完整任务文本、模型和思维强度。

开始前明确目标、交付物、约束和验收条件，将工作拆成可检查任务，标明父子关系、依赖、执行者和可并行项。有依赖的任务按顺序推进；独立任务按上述规则分派。主会话负责拆分、派发、协调、集成、验收和维护进度。

每批创建子智能体前，先展示表格，包含：工具标识与中文名称、实际派发的完整任务文本、`fork_turns`、模型和思考强度。统一设置 `fork_turns="none"`，按难度与风险选择当前可用的模型及思考强度。派发文本必须自包含，写清背景、目标、约束、必要路径和验收条件；不要依赖主会话上下文。并行修改须先明确文件归属，并优先复用已有工作区，避免相互覆盖。

插件保存任务与派发记录、校验计划角色及状态转换；实际创建、联系和等待子智能体由主会话调用协作工具完成。记录 assignment 不代表已派发。`execution_mode`、reviewer、agent ID 都是调用方声明，HTTP Bearer 仅证明持有服务凭据，不能认证主/子会话身份。attempt/instance ID 防止迟到更新，不是角色授权凭证；插件不能拦截其他工具中的实际工作，也不能保证调用者遵守获派文件边界。旧记录的未认证身份或未记录验收保持未知，不回填虚构接受记录。

每个执行尝试启动时，必须在 `assignment` 中记录 `execution_mode`（`main_session` 或 `subagent`）。当前 MCP server 不会自动识别 Codex 的活动 thread/turn，也没有宿主认证的元数据通道。调用方填写的模型或思维强度会标明“未经 Codex 宿主核验”。主会话值未知时省略未知字段并继续任务，不猜测、不阻塞；子智能体记录本次派发调用明确指定的配置，这代表派发配置，不代表后端执行回执。

#### 主会话模型与思维强度记录规则

1. **优先使用当前 thread/turn 的宿主元数据。**只有 Codex 明确提供且绑定当前会话的值，才可称为宿主提供。`codex_thread_id` / `codex_turn_id` 也必须来自宿主，不能从其他会话或历史记录代填。当前插件尚未接入这条通道。
2. **区分会话设置、本轮请求和后端执行回执。**当前插件可保存的是调用方报告值，不能验证它是否等于 Codex 活动设置或后端实际执行值。不要把配置文件默认值或子智能体派发值描述成主会话实际运行值。
3. **不从配置或模型目录推断活动值。**配置文件只能说明默认/项目配置，模型目录只能说明可选模型；除非 Codex 宿主确认它就是当前 thread/turn 的有效值，否则不填入活动设置字段。
4. **允许部分值未知。**初始化时可传单独已知字段，例如 `main_session: {"model":"<宿主明确提供的模型>"}`；未知字段省略。两项都未知时可省略整个对象。主会话启动叶任务仍需 `execution_mode: "main_session"` 与具体 `self_execution_reason`（原子 root 例外），不因缺少可选模型元数据阻塞工作。
5. **每次 attempt 单独记录。**不要从初始化快照复制模型/思维强度到后续 attempt。值若未在本轮获得，记录未知；切换后新 attempt 使用新获得的值，不回写旧记录。

正确的初始化形态（仅在两个值都已核实时传入；尖括号内容必须替换为本会话实值）：

```json
{
  "session_id": "<本会话 ID>",
  "task_title": "<任务标题>",
  "main_session": {
    "model": "<当前会话实际模型 ID>",
    "reasoning_effort": "<当前会话实际思考强度>"
  }
}
```

若任一值未知，只省略对应字段；两项都未知时可省略整个 `main_session`。不得照抄文档示例或其他会话的值。快照由调用方提交，卡片会显示采集时间与核验状态。主会话综合验收父任务的 assignment 同样允许未知，不要伪造值。

## HTN 操作顺序

### 1. 初始化与分解

本会话已有 ID 时，调用 `init_task_network` 并传入 `session_id` 与 `task_title`，保存响应的 `session_id`、`task_instance_id`、`epoch` 和 `root_id`。只附加当前上下文明确提供的 `main_session` 字段；未知字段省略，不要用配置猜测。首次初始化尚无 `session_id` 时可省略该参数；后续开始新任务复用同一 ID，并保存新返回的 `task_instance_id`。用返回的根 ID 分解；以下以首轮 `root` 为例：

<!-- delegation-example:plan -->
```json
{
  "session_id": "<本会话 ID>",
  "task_instance_id": "<当前任务实例 ID>",
  "task_id": "root",
  "tasks": [
    {"id":"inspect","title":"深入调查并确认根因","assignment":{"execution_mode":"subagent","complexity":"complex"}},
    {"id":"assets","title":"整理独立素材","assignment":{"execution_mode":"subagent"}},
    {"id":"fix","title":"实施修复及跨文件集成","depends_on":["inspect"],"assignment":{"execution_mode":"subagent","complexity":"complex"}},
    {"id":"verify","title":"专门回归验证","depends_on":["fix"],"assignment":{"execution_mode":"subagent","complexity":"complex"}},
    {"id":"accept","title":"判断交付是否满足目标","depends_on":["verify","assets"],"assignment":{"execution_mode":"main_session","complexity":"simple","self_execution_reason":"主会话汇总已复核证据并做最终交付决策"}}
  ]
}
```

将以上对象传给 `decompose_task`。每个子任务都必须在 `assignment.execution_mode` 标明计划执行者；分组节点标为 `main_session`，叶节点按分工标为主会话或子智能体。尚未确定执行者时，先完成分工评估再调用工具，不要留空后默认全由主会话处理。可继续分解未结束的叶任务形成层级；也可对尚未结束的同一父任务重复调用 `decompose_task`，把新一批子任务追加到现有 children。每个实际委派的子智能体都必须有自己的子任务节点，不能只在消息或根任务备注中记录。父任务进入终态后不能再追加子任务；计划结构需要重做时使用重规划。任务 ID 必须由 1–64 位字母、数字、连字符或下划线组成，并在当前 `task_instance_id` 的生命周期内唯一，同一任务实例的不同重规划轮次也不可复用；新任务实例可重用已归档任务的节点 ID。所有依赖必须有效且无环。

分解 `running` 节点时，先协调旧执行者停止或移交工作，再在 `decompose_task` 中传入它的当前 `attempt_id`。插件会将该尝试以 `cancelled` 归档，保留配置、摘要、证据和时间；节点转为等待子任务的分组，原 `attempt_id` 不再有效。插件不会实际停止子智能体或进程。若分解被拒绝，原尝试保持不变。不要先把节点设为终态再调用分解，因为终态节点不能追加子任务。

`depends_on` 描述前驱。默认 `dependency_policy="resolved"`，仅前驱已接受且 `outcome="resolved"`（或主会话直接完成）才放行。子智能体提交后尚待 review 时，包括显式 `dependency_policy="finished"` 的下游也必须等待。`finished` 策略允许已复核的 `finished/open`、`failed`、`cancelled`，仅在下游能处理未解决结果时使用；主会话传递前驱摘要和证据，不能把放行当作已解决。

分组节点的依赖约束也适用于其全部后代，并沿用声明该依赖的节点的 `dependency_policy`；子节点自己的策略不能放宽祖先约束。ready 任务的 `dependencies` 包含继承的前驱结果与 `inherited_from`。不得让子任务依赖其祖先，或创建跨分组的完成等待环。

### 2. 获取 ready 任务并实际派发

实际调用 `collaboration.spawn_agent` 前，先将任务挂到父节点、分配 `subagent`，再调用 `get_ready_tasks`；只启动 ready 叶节点。复杂工作即使只能串行仍委派，同一依赖链可顺序交给同一 agent；共享文件明确 owner 并串行交接。Task Progress 不会自动创建子智能体。

**顺序是 start → spawn → bind。**派发前展示协作表格，先用 `update_task` 开始 attempt，保存返回的 `attempt_id`，再调用真实协作工具。新 subagent attempt 要有非空 `tool_identifier`、完整 `dispatch_text`、模型及思考强度。agent ID 尚未返回时省略 `agent_name`，不得填虚构占位 ID；不可变 assignment 与可补充的 dispatch 绑定分开保存。

调用 `update_task`（启动示例）：

<!-- delegation-example:start -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "execution_status":"running",
  "assignment":{
    "execution_mode":"subagent",
    "complexity":"complex",
    "tool_identifier":"collaboration.spawn_agent",
    "tool_name":"创建子智能体",
    "dispatch_text":"背景：既有问题需要深入调查；目标：确认根因并提供证据；约束：只读获派模块，不改兄弟节点或父计划；路径：派发时列明相关模块；验收：报告根因、证据及剩余风险，并向获派节点提交结果。",
    "fork_turns":"none",
    "model":"<实际模型>",
    "reasoning_effort":"medium",
    "batch":"batch-1"
  }
}
```

将 task/instance/attempt ID 和范围交给真实 `collaboration.spawn_agent`；返回实际 ID 后调用 `bind_task_agent`：

<!-- delegation-example:bind -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "attempt_id":"<当前尝试 ID>",
  "agent_name":"<实际返回的子智能体标识>"
}
```

绑定相同 ID 可安全重试，不同 ID 或过期 attempt 会被拒绝；已知实际 agent ID 的 follow-up 可在启动时绑定。新 attempt 未绑定前不能提交 finished；spawn 失败则用当前 attempt 上报 `failed`/`cancelled` 并记录原因，不能伪造 ID 或重开虚假 attempt 来补字段。旧 in-flight 无绑定记录可兼容收尾，但显示 legacy/unbound，提交后仍须复核。

主会话直接执行叶任务时在计划和启动中给出 `self_execution_reason`，例如 `{"execution_mode":"main_session","complexity":"simple","self_execution_reason":"仅需调整一处已确认文案，交接成本高于该短小操作"}`。模型等未知字段省略。每个 attempt 的 assignment 不可变，后续更新不以改写 assignment 来补绑定或接管。

改变 pending 叶任务分工，调用 `reassign_task`，例如：

<!-- delegation-example:reassign -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"assets",
  "assignment":{"execution_mode":"main_session","complexity":"simple","self_execution_reason":"素材已整理完毕，仅需主会话确认一个本地路径，交接成本高于该短小操作"},
  "reason":"范围缩小为确认一个本地路径，由主会话显式接管短小操作",
  "reviewer":{"execution_mode":"main_session"}
}
```

### 3. 提交、复核与综合验收

子智能体用 `update_task` 提交其获派叶节点的结果（主会话也可代转原始报告，但不能省略独立复核）：

<!-- delegation-example:submit -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "attempt_id":"<当前尝试 ID>",
  "execution_status":"finished",
  "outcome":"resolved",
  "summary":"已确认根因，提交报告供主会话复核。",
  "evidence":["<文件路径、报告或可复核的结果链接>"]
}
```

此调用保存原始 `submission`，节点为 `execution_status=finished`、`outcome=null`、`review_status=pending`，显示 `awaiting_review`。执行结束计数可以增加，已解决计数和依赖不得提前放行。不能用通用 `update_task` 覆盖待验收提交、直接接受或跳过 review 重试。

主会话对照范围、文件、验证及风险复核后调用 `review_task`：

<!-- delegation-example:review -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "attempt_id":"<当前尝试 ID>",
  "decision":"accept",
  "summary":"已复核报告与证据，接受本次调查结果。",
  "evidence":["<主会话复核的证据或验证结果>"],
  "reviewer":{"execution_mode":"main_session"}
}
```

`decision=accept` 采用提交的原始 outcome，不能把 open 升级成 resolved；`reject` 保存退回理由和证据，节点为 `finished/open`，将具体修改反馈给原 agent，再依计划重试或重规划。两种决策都须非空 summary/evidence，绑定当前待复核 attempt；旧 attempt 或冲突的重复复核被拒绝。review 另存，不能覆盖执行配置、submission 或派发回执。

继续执行时对照 `next_actions` 和实际状态：ready 的 subagent 叶任务由主会话派发，ready 的 main_session 叶任务按自执行原因处理；pending dispatch 先绑定真实 ID，pending review 先复核，running 则等待或记录有意义进展。ready 列表为空且分组已可验收时提交综合验收，不反复尝试启动空列表；完成状态仍须核对根结果及剩余 open 项。

`pending` 是新节点初始状态，`update_task` 不接受 pending。叶任务必须先启动再完成；只有 `finished` 须传 `outcome=resolved|open`。主会话自执行叶任务可直接结束，但要保留自执行原因。失败/取消也记录 summary/evidence。结果复核后再次 `get_ready_tasks`，将未解决前驱的证据传给能够处理它的后继。

分组/root 不直接 running，只能由主会话综合验收。所有直接子任务必须已解决且无待复核项，才可提交 `finished/resolved`；需要非空 summary/evidence 和 `assignment.execution_mode=main_session`，模型未知可省略。服务端另建 `kind=synthesis` attempt；叶节点结束百分比 100% 不代表整体完成。

存在 open/failed/cancelled 子结果时，先处理、重试或重规划；也可在子任务均结束后如实以 `finished/open`、`failed` 或 `cancelled` 收尾，并说明证据和剩余工作，不能把未解决子结果静默视为 resolved。取消/失败保存 `kind=group_termination`，不会停止外部执行者，须先协调停止。没有运行 attempt 的分组不传旧的 attempt ID。旧的已完成/已归档记录保持可读，不虚构历史验收。

### 4. 重试与新一轮规划

- 重试已结束叶任务：先处理待复核提交，再用 `update_task` 传 `execution_status="running", retry=true` 及新的完整 assignment，执行角色须与保留的计划一致，保存新 `attempt_id` 并完成新的派发绑定；旧 submission/review 留在历史，当前 review 清空。旧尝试的迟到上报不能覆盖当前尝试。
- 每次重试都重新检查自身及祖先的依赖。如果有运行中的直接或传递下游仍依赖本次重试将使其失效的结果，先协调结束这些尝试，不能让旧下游与新上游并行。成功重试会使已结束的消费者（含依赖分组的后代）及其祖先验收失效，保留历史结果和 attempts，并恢复为 pending、标记 invalidated_by。再次读取 ready 列表，按依赖顺序重新执行或验收；旧 attempt_id 不再能更新这些节点。这里的状态失效不表示已实际派发重跑。
- 需要调整任务网络：先核实并结束所有 running 尝试，再调用 `rebuild_task_network({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","reason":"<重规划原因>"})`。
- 重建会将当前计划轮次保存到当前任务的 `history`，递增 `epoch` 并返回新的 `root_id`；`task_instance_id` 不变，这仍是同一个任务。必须使用实际返回的 `root_id`，不要推算其名称。用当前任务实例尚未使用过的任务 ID 重新分解；同一 `task_instance_id` 的重规划轮次不能复用节点 ID。不要继续使用旧轮次的 root/task ID。
- 派发及协调时将 `session_id`、`task_instance_id`、`epoch`、`task_id`、`attempt_id` 与实际智能体对应保存；收到旧任务实例、旧 epoch 或旧 attempt 的结果时拒绝更新当前任务。历史结果可作为附有来源的参考证据，不能直接冒充新一轮验收结果。

## 旧版顺序步骤 API

`init_progress`、`update_step`、`get_progress`、`render_progress`、`clear_progress` 保持兼容，仅供用户明确要求线性步骤或恢复旧版线性任务。线性清单必须显式确认例外：

```json
{"session_id":"<本会话 ID>","task_title":"简短任务标题","legacy_linear":true,"steps":["步骤一","步骤二","步骤三"]}
```

将以上对象传给 `init_progress`，保存响应的 `session_id` 和 `task_instance_id`。首次初始化尚无会话 ID 时可省略 `session_id`；已有 ID 时必须复用。重复调用会归档当前任务并创建新任务；恢复旧版清单只调用 `get_progress`。

开始主会话步骤时至少传 `assignment: {"execution_mode":"main_session"}`；只有当前上下文明确提供的模型、思维强度、thread ID 和 turn ID 才附加，不能从初始化快照或旧任务复制。子智能体步骤将 `execution_mode` 设为 `subagent`，并记录该次派发明确指定的 `agent_name`、`fork_turns`、`model` 和 `reasoning_effort`。未知的主会话元数据不阻止步骤启动。

开始步骤后，保存响应中该步骤的 `attempt_id`；运行中的备注更新、完成、重置和重开都须传回当前 ID。完成步骤：`update_step({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","step_index":0,"attempt_id":"<当前尝试 ID>","status":"completed","note":"验收结果"})`；执行元数据会保留，无需重复传入。

重置为 `pending` 会结束当前尝试并保留历史；下一次启动返回新的 `attempt_id`，旧 ID 不得继续使用。重开已完成步骤时先传其当前 ID，再保存启动响应中的新 ID。旧存储中尚无 ID 的既有尝试允许兼容收尾或重置，之后的新尝试严格校验 ID。

`step_index` 从 0 开始。旧版顺序模式先完成当前步骤再启动下一步，最多一个步骤 in_progress。新任务默认使用 HTN；不要对网络状态调用 `update_step`，也不要对旧版清单调用网络更新工具。

顺序模式必须先将步骤设为 `in_progress` 才能 `completed`。重开已完成的前序步骤时，若后续仍有已开始或已完成步骤，先实际协调执行者，再从后往前把后续步骤恢复为 pending。服务端会拒绝跳过执行或直接重开前序步骤而保留后续旧结果。

## 展示、恢复与清理

`init_task_network` 与 `init_progress` 只创建或更新任务数据，不触发展示。主会话首次建立多步骤任务后，紧接着调用一次 `render_progress`；完整面板默认显示在会话内。同一会话的新任务复用原会话 ID；已打开的视图通过轮询读取最新任务。只有用户将面板切换到 fullscreen 标签页时，会话内卡片才隐藏；标签页关闭后卡片恢复。普通状态更新、读取和后台轮询不得调用展示入口。恢复任务时按需要先 `get_progress` 读取状态。子智能体若在主会话任务树中执行，只更新获派节点，不调用展示入口，不另开主会话面板。纯文本客户端没有交互面板时，继续使用工具返回的文本摘要。不要手工拼接假进度，或把任务卡片描述成日志面板。

- `session_id` 是 Codex 会话的数据边界；所有读取、更新、渲染、重规划和清理操作显式传入它。本会话内多个任务复用同一个 ID，以 `task_instance_id` 区分任务实例。
- 每个 Codex 会话使用自己生成并保存的 ID，不能复制、猜测或读取其他会话的 ID。其他会话的任务不得阻止本会话初始化。
- 恢复先调用 `get_progress`，读取当前任务及其 `task_instance_id`；只有明确开始新任务才调用初始化工具。若已知 ID 的本会话数据不存在，说明实际情况，确需开始新任务时仍使用该会话 ID 初始化；首次没有已知 ID 时让初始化工具生成并保存。
- `init_task_network` 的同一次初始化重试可复用稳定的 `request_id`；首次省略 `session_id` 时使用全局唯一键（建议 UUID），收到会话 ID 后可带该 ID 重试。同一任务重规划后重试返回当前计划；绑定任务已归档或清除时旧键会被拒绝，不能用它恢复旧任务或覆盖当前任务。清理任务内容仍保留请求哈希和身份墓碑，以防迟到请求重建已清除任务。
- fullscreen 标签页每 10 秒续租；关闭时释放登记，宿主未发 teardown 时租约 35 秒后过期。会话内卡片在标签页打开期间隐藏，关闭后恢复；普通更新只改数据，不应为了刷新卡片重复调用 `render_progress`。不要用清理进度来关闭或重开面板。
- 任务数据按插件 `session_id` 隔离。当前公开 MCP Apps 上下文未定义“当前会话是历史/归档会话”的状态标志，因此不要声称插件能主动隐藏已经保存的历史会话卡片；若宿主后续提供该标志，应再增加历史会话抑制。
- `task_history` 保存本会话内已归档的旧任务；HTN 的 `history` 保存同一任务重规划产生的旧计划轮次。初始化创建新 `task_instance_id` 并归档旧任务；`rebuild_task_network` 保持任务实例不变，只递增 `epoch`。两种历史不能混写，也不能用重规划代替开始新任务。
- 一旦本会话存在历史任务，当前任务的写操作必须传回当前 `task_instance_id`，避免旧任务的迟到更新写入新任务。建议每次当前任务写操作都传入，包括 `decompose_task`、`update_task`、`bind_task_agent`、`review_task`、`reassign_task`、`rebuild_task_network` 和 `update_step`。`clear_progress` 仅接收 `session_id`，不支持 `task_instance_id`，其语义是删除本会话所有进度；清空后首次及后续新任务的写操作都必须传当前 `task_instance_id`，以拒绝清空前不带实例 ID 的迟到请求。初始化工具负责创建新实例，保存其新返回的 ID；后续写操作不得继续使用已归档实例 ID。`attempt_id` 和 `epoch` 仍分别标识当前任务中的尝试与计划轮次，不能代替 `task_instance_id`。
- 完成后读取并确认最终状态，在最终回复说明结果及未解决项。任务完成、开始新任务或关闭视图都不触发清理；未归档 Codex 会话的当前任务、历史、计划与证据必须保留。如需继续新任务，直接用同一 `session_id` 初始化。
- `clear_progress` 仅用于已归档 Codex 会话的全量数据清理。当前插件没有 Codex 归档事件或归档状态与插件 `session_id` 的可信映射，不能自行确认某个会话已归档，也不会自动响应归档。仅有插件 ID、任务完成状态、`task_history` 或用户提出删除请求，都不构成已归档依据。用户明确要求清理时，按 [cleanup-task-progress-history](../cleanup-task-progress-history/SKILL.md) 核对用户或宿主提供的明确归档依据、目标插件 ID、保留期限与删除范围；归档状态或对应关系无法确认就不调用清理工具。
- `clear_progress({"session_id":"<已确认归档会话的插件 ID>"})` 会清除该 ID 下的当前任务与 `task_history`，不支持仅删除某个历史任务。不要迁移或删除旧版全局状态，也不要扩大到其他未明确指定且未确认归档的会话。

若环境提供 `set_thread_title`，可在重要阶段更新标题；不要为标题频繁调用工具。
