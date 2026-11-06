# 执行、失败收尾与长任务拆分

任务树表达可单独验收的结果，attempt 表达一次尝试，execution 表达该尝试中的一轮命令或外部运行。一个 attempt 最多绑定一轮 execution。失败、阻塞或取消之后，新运行必须使用 `update_task(running, retry=true)` 取得新 attempt；不能把多个 run ID 连续覆盖到同一个 running 节点。

## 托管命令

1. 按计划启动叶任务并保存实际返回的 `attempt_id`。
2. 调用 `start_task_execution`，提供精确身份、授权范围内的 `command` argv 数组、绝对 `cwd` 和目的 `summary`。可预先生成 UUID `execution_id`，用于丢失回执后的安全重放。
3. 独立监督进程执行命令，将输出写入私有日志，保存退出码和真实结束时间。非零退出自动将任务收尾为 failed，并保留本轮原因与执行记录。
4. 监督进程独立于 MCP 服务。服务不在时仍保存退出回执，进度读取和后续写入会重新对账。没有回执时保持未知，不凭经过时间推断失败，也不自动杀死进程。
5. 退出成功只表示命令成功；所有者仍须核对验收条件并收尾任务。界面显示“执行已结束 · 待任务收尾”，执行回执有独立结束时间。

相同 UUID 和相同输入的启动重放不会执行第二次命令；同 UUID 改参数、同 attempt 绑定其他执行，以及把旧 run ID 套进新 attempt 都会被拒绝。正在执行的托管命令不能被手动提前标成停止、成功或失败。

该入口不增加命令授权，不改变项目的本地/Dagu 路由。不能把必须通过 Dagu 运行的命令改成本地托管命令。

## Dagu 与子代理回执

外部运行用 `record_task_execution` 登记 `provider`、真实 `execution_id`、当前 attempt 和 running 状态。Dagu 的执行 ID 使用 `dagRunId`；子代理必须与 `bind_task_agent` 的实际标识一致。

收到终态后上报 status、summary、evidence，及已知的 exit_code、finished_at。running 可提供真实 started_at。时间须带时区，结束不得早于开始。外部回执是调用方填报，不是宿主认证。

| 执行回执 | 任务转换 |
| --- | --- |
| failed | 自动 failed，记录原因和结束时间 |
| blocked | 自动 finished/open，保留评审或前置缺口 |
| cancelled | 自动 cancelled，保留结束时间 |
| succeeded | 保存执行成功证据，等待任务所有者收尾与既有独立验收 |

同一终态回执重复上报幂等，冲突回执和旧 attempt 回执拒绝。Dagu 的 failed 可能对应 Sumi blocked，必须按实际流程结论登记，不能全部解释成测试断言失败。

插件使用 `PreToolUse` 区分派发、followup、等待/轮询、查询、编辑、执行和未知工具；等待/轮询及已知只读查询默认静默。派发前提醒先启动 attempt，只有成功结果含结构化 host `agent_id` 且待派发 attempt 唯一时才建立局部观察关联并提醒绑定。`PostToolUse` 对 Task Progress 自身调用只观察版本化成功回执，对其他工具按状态缺口给短提示；`SubagentStart` 仅在精确观察关联仍对应 running attempt 时注入插件任务 ID，否则给通用获派指引。终态与 retry 回执会撤销旧映射；子 actor 的终态回执也会通过哈希父 binding 索引撤销 parent 侧映射。事件重放去重与提醒语义预算分开，缺稳定身份或缓存不可用时高频工具回调静默。Windows handler 使用 `commandWindows` 指向现有 `.cmd` launcher。

子代理等待结果返回后，`PostToolUse` 的 wait 回调和下一次 `UserPromptSubmit` 会按显式绑定的 Task Progress 实例读取有界待办摘要，提示当前 pending submission 以及仍待综合的汇总节点；已结束、已接受收尾、已移交关闭或按阶段盘点标记为 obsolete/covered 的父节点不会重复进入 synthesis 队列。派发 actor 的精确任务范围会保存在本地 sidecar；该 child 不读取实例级父级验收队列，提交回执后仍保持隔离。重复提示绑定首个待办身份和 attempt 并受每轮/冷却预算限制。提醒只要求主会话查询指定 submission/evidence 并调用 `review_task` 或完成父节点 synthesis，不自动验收、不阻塞等待，也不从子代理 transcript 推断状态。没有精确 actor/插件实例绑定、状态过期或读取失败时保持静默。

Task Progress 成功 mutation receipt 带有 `receipt_version`、`operation` 和服务端推导的 `context_effect`。hook 在侧车中按实际宿主 session/agent 作用域记录这些关联，并用本地只读 `read_hook_snapshot` 核验插件 session、instance、epoch、task 和 attempt；host actor、角色和 `agent_name` 不因此获得认证。旧身份、状态读取故障或不支持的回执均降级为 unknown/stale。required 门禁和 Stop auto-continue 保持关闭。

这些 hooks 不读取 transcript，也不自动写入执行终态或任务结果。工具返回可能只是运行句柄、排队或局部结果，仍须保持 running；子智能体提交仍须主会话独立验收。通过其他工具启动、且未接入执行回执的外部工作，插件无法自动知道其是否结束。不能宣称此类工作已自动同步，也不能自动修改已有活动会话的历史事实。hooks 尚未在原生 Windows 或真实 Codex 工具事件上运行验收；宿主事件覆盖及输出格式遵循 [Codex Hooks 文档](https://learn.chatgpt.com/docs/hooks)，信任审核后才会运行回调。

## 收尾写入恢复

错误 attempt 的写入不会应用到当前 attempt。错误回执提供 `structuredContent.recovery`，含当前实例、任务、attempt 和恢复动作；拒绝记录另存为 `write_rejections`。读取当前记录并核实实际工作后，用正确身份收尾。不得盲目替换 ID 重放旧结果。正确收尾会标记该轮拒绝审计已恢复。

未知或缺失 attempt 的终态写入被拒绝后，服务端阻止该轮继续运行更新或启动新执行，直到如实收尾。已识别的旧 attempt 迟到回执只记录拒绝，不封锁新的当前尝试。托管命令的真实退出回执仍可自动完成收尾；不会因恢复门禁中断实际进程。

## 服务端拆分门禁

HTN 叶 attempt 超过 10 分钟后，继续运行更新或绑定新执行会被拒绝，直到实际分解，或调用 `review_task_split` 明确唯一剩余结果、理由和证据。多个剩余结果必须分解，用依赖表示串行顺序。

保留原子叶的决定绑定 attempt、原始标题和 execution ID，最多有效 10 分钟；换执行或重试立即失效。摘要里的“无需拆分”不构成豁免。读取和终态回执始终可用，不为拆分中断正在运行的命令。插件强制约束自身工具的状态转换，不能拦截宿主中所有其他工具的使用。
