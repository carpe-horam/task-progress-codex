### 2. 获取 ready 任务并实际派发

实际调用 `collaboration.spawn_agent` 前，先将任务挂到父节点、分配 `subagent`，再调用 `get_ready_tasks`；只启动 ready 叶节点。复杂工作即使只能串行仍委派，同一依赖链可顺序交给同一 agent；共享文件明确 owner 并串行交接。Task Progress 不会自动创建子智能体。

**顺序是 start → spawn → bind。**派发前展示协作表格，先用 `update_task` 开始 attempt，保存回执 `structuredContent.attempt_id`，再调用真实协作工具。新 subagent attempt 要有非空 `tool_identifier`、完整 `dispatch_text`、模型及思考强度。agent ID 尚未返回时省略 `agent_name`，不得填虚构占位 ID；不可变 assignment 与可补充的 dispatch 绑定分开保存。

如果宿主只允许继承默认值，而模型或强度未被明确提供，插件的必填字段无法如实填写：不要启动该 subagent attempt，也不要派发后补造配置。若宿主及任务规则允许主会话执行，由主会话先用 `reassign_task` 显式改派 pending 叶节点，记录具体限制和自执行理由，再按主会话流程启动；不允许改派或无合适执行者时保留未执行状态，说明限制。不要把继承默认值猜成已知配置，也不要把改派当成额外授权。

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
