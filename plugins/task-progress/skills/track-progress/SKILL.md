---
name: track-progress
description: 对 Codex 主会话与 agent 中包含两个或以上可辨识步骤的任务默认自动跟踪进度，无需 AGENTS.md/agent.md 额外强制。支持按会话隔离的 HTN 任务网络、依赖、并行、结果与证据记录。
---

# Track Progress — 任务进度跟踪与展示

## 何时使用与模式选择

### 自动调用（Codex 主会话与 agent 均适用）

当任务包含 **2 个或以上** 可辨识步骤时，自动启用本 skill 并调用 Task Progress；这是默认工作规则，**不要求用户点名，也不依赖项目中存在 `AGENTS.md`/`agent.md` 或其中写有强制条款**。开始实质执行前初始化/恢复进度记录，不要因为项目规则缺失而跳过。此自动调用规则覆盖主会话和 agent。

若 agent 收到的工作已由主会话的 Task Progress 任务树表示，沿用派发信息中提供的任务节点与尝试标识更新该节点，不另建重复根任务；若 agent 独立承担多步骤工作且没有可更新的父任务节点，则在自己的会话中初始化并维护进度。单步查询或简单操作无需初始化。

多步骤新任务默认使用 HTN 任务网络：`init_task_network` → `decompose_task` → `get_ready_tasks` → `update_task`。不得因任务看起来简单、顺序执行或不需要并行而改用线性 API。`init_progress` 对两个及以上步骤默认会被服务端拒绝；只有用户明确要求线性步骤时才可传 `legacy_linear=true`。

- 任务有子任务分解、依赖、并行、委派或重规划时，继续使用 HTN 任务网络表达结构与状态。
- 仅当用户明确要求线性步骤，或恢复已有旧版线性任务时，使用 `init_progress` → `update_step`；恢复时先 `get_progress`，再按原数据模式继续，不要求把旧状态迁移成网络。需要按步骤显示执行者、模型和思考强度时，在启动步骤的 `update_step` 调用中附上 `assignment`。
- **10 分钟拆分复核点：**任一仍在执行的 HTN 叶节点或线性步骤超过 **10 分钟**时，主会话必须明确评估“是否应拆成更细的任务展示”，不能只更新耗时而跳过评估。检查其中是否已有可独立执行、并行推进、存在依赖边界或可单独验收的阶段：有则把未完成工作拆成子任务并记录依赖；若当前仍是线性步骤且任务已适合分层展示，则按线性转 HTN 的规则重规划。评估后若没有自然任务边界，或细分不会改善执行与进度可见性，就保留原粒度，并在进度摘要中记下“已评估，无需拆分”及原因。**超过 10 分钟是强制评估点，不是自动拆分条件**；任务范围后来变复杂时，即使尚未达到 10 分钟也应重新评估。
- 线性任务转 HTN 时，不要在同一任务实例混用 `update_step` 与 `update_task`。在同一 `session_id` 下初始化 HTN 网络会把旧线性任务归档为历史；新网络沿用原目标，摘要已完成工作并保留其证据，只将未完成工作分解为新节点，避免把已完成部分伪装成待重做。若不值得转换，就继续原线性任务。
- 每个 Codex 会话复用一个 `session_id`。首次初始化可让工具生成并返回 ID；保存它，之后本会话的新任务必须显式传入同一个 ID，不为每个任务生成新 ID。插件按 `session_id` 隔离历史，换成新 ID 就会新建另一套任务记录，旧任务不会出现在新卡片的历史中。
- 恢复当前任务时调用 `get_progress({"session_id":"<本会话 ID>"})`，根据返回的数据模式继续执行，并保存当前 `task_instance_id`；不要调用初始化工具来恢复。
- 开始新任务时默认调用 `init_task_network`。在同一 `session_id` 下重复初始化会先归档当前任务，再创建新任务并返回新的 `task_instance_id`；最新卡片显示新任务，展开“历史任务”可查看旧任务，先前的卡片会折叠。不要为新任务再生成 `session_id`，也不要用 `rebuild_task_network` 代替新任务初始化。
- 若环境提供内置计划工具，可同步维护可检查清单；它不能替代插件中的实际状态。

## 协作原则

开始前明确目标、交付物、约束和验收条件，将工作拆成可检查任务，标明父子关系、依赖和可并行项。独立任务可并行，有依赖的任务依次推进；主会话负责拆分、派发、协调、验收和维护进度。

