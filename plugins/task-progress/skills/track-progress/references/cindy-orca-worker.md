# Cindy Orca Worker 协同

Orca Worker 是持久、UI 可见的会话；宿主原生 subagent 是一次性执行体。Cindy Lead 用 Orca Worker 负责持续工作包，Worker 在其范围内按需创建原生 subagent。不能用 `create_worker` 冒充原生 spawn，也不能把 Orca ID 交给 `bind_task_agent`。仅需一次性子任务时直接走原生 subagent 流程。没有 Cindy Orca 工具时，不猜工具名或用进程模拟。

## 主账本归属

**Lead 当前 Task Progress 任务树是唯一主账本。**先按本技能第 1–2 节建立或恢复原任务，保留其 `session_id`、`task_instance_id`、阶段和依赖；创建 Worker 不重新初始化原任务。一个 Orca 协调批次对应一个 Lead 协调与验收叶节点，批次可以派给一个或多个 Worker。其计划和 attempt 设 `execution_mode=main_session`，`self_execution_reason` 写明 Lead 负责派发、接收回报和独立验收，实际实施由 Orca Worker 完成。该节点记录 Lead 的协调与验收工作，不声称 Lead 实施了 Worker 的产物。现有服务端一次只允许一个 running 的 `main_session` 叶节点；因此并行 Worker 应放进同一批次节点，各 Worker 的派发与结果分别写入该节点证据。批次内不能靠不同叶节点独立放行依赖；需要独立放行时先按依赖划成顺序批次。

Lead 在派发前启动 ready 叶 attempt，逐个保存工作包范围、真实 Orca `worker_id` / `session_id`、派发回执和状态。排队时注明尚未开始；未送达或失败时记录障碍，不把创建成功当执行成功。各 Worker 回报后由 Lead 分别检查改动、实际验证和剩余风险，所有批次结果核对后再更新该叶及父/root；只有整个批次验收通过才以 resolved 放行依赖。回报缺失或证据不完整时保持 running，或如实以 open 收尾。主账本不调用 `bind_task_agent` 绑定 Orca Worker。

Worker 可为**获派工作包**建立 Task Progress 辅助任务树，记录内部原生 subagent 的派发与复核；不能复制整个原任务 root。Task Progress `session_id` 必须来自该辅助树 `init_task_network` 的 UUID 回执，不能使用 Orca 的 `worker_id` 或业务 `session_id`。Worker 报告辅助树的真实 ID 与关键结果，Lead 将其作为跨会话证据引用；辅助账本不会自动更新主账本或放行依赖。

## Lead 建立与派发

1. 跨阶段、需要内部拆分或后续事件的工作包适合 Orca Worker。Lead 保留用户沟通、范围、授权、跨包依赖、文件 owner 和最终验收。先用 `mcp__cindy_orca__get_workspace_info` 确认 team、已有 Worker 和受支持设备，优先复用合适的 Worker。
2. 没有 team 时调用 `mcp__cindy_orca__start_team`。首次创建显式传符合授权的 `worker_permission_mode`；没有 Full access 授权时选 `auto`，因为省略且无保存偏好会默认 `bypassPermissions`。已是 Lead 时不为普通派活重复调用；要修改默认权限须按宿主要求显式传模式。Worker 内不能嵌套 team，也不能通过改档绕过审批。
3. 一个新 Worker 用 `mcp__cindy_orca__create_worker`；一次需要两个及以上新 Worker 必须用 `create_workers` 批量创建。每项明确唯一 `label`、`role` 和自包含的 `initial_task`。没有适用的手工创建组合、无法推断 Agent 时，先用 `list_available_models` 确认可用路线，再显式传 `agent`；显式 `model` / `provider_id` / `effort` 须匹配同一 Agent 的能力。按创建错误调整，不盲目重复请求。
4. `initial_task` 正文写明背景、目标、交付物、SDK/生产等硬约束、依赖、工作目录、文件归属、验证边界、证据和回报要求。共用当前 worktree 时显式传绝对 `working_dir`；省略只继承 Lead 启动时目录，不跟随之后的 `cd`。仅在项目允许独立检出时用 `new_worktree`，不嵌套创建。跨设备只选 `get_workspace_info` 标为 supported 的设备；远程设备读不到本机未提交文件。
5. 保存创建回执中的真实 `worker_id`、Orca `session_id`、`working_dir`、`file_access`、`initial_task_state`，逐项区分 `delivered`、`queued`、`not_delivered`、`none`。批量创建后按工具要求向用户逐字转告 `user_report`，补充每项成功、失败或跳过与首条任务状态；硬上限时同时说明回执里的建议。创建成功不等于任务已开始或完成。
6. 已有 Worker 的新工作用 `mcp__cindy_orca__send_to_worker`。忙碌时消息可能排队；回执有 `other_queued_messages` 时先判断是否需合并、改写或撤回自己未被消费的消息。普通追加用默认投递；当前 turn 所需信息可用 `delivery=steer`；只有必须替换当前未完成任务才用 `interrupt_worker`。错误状态的 Worker 可原地继续。Worker 完成会主动回报；`read_worker`、`worker_status` 仅用于紧急诊断，不作日常轮询。

## Worker 内部分工与回报

Worker 在获派范围内按 [协作与职责](delegation.md) 和 [HTN 派发](htn-dispatch.md) 拆分；复杂分析、实施、专门验证和独立评审用宿主原生 subagent。原生 attempt 按 `update_task` 启动 → 实际 spawn/follow-up → `bind_task_agent` 绑定真实 ID → Worker 独立复核的顺序记录。子智能体向 Worker 报告，不直接向 Lead 发最终报告。Worker 不更新 Lead 的兄弟节点或替 Lead 验收主账本。

Worker 只在最终结果或一个阻塞问题时调用 `mcp__orca_worker_bridge__send_to_lead`，每个 turn 一次，使用最新 Lead 消息中给出的真实 `worker_id`。合并内部结果，写清改动、实际验证、兼容证据、辅助任务树 ID 和剩余风险。发出阻塞问题后等待 Lead 的 `send_to_worker`；Lead 后续派活复用 Worker 的持久上下文。回报送达仍需 Lead 独立验收后才能更新主账本为 resolved。
