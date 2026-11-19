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

任务树按独立结果、owner 或核验边界拆分，不按命令或阶段机械拆分。若调查、修复和局部验证紧密耦合，可用一个实现结果节点并交给同一 owner；worker 在派发范围内自行安排这些步骤。若要保留单独的阶段结果，后续阶段也可通过 follow-up 交给同一真实 agent ID，无需创建新 agent。只有独立产物、独立 owner 或高风险/难以直接核验的要求才增加节点。主会话仍负责目标与范围变更、资源冲突、依赖和最终验收；worker 发现新范围时先请求调整计划。

### 追加到合适的父任务

1. 先用 `get_progress(view="tree")` 找与新增工作共享交付或验收结果的分组。以最具体、未结束的分组 ID 作为 `decompose_task.task_id`。同阶段再次追加时沿用这个 ID。已有叶任务可以在符合分解条件时拆为两个或更多有独立结果的子任务。仅补充原任务细节时更新上下文即可。
2. 多项新增工作属于同一个新阶段时，在合适的上级下建一个分组。若 `get_progress(view="overview")` 的 `protocol_capabilities` 含 `task_batch_parent_v1`，可在一次 `decompose_task` 中提交新分组与子任务。每个子任务的 `parent_id` 引用同批新父任务的 ID。没有此能力时，先创建分组节点，再以该节点作为 `task_id` 提交子任务，两次调用都省略 `parent_id`。首次 `init_task_network` 也支持同批分层，前提是当前工具的任务 schema 声明了 `parent_id`。
3. 分组已结束时保留其执行和验收记录。确需继续时，在合适的未结束上级下建立有明确验收目标的补充分组，或在当前计划已收束时按同一目标重规划。已有平铺节点若尚未开始，且相关分支和消费者符合原位修订条件，可用 `revise_task_plan` 移进已有分组。

例如，把多项新验证聚合成一个“导出回归”分组。以下是支持 `task_batch_parent_v1` 时传给 `decompose_task` 的 `tasks`，顶层 `task_id` 填实际查到的上级 ID。后续发现同类验证时直接向 `export-checks` 追加。

```json
[
  {"id":"export-checks","title":"导出回归","addition_reason":"导出范围扩大，需要覆盖两种交付格式","assignment":{"execution_mode":"main_session"}},
  {"id":"export-html","parent_id":"export-checks","title":"浏览器中核对离线报告","addition_reason":"增加离线报告验收","assignment":{"execution_mode":"main_session","self_execution_reason":"由主会话核对生成的报告"}},
  {"id":"export-xlsx","parent_id":"export-checks","title":"核对工作簿中的任务层级","addition_reason":"增加工作簿验收","assignment":{"execution_mode":"main_session","self_execution_reason":"由主会话核对生成的工作簿"}}
]
```

分解 `running` 节点时，先协调旧执行者停止或移交工作，再在 `decompose_task` 中传入它的当前 `attempt_id`。插件会将该尝试以 `cancelled` 归档，保留配置、摘要、证据和时间；节点转为等待子任务的分组，原 `attempt_id` 不再有效。插件不会实际停止子智能体或进程。若分解被拒绝，原尝试保持不变。不要先把节点设为终态再调用分解，因为终态节点不能追加子任务。

`depends_on` 描述前驱。默认 `dependency_policy="resolved"`，仅前驱已接受且 `outcome="resolved"`（或主会话直接完成）才放行。子智能体提交后尚待 review 时，包括显式 `dependency_policy="finished"` 的下游也必须等待。`finished` 策略允许已复核的 `finished/open`、`failed`、`cancelled`，仅在下游能处理未解决结果时使用；主会话传递前驱摘要和证据，不能把放行当作已解决。

分组节点的依赖约束也适用于其全部后代，并沿用声明该依赖的节点的 `dependency_policy`；子节点自己的策略不能放宽祖先约束。ready 任务的 `dependencies` 包含继承的前驱结果与 `inherited_from`。不得让子任务依赖其祖先，或创建跨分组的完成等待环。