每批创建子智能体前，先展示表格，包含：工具标识与中文名称、实际派发的完整任务文本、`fork_turns`、模型和思考强度。统一设置 `fork_turns="none"`，按难度与风险选择当前可用的模型及思考强度。派发文本必须自包含，写清背景、目标、约束、必要路径和验收条件；不要依赖主会话上下文。并行修改须先明确文件归属，并优先复用已有工作区，避免相互覆盖。

插件保存任务与派发记录、计算可执行状态并展示进度；实际创建、联系和等待子智能体由主会话调用环境提供的协作工具完成。记录 assignment 不代表已派发。只在关键状态变化时更新用户可见进度，避免重复播报。

每个执行尝试启动时，必须在 `assignment` 中记录 `execution_mode`（`main_session` 或 `subagent`）、实际模型 `model` 和思考强度 `reasoning_effort`；插件会拒绝缺少这些字段的启动更新。主会话直接执行时标为 `main_session`；委派执行时标为 `subagent`。

#### 主会话模型与思考强度的传入约束（必须遵守）

Task Progress **不会自动识别** Codex 当前使用的模型或思考强度；`main_session` 和 `assignment` 都是调用方写入的记录字段。调用前必须按以下规则核实，不能凭印象填值：

1. **只读取当前会话此刻的有效配置**：优先使用当前运行环境明确提供的模型标识与思考强度；也可使用用户明确确认适用于当前会话的值。只有确认当前会话没有覆盖时，才可使用已核实生效的本机 Codex 默认配置。
2. **禁止从旁路推断或复制**：不得把 `list_threads`、`read_thread`、其他会话、历史任务/attempt、Task Progress 旧卡片、子智能体配置、模型目录、模型名称相似性、回答风格或“常用默认值”当作当前主会话配置来源。不得把模型别名改写成另一个模型 ID，也不得猜测思考强度。
3. **初始化时两项成对传入**：确认模型和思考强度后，才在 `init_task_network` 顶层传 `main_session: {model, reasoning_effort}`，并逐字使用核实到的值。如果任一项未知，就省略整个 `main_session` 对象（不要填占位符、猜测值或只传一项）；界面显示“未记录”是正确结果，不影响创建任务网络。
4. **启动执行时重新核实执行者配置**：将任务/步骤设为 `running` 时，`assignment.model` 与 `assignment.reasoning_effort` 必须对应实际执行者在该次执行开始时使用的配置。主会话执行就记录当前主会话；子智能体执行就记录该子智能体实际使用的配置，不能拿主会话参数代填。若主会话的任一值无法确认，先不要把节点标为运行中；说明缺少的字段并请求准确值。若会话中途切换了模型或思考强度，后续新 attempt 按新配置记录，不回写或沿用旧值。

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

若任一值未知，初始化参数中只保留 `session_id` 与 `task_title`，完全省略 `main_session`。不得照抄文档示例或其他会话的值。主会话综合验收父任务时，也须在该次 `assignment` 中记录实际配置。

## HTN 操作顺序

### 1. 初始化与分解

本会话已有 ID 时，调用 `init_task_network` 并传入 `session_id` 与 `task_title`，保存响应的 `session_id`、`task_instance_id`、`epoch` 和 `root_id`。只有按“主会话模型与思考强度的传入约束”核实出当前会话的两项实值后，才附加顶层 `main_session`；任一未知就省略整个对象。首次初始化尚无 `session_id` 时可省略该参数；后续开始新任务复用同一 ID，并保存新返回的 `task_instance_id`。用返回的根 ID 分解；以下以首轮 `root` 为例：

```json
{
  "session_id": "<本会话 ID>",
  "task_instance_id": "<当前任务实例 ID>",
  "task_id": "root",
  "tasks": [
    {"id":"inspect","title":"确认根因"},
    {"id":"assets","title":"整理独立素材"},
    {"id":"fix","title":"实施修复","depends_on":["inspect"]},
    {"id":"accept","title":"验收结果","depends_on":["fix","assets"]}
  ]
}
```

将以上对象传给 `decompose_task`。可继续分解未结束的叶任务形成层级；也可对尚未结束的同一父任务重复调用 `decompose_task`，把新一批子任务追加到现有 children。每个实际委派的子智能体都必须有自己的子任务节点，不能只在消息或根任务备注中记录。父任务进入终态后不能再追加子任务；计划结构需要重做时使用重规划。任务 ID 必须由 1–64 位字母、数字、连字符或下划线组成，并在当前 `task_instance_id` 的生命周期内唯一，同一任务实例的不同重规划轮次也不可复用；新任务实例可重用已归档任务的节点 ID。所有依赖必须有效且无环。

