## 旧版顺序步骤 API

`init_progress`、`update_step`、`get_progress`、`render_progress`、`clear_progress` 保持兼容，仅供用户明确要求线性步骤或恢复旧版线性任务。线性清单必须显式确认例外：

```json
{"session_id":"<本会话 ID>","task_title":"简短任务标题","legacy_linear":true,"steps":["步骤一","步骤二","步骤三"]}
```

将以上对象传给 `init_progress`，保存响应 `structuredContent` 顶层的 `session_id` 和 `task_instance_id`。首次初始化尚无会话 ID 时可省略 `session_id`；已有 ID 时必须复用。重复调用会归档当前任务并创建新任务；恢复旧版清单只调用 `get_progress`。

开始主会话步骤时至少传 `assignment: {"execution_mode":"main_session"}`；只有当前上下文明确提供的模型、思维强度、thread ID 和 turn ID 才附加，不能从初始化快照或旧任务复制。子智能体步骤将 `execution_mode` 设为 `subagent`，并记录该次派发明确指定的 `agent_name`、`fork_turns`、`model` 和 `reasoning_effort`。未知的主会话元数据不阻止步骤启动。

开始步骤后，保存响应 `structuredContent` 顶层的 `attempt_id`（步骤索引为 `step_index`）；运行中的备注更新、完成、重置和重开都须传回当前 ID。完成步骤：`update_step({"session_id":"<本会话 ID>","task_instance_id":"<当前任务实例 ID>","step_index":0,"attempt_id":"<当前尝试 ID>","status":"completed","note":"验收结果"})`；执行元数据会保留，无需重复传入。

重置为 `pending` 会结束当前尝试并保留历史；下一次启动返回新的 `attempt_id`，旧 ID 不得继续使用。重开已完成步骤时先传其当前 ID，再保存启动响应中的新 ID。旧存储中尚无 ID 的既有尝试允许兼容收尾或重置，之后的新尝试严格校验 ID。

`step_index` 从 0 开始。旧版顺序模式先完成当前步骤再启动下一步，最多一个步骤 in_progress。新任务默认使用 HTN；不要对网络状态调用 `update_step`，也不要对旧版清单调用网络更新工具。

顺序模式必须先将步骤设为 `in_progress` 才能 `completed`。重开已完成的前序步骤时，若后续仍有已开始或已完成步骤，先实际协调执行者，再从后往前把后续步骤恢复为 pending。服务端会拒绝跳过执行或直接重开前序步骤而保留后续旧结果。
