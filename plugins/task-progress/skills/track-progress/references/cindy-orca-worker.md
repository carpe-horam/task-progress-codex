# Cindy Orca Worker 协同

Cindy Lead 获准委派时，一次性子任务和跨阶段工作包均使用 Orca Worker。先通过宿主的当前会话角色和 `mcp__cindy_orca__get_workspace_info` 核实上下文。有合适 Worker 就复用，没有则按下文创建。Orca Worker 在获派范围内可按授权使用宿主原生子智能体，非 Cindy 宿主继续使用原生派发流程。

如果 Cindy Lead 无法使用 Orca、权限不满足或没有可用容量，记录具体障碍，不能用原生 spawn、普通跨会话消息或本地进程代替。按实际授权决定是否由 Lead 自行执行、排队或报告阻塞。路由规则本身不授予启动 Worker 或子智能体的权限。

## 主账本归属

**Lead 当前 Task Progress 任务树是唯一主账本。**先按本技能第 1–2 节建立或恢复原任务，保留其 `session_id`、`task_instance_id`、阶段和依赖；创建 Worker 不重新初始化原任务。一个 Orca 协调批次对应一个 Lead 协调与验收叶节点。一次性委派通常由一个 Worker 给出一个结果；跨阶段工作包可由同一 Worker 持续承接。一个批次也可包含多个 Worker 的独立结果。其计划和 attempt 设 `execution_mode=main_session`，`self_execution_reason` 写明 Lead 负责派发、接收回报和独立验收，实际实施由 Orca Worker 完成。该节点记录 Lead 的协调与验收工作，不声称 Lead 实施了 Worker 的产物。现有服务端一次只允许一个 running 的 `main_session` 叶节点；因此并行 Worker 应放进同一批次节点，各 Worker 的派发与结果分别写入该节点证据。批次内不能靠不同叶节点独立放行依赖；需要独立放行时先按依赖划成顺序批次。

Lead 在派发前启动 ready 叶 attempt，通过当前 attempt 的 `update_task` 逐项累积 `evidence`，保留已记证据并保存派发范围、真实 Orca `worker_id` / `session_id`、派发回执和状态。排队时注明尚未开始；未送达或失败时记录障碍，不把创建成功当执行成功。各 Worker 回报后由 Lead 分别检查改动、实际验证和剩余风险，所有批次结果核对后再更新该叶及父/root；只有整个批次验收通过才以 resolved 放行依赖。回报缺失或证据不完整时保持 running，或如实以 open 收尾。主账本不调用 `bind_task_agent` 绑定 Orca Worker。

Worker 可为需要内部拆分的**获派范围**建立 Task Progress 辅助任务树，记录内部原生 subagent 的派发与复核；不能复制整个原任务 root。Task Progress `session_id` 必须来自该辅助树 `init_task_network` 的 UUID 回执，不能使用 Orca 的 `worker_id` 或业务 `session_id`。一次性任务能直接交付时可不另建树。Worker 报告关键结果及已使用的辅助树真实 ID，Lead 将其作为跨会话证据引用；辅助账本不会自动更新主账本或放行依赖。

新委派遇到尚未启动、原计划为 `subagent` 的普通 HTN 叶时，先用 `reassign_task` 改为 `main_session` 并记录原因。已经启动的原生 attempt 先核实是否实际派发。有真实原生回执时按原绑定复核和收束，尚未派发时如实取消该 attempt。后续工作通过显式重规划衔接 Orca。为 Orca 无法使用 `main_session` 的隔离并行请求保留排队路径，详见 [请求分流](request-routing.md)。

## Lead 建立与派发