`depends_on` 描述前驱。默认 `dependency_policy="resolved"`，仅当前驱 `execution_status="finished"` 且 `outcome="resolved"` 才放行。显式设置 `dependency_policy="finished"` 时，前驱结束即可放行，包括 `finished/open`、`failed` 和 `cancelled`；仅在下游能够处理未解决结果时使用。主会话须将前驱的摘要和证据传给下游，不能把放行当作问题已解决。

### 2. 获取 ready 任务并实际派发

每次调用实际的 `collaboration.spawn_agent` 前，先调用 `decompose_task` 将该子智能体的任务挂到对应父任务下；在子任务对象中写入其自身 assignment。然后调用 `get_ready_tasks({"session_id":"<本会话 ID>"})`，从返回的 ready 叶任务选择本批任务。同批独立任务可并行；尚未 ready 的任务不能启动。Task Progress 只保存树和 assignment，不会自动创建或调用子智能体。

派发前按协作原则展示表格。先用 `update_task` 开始对应子任务并记录派发信息、保存 `attempt_id`，再调用实际协作工具；这样派发失败也能在对应树节点如实记录。每次新 attempt 的 assignment 是不可变快照；同一 attempt 后续更新不要传入不同 assignment。重试时必须传入新的完整 assignment，插件会用它替换旧 attempt 的字段。示例：

```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "execution_status":"running",
  "assignment":{
    "execution_mode":"subagent",
    "tool_identifier":"collaboration.spawn_agent",
    "tool_name":"创建子智能体",
    "dispatch_text":"背景：……；目标：……；约束：……；路径：……；验收条件：……",
    "fork_turns":"none",
    "model":"<实际模型>",
    "reasoning_effort":"medium",
    "agent_name":"<实际返回的子智能体标识>",
    "batch":"batch-1"
  }
}
```

主会话直接执行时使用同一字段明确标记执行者，例如 `"assignment":{"execution_mode":"main_session","model":"<当前会话有效模型>","reasoning_effort":"<当前会话有效思考强度>"}`。不要只传 `execution_mode`；启动更新缺任一字段都会被拒绝。

保存响应中该节点的新 `attempt_id`。执行中的后续更新和结束上报必须传入这个 ID。派发失败也应如实记录失败与证据，不得把记录成功视为实际执行成功。

### 3. 验收与记录结果

主会话验收子智能体返回内容，再调用 `update_task`：

```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "attempt_id":"<当前尝试 ID>",
  "execution_status":"finished",
  "outcome":"resolved",
  "summary":"已确认根因，满足本任务验收条件。",
  "evidence":["<文件路径、报告或可复核的结果链接>"]
}
```

`pending` 仅是新节点的初始状态，`update_task` 不接受 `pending`。更新时 `execution_status` 可为 `running`、`finished`、`failed`、`cancelled`。只有 `finished` 使用且必须明确给出 `outcome`：`resolved` 表示验收通过，`open` 表示执行结束但问题仍未解决。失败或取消时同样记录 `summary` 和 `evidence`。不要仅因智能体结束或命令退出就标为 resolved。

结果更新后再次 `get_ready_tasks` 推进后继。`finished` 策略放行的未解决前驱会在响应中携带摘要和证据；将这些内容纳入实际派发文本。

分组任务由子任务执行，不能直接标 running。其直接子任务都结束后，主会话才可提交父任务的综合验收结果；逐层验收到根任务。叶任务结束百分比达到 100% 只表示执行已结束，仍须检查 open、failed、cancelled 和根任务验收结果。

### 4. 重试与新一轮规划

- 重试已结束叶任务：`update_task` 传 `execution_status="running", retry=true`，保存新的 `attempt_id`；旧尝试的迟到上报不能覆盖当前尝试。
- 需要调整任务网络：先核实并结束所有 running 尝试，再调用 `rebuild_task_network({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","reason":"<重规划原因>"})`。
- 重建会将当前计划轮次保存到当前任务的 `history`，递增 `epoch` 并返回新的 `root_id`；`task_instance_id` 不变，这仍是同一个任务。用当前任务实例尚未使用过的任务 ID 重新分解；同一 `task_instance_id` 的重规划轮次不能复用节点 ID。不要继续使用旧轮次的 root/task ID。
- 派发及协调时将 `session_id`、`task_instance_id`、`epoch`、`task_id`、`attempt_id` 与实际智能体对应保存；收到旧任务实例、旧 epoch 或旧 attempt 的结果时拒绝更新当前任务。历史结果可作为附有来源的参考证据，不能直接冒充新一轮验收结果。

