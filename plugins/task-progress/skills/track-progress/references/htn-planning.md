### 1. 初始化与分解

首次建立任务，或原任务已收尾且没有待处理独立请求时，调用 `init_task_network`；本会话已有 ID 则传入 `session_id`，同时传 `task_title`，保存响应 `structuredContent` 顶层的 `session_id`、`task_instance_id`、`epoch` 和 `root_id`。只附加当前上下文明确提供的 `main_session` 字段；未知字段省略。处理中收到独立请求改用 `route_request`，队列项用 `activate_request`；两者都复用本会话 ID。用实际返回的根 ID 分解；以下以首轮 `root` 为例：

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
