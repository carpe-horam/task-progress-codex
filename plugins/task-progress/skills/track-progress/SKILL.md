---
name: track-progress
description: 对包含两个或以上可辨识步骤的任务使用 Task Progress v3，记录任务分解、依赖调度、执行分发、验收结果和会话历史。
---

# Track Progress

## 适用范围

多步骤任务默认使用当前 v3 API。单步查询和简单操作无需创建任务。一个 Codex 会话复用同一个 `session_id`；会话内的独立任务使用不同的 `task_instance_id`。本 skill 只描述当前 v3 插件接口。

## 会话与任务生命周期

- 首次创建任务调用 `task_open(mode: "new")`，传入目标、需求、验收条件和 UUID `request_id`。工具首次生成 `session_id` 后，将它保存在当前 Codex 会话的上下文中。
- 恢复时调用 `task_open(mode: "ensure")` 或 `task_open(mode: "resume")`，使用已保存的 `session_id`。恢复不得新建任务或改写目标。
- 同一会话开始另一个独立任务时，复用 `session_id` 并调用 `task_open(mode: "new")`。这会归档旧任务实例并创建新 `task_instance_id`；历史仍保留在该会话。
- 同一任务调整方案时使用 `task_decide(kind: "rebuild")`，递增 `epoch` 并保留旧计划；不要创建新任务实例。
- 普通任务完成、开始新任务或恢复会话时，不调用 `clear_progress`。清理范围是整个会话；仅在整个 Codex 会话进入归档流程，或用户明确要求删除该会话进度时调用。插件没有独立的会话归档事件回调，不要假设任务归档等于会话归档。

## 规划、调度与执行

1. 用最新的 `scope` 调用 `task_read` 获取摘要、节点、上下文或事件。`scope` 包含 `session_id`、当前 `task_instance_id` 和 `epoch`。
2. 调用 `task_advance(mode: "preview")` 查看可执行动作和阻塞原因。需要提交动作时，用新的 UUID `request_id` 和最新 `expected_revision` 调用 `task_advance(mode: "claim")`。提交后续决策或报告时，携带认领返回的 `action_id`、`lease_token` 和当前 scope；只有成功认领的动作才能提交决策。
3. 对 `PLAN` 动作，调用 `task_decide` 提交 `plan.execute` 或 `plan.decompose`。分解需覆盖目标需求、说明每个子任务的目标和验收条件，并定义真实依赖；只有具备独立执行价值的工作才拆成子任务。
4. 对 `ORCHESTRATE` 动作，检查节点目标、前置结果、验收条件、资源与剩余工作，再决定主会话执行、派发子智能体、等待或完成验收。主会话执行通过 `task_report(event_kind: "main_session_transition")` 登记实际执行身份和观察；不要把同一个工作重复登记成多个节点或 assignment。
5. 子智能体派发由宿主实际执行。`task_decide(kind: "orchestrate.dispatch")` 只登记包含完整指令、交付物和执行配置的 assignment，不代表已调用 agent。派发前记录 `task_report(event_kind: "dispatch_begin")`，实际调用后按事实记录 `dispatch_confirmed` 或 `dispatch_failed`。结果未知时记录未知并先对账，不盲目重复派发。
6. 根据实际执行记录 `worker_report`、`execution_observed` 或 `reconcile_result`。终态报告齐备后处理 `COLLECT`；父级的子任务结束后处理 `SYNTHESIZE`。验收时用 `task_decide(kind: "orchestrate.finish")` 记录结果、逐项需求结论、产出、未解决项和证据引用。

每个写操作使用新的 UUID `request_id`；同一 ID 仅用于完全相同的重试请求。写操作携带当前 scope 和 `expected_revision`。遇到 `SCOPE_STALE`、`REVISION_CONFLICT` 或 `LEASE_STALE`，先重新读取并按最新状态判断，不机械重放旧决策。

## 执行身份与协作

- 任务启动和 assignment 中的模型、思考强度、执行模式及执行者必须来自当前运行上下文或实际派发参数。不要从历史任务、其他会话或子智能体设置推断主会话配置；无法确认的可选初始化字段省略，不伪造值。
- 需要子智能体时，主会话先规划并创建对应节点，再调用宿主协作工具。派发说明须包含目标、约束、相关上下文、交付物和验收条件；实际返回的执行者身份写入对应 assignment。
- 插件负责保存计划、派发事实、执行观察与验收记录；实际创建 agent、运行工作和核验结果由宿主完成。记录成功不等于执行成功。
- 分组节点由子任务完成，不能直接当作 worker 执行。只有全部必要子任务及证据通过综合验收，根任务才能标记为已解决。

## 控制、历史与结果

- 用户通过对话提出暂停、继续或停止后，由宿主用 `task_control` 执行对应操作。卡片只展示已确认的控制状态；停止请求不等于执行已经终止。
- 用户补充信息时，按当前输入请求及版本提交 `answer_input`。过期问题先重新读取，不套用旧答复。
- `task_history(type: "task")` 查询当前会话中其他已归档任务；`type: "epoch"` 查询指定 `task_instance_id` 的旧计划；`type: "attempt"` 查询指定任务节点的执行尝试。历史只读，浏览历史不切换当前任务。
- 每项工作的结果、产出、检查、未解决项和证据归属对应任务节点；根节点保存整体验收结论。不另建重复的全局“任务结果”。

## 进度展示

需要展示或恢复任务时读取当前 v3 状态并使用插件卡片。进度百分比表示当前计划中已结束任务项的比例，不代表成功率或时间进度。未知执行状态须明确显示并先对账；刷新页面不等于执行状态已更新。只在状态有实质变化时向用户报告。