## 旧版顺序步骤 API

`init_progress`、`update_step`、`get_progress`、`render_progress`、`clear_progress` 保持兼容，仅供用户明确要求线性步骤或恢复旧版线性任务。线性清单必须显式确认例外：

```json
{"session_id":"<本会话 ID>","task_title":"简短任务标题","legacy_linear":true,"steps":["步骤一","步骤二","步骤三"]}
```

将以上对象传给 `init_progress`，保存响应的 `session_id` 和 `task_instance_id`。首次初始化尚无会话 ID 时可省略 `session_id`；已有 ID 时必须复用。重复调用会归档当前任务并创建新任务；恢复旧版清单只调用 `get_progress`。

开始主会话步骤并记录该次执行的真实模型与思考强度：`update_step({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","step_index":0,"status":"in_progress","assignment":{"execution_mode":"main_session","model":"<已核实的当前会话实际模型>","reasoning_effort":"<已核实的当前会话实际思考强度>"}})`。这里的两个值按“主会话模型与思考强度的传入约束”核实，不能从初始化历史值或旧任务复制。子智能体步骤将 `execution_mode` 设为 `subagent`，并记录该次执行实际使用的 `agent_name`、`fork_turns`、`model` 和 `reasoning_effort`。启动必需字段缺失或未核实时，先获取真实值，不要启动进度状态。

完成步骤：`update_step({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","step_index":0,"status":"completed","note":"验收结果"})`；执行元数据会保留，无需重复传入。

`step_index` 从 0 开始。旧版顺序模式先完成当前步骤再启动下一步，最多一个步骤 in_progress。新任务默认使用 HTN；不要对网络状态调用 `update_step`，也不要对旧版清单调用网络更新工具。

## 展示、恢复与清理

`get_progress`、`render_progress` 及更新工具提供文本摘要；支持 MCP Apps 的客户端显示任务卡片。不要手工拼接假进度，或把任务卡片描述成日志面板。

- `session_id` 是 Codex 会话的数据边界；所有读取、更新、渲染、重规划和清理操作显式传入它。本会话内多个任务复用同一个 ID，以 `task_instance_id` 区分任务实例。
- 每个 Codex 会话使用自己生成并保存的 ID，不能复制、猜测或读取其他会话的 ID。其他会话的任务不得阻止本会话初始化。
- 恢复先调用 `get_progress`，读取当前任务及其 `task_instance_id`；只有明确开始新任务才调用初始化工具。若已知 ID 的本会话数据不存在，说明实际情况，确需开始新任务时仍使用该会话 ID 初始化；首次没有已知 ID 时让初始化工具生成并保存。
- `task_history` 保存本会话内已归档的旧任务；HTN 的 `history` 保存同一任务重规划产生的旧计划轮次。初始化创建新 `task_instance_id` 并归档旧任务；`rebuild_task_network` 保持任务实例不变，只递增 `epoch`。两种历史不能混写，也不能用重规划代替开始新任务。
- 一旦本会话存在历史任务，当前任务的写操作必须传回当前 `task_instance_id`，避免旧任务的迟到更新写入新任务。建议每次当前任务写操作都传入，包括 `decompose_task`、`update_task`、`rebuild_task_network` 和 `update_step`。`clear_progress` 仅接收 `session_id`，不支持 `task_instance_id`，其语义是删除本会话所有进度。初始化工具负责创建新实例，保存其新返回的 ID；后续写操作不得继续使用已归档实例 ID。`attempt_id` 和 `epoch` 仍分别标识当前任务中的尝试与计划轮次，不能代替 `task_instance_id`。
- 完成后读取并确认最终状态，在最终回复说明结果及未解决项。任务完成无需清理；只有明确要删除本会话全部进度时才调用 `clear_progress({"session_id":"<本会话 ID>"})`。如需保留历史并继续新任务，直接用同一 `session_id` 初始化。
- `clear_progress` 仅清除传入 `session_id` 的本会话数据，包括当前任务与 `task_history`；不要迁移或删除旧版全局状态，也不要清理其他会话。

若环境提供 `set_thread_title`，可在重要阶段更新标题；不要为标题频繁调用工具。