1. Lead 保留用户沟通、范围、授权、跨包依赖、文件 owner 和最终验收。查看 team、已有 Worker、队列和受支持设备。复用前核对 Worker 的职责、执行权限、工作目录、文件可见范围和当前任务，避免重复派发或并发覆盖。一次性任务完成后仍可复用该 Worker，按宿主生命周期管理它。
2. 没有 team 时调用 `mcp__cindy_orca__start_team`。首次创建显式传符合授权的 `worker_permission_mode`；没有 Full access 授权时选 `auto`，因为省略且无保存偏好会默认 `bypassPermissions`。已是 Lead 时不为普通派活重复调用；要修改默认权限须按宿主要求显式传模式。Worker 内不能嵌套 team，也不能通过改档绕过审批。
3. 一个新 Worker 用 `mcp__cindy_orca__create_worker`；一次需要两个及以上新 Worker 必须用 `create_workers` 批量创建。每项明确唯一 `label`、`role` 和自包含的 `initial_task`。没有适用的手工创建组合、无法推断 Agent 时，先用 `list_available_models` 确认可用路线，再显式传 `agent`；显式 `model` / `provider_id` / `effort` 须匹配同一 Agent 的能力。按创建错误调整，不盲目重复请求。
4. `initial_task` 和复用投递正文写明背景、目标、交付物、SDK/生产等硬约束、依赖、工作目录、文件归属、验证边界、证据和回报要求。附上 Lead 账本的 `session_id`、`task_instance_id`、`epoch`、`task_id`、`attempt_id` 作为回报关联信息。共用当前 worktree 时显式传绝对 `working_dir`；省略只继承 Lead 启动时目录，不跟随之后的 `cd`。仅在项目允许独立检出时用 `new_worktree`，不嵌套创建。跨设备只选 `get_workspace_info` 标为 supported 的设备；远程设备读不到本机未提交文件。
5. 保存创建回执中的真实 `worker_id`、Orca `session_id`、`working_dir`、`file_access`、`initial_task_state`，逐项区分 `delivered`、`queued`、`not_delivered`、`none`。批量创建后按工具要求向用户逐字转告 `user_report`，补充每项成功、失败或跳过与首条任务状态；硬上限时同时说明回执里的建议。创建成功不等于任务已开始或完成。
6. 已有 Worker 的新工作用 `mcp__cindy_orca__send_to_worker`，默认尝试插话，显式传入 `delivery="steer"`。检查投递回执；若插话未成功，则按回执处理排队状态，不把排队当作已即时送达。回执有 `other_queued_messages` 时先判断是否需合并、改写或撤回自己未被消费的消息。需要替换或停止当前任务时，仍使用 `interrupt_worker` 等相应的中断机制，不以插话代替中断。记录复用回执和队列 ID。投递结果不明时先核对指定 Worker 的队列和当前状态，确认未接收后再重发。错误状态的 Worker 可原地继续。重试保存新的 Lead attempt ID，只用匹配当前实例、epoch 和 attempt 的回报验收。Worker 完成会主动回报；`read_worker`、`worker_status` 仅用于紧急诊断，不作日常轮询。

## Worker 内部分工与回报

Worker 需要内部拆分时，在获派范围内按 [协作与职责](delegation.md) 和 [HTN 派发](htn-dispatch.md) 拆分；复杂分析、实施、专门验证和独立评审用宿主原生 subagent。原生 attempt 按 `update_task` 启动 → 实际 spawn/follow-up → `bind_task_agent` 绑定真实 ID → Worker 独立复核的顺序记录。子智能体向 Worker 报告，不直接向 Lead 发最终报告。Worker 不更新 Lead 的兄弟节点或替 Lead 验收主账本。

Worker 只在最终结果或一个阻塞问题时调用 `mcp__orca_worker_bridge__send_to_lead`，每个 turn 一次，默认尝试插话，显式传入 `delivery="steer"`。检查投递回执；若插话未成功，则按回执处理排队状态，不把排队当作已即时送达，也不因排队重复发送。使用最新 Lead 消息中给出的真实 `worker_id`，回传该委派携带的 Lead 关联信息。合并内部结果，写清改动、实际验证、兼容证据、辅助任务树 ID 和剩余风险。发出阻塞问题后等待 Lead 的 `send_to_worker`；Lead 后续派活复用 Worker 的持久上下文。需要替换或停止当前任务时，仍使用宿主相应的中断机制。回报送达仍需 Lead 独立验收后才能更新主账本为 resolved。
