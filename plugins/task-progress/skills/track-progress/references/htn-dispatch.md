### 2. 获取 ready 任务并实际派发

实际调用 `collaboration.spawn_agent` 前，先将任务挂到父节点、分配 `subagent`，再调用 `get_ready_tasks`；只启动 ready 叶节点。复杂工作即使只能串行仍委派，同一依赖链可顺序交给同一 agent；共享文件明确 owner 并串行交接。Task Progress 不会自动创建子智能体。

**顺序是 start → spawn/follow-up → bind。**派发前展示协作表格，先用 `update_task` 开始 attempt，保存回执 `structuredContent.attempt_id`，再调用真实协作工具。新 subagent attempt 要有非空 `tool_identifier` 和完整 `dispatch_text`。读取 overview 的 `protocol_capabilities`：服务端支持 `assignment_sources_v1` 时模型与推理强度各自可记为 explicit/inherited/unknown；继承或未知不带具体值。没有该能力时沿用旧的可行路径。agent ID 尚未返回时省略 `agent_name`，不得填虚构占位 ID；不可变 assignment 与 dispatch 绑定分开保存。

服务端返回 `assignment_sources_v1` 且宿主只允许继承默认值时，照实传 `model_source="inherited"` / `reasoning_effort_source="inherited"`，省略具体值并继续宿主支持的派发；未知值分别记 `unknown`。没有能力标志的旧服务端仍按旧流程处理，不能伪造配置或向旧服务端发送新字段。显式选择仍须与实际派发一致；若 host 明确拒绝，则如实收尾本次 attempt。

同一紧密工作链优先由同一个 worker 连续处理调查、实现和局部核验；不为阶段本身派发新 agent。新 spawn 需要 fresh 探索时明确 `fork_turns="none"`；仅当 spawn 工具确实接收完整继承上下文时才传 `all`。follow-up 复用原 owner/thread 的现有上下文；若该 host 工具未提供 `fork_turns` 字段，就省略而不推断。主会话用 `followup_task` 复用原 owner 并发起新的 attempt，配置和 attempt ID 重新记录，owner 身份保持相同。

`collaboration.send_message`、thread 消息等仅投递内容，不证明 idle worker 被唤醒。若 owner 仍在原 attempt 执行，消息可作为该 attempt 上下文，不启动新 attempt；若它已空闲且要继续执行，调用能唤醒/继续执行的 follow-up 工具。仅收到消息发送成功不能当作工作已开始或完成。

调用 `update_task`（启动示例）：

以下启动示例用于 overview 明确返回 `assignment_sources_v1` 与 `fork_context_v1` 的服务端。旧服务没有相应能力标志时，不发送 `model_source`、`reasoning_effort_source` 或 `fork_turns` 新字段，按旧服务接受的显式配置路径执行；不要猜测值，也不能因此跳过绑定、提交或主会话复核。

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

绑定相同 ID 可安全重试，不同 ID 或过期 attempt 会被拒绝；只有显式 follow-up 工具与已知实际 agent ID 的组合可在启动时绑定。新建 spawn 即使误填计划别名也仍待绑定；实际 spawn 的 fork、模型、思维强度必须与记录一致，不得把计划值当成宿主回执。新 attempt 未绑定前不能提交 finished；spawn 失败则用当前 attempt 上报 `failed`/`cancelled` 并记录原因，不能伪造 ID 或重开虚假 attempt 来补字段。旧 in-flight 无绑定记录可兼容收尾，但显示 legacy/unbound，提交后仍须复核。

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
