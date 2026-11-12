## 按需读取与小回执

进度内容默认不附在写操作或展示入口的会话消息里；仅显式查询才返回对应数据。优先读最小范围，不在每次 mutation 后机械地查询整树或全量。

### 先选查询目的，再读最小范围

- **首次开始或恢复**：先查一次 `overview`。恢复时按主 skill 再查 `context` 和完整的请求分页；如果只是继续当前叶任务，不要每轮都重复读 context。
- **判断计划或依赖**：只在建立/修改计划、阶段切换或需要挑选下一项工作时读 `tree`、指定 `tasks` 或 `get_ready_tasks`。已知目标节点时优先查该节点；不要为了读取一个节点而请求 `full`。
- **核对提交与证据**：验收子智能体结果时查对应 `tasks`；只有摘要缺少执行尝试或失效记录等必要证据时再读该节点的 `records`。
- **检查历史**：只在用户询问历史、需要审计先前计划或当前证据明确依赖历史时分页读取对应 `history_kind`。`full` 仅用于确需完整目标实例、且无法由定向查询满足的问题。
- **写后读取**：一次写操作的成功回执已经证明该操作被记录。只有下一步确实依赖被写入的节点详情、依赖证据、review 状态或当前计划结构时再查；相邻的多次写操作可先完成后做一次合并的定向读取。
- **分页与版本冲突**：从同一查询的第一页取得 `state_revision`，再用它固定后续页。独立的单次 `tasks` 或 `records` 查询需要最新结果时，省略 `expected_state_revision`；只有确实需要对齐另一份快照时才传那份快照的版本。工具明确报告 revision 变化时，从第一页重新开始，不混用新旧页。身份或参数错误按主 skill 的失败处理规则处置。

同一轮内若刚成功读取了仍有效的视图，且期间没有计划/状态写入、上下文恢复或用户新问题，复用已有结果，不要重复调用相同的 `get_progress`。不要为“确认工具还可用”而轮询；等待、无变化状态和宿主后台刷新均不需要模型再次查询。

遇到 `task_instance_id` 不匹配且已确认 `session_id` 时，用一次省略 `task_instance_id` 的 `get_progress(view="overview")` 找回当前主实例。若目标是并行请求，读取 `view="context"` 的 `session_work.requests`，按请求身份核对其 `task_instance_id`。若旧写入所属的实例或 attempt 已失效，保留原回执和错误，不把结果改投给新实例或 attempt。

**合并同一轮的读取。**需要比较多个节点时，优先一次 `view="tasks"` 传入最多 20 个 `task_ids`；批量结果已包含当前提交、派发、review 和证据时，不要仅因节点失败、待复核或状态变化而补读 `records`。只有下一步判断确实需要、且 `tasks` 未提供的历史尝试或失效记录等细节时，才读取相关节点的 `records`，不要再把同一批节点逐个重查。`select_work_context` 用于切换到具体节点/attempt 或恢复执行；在范围未变时，不要把它当作状态刷新工具。

如果宿主截断查询结果，只返回不完整的文本，把所需节点分成更小的批次，避免原样重试。需要对照同一快照时，各批使用同一个 `expected_state_revision`，版本变化后从首批重读。

**降低外部运行的轮询噪声。**等待长时间 Dagu gate 时优先使用宿主的事件等待能力；若只能轮询，稳定运行期间通常每 60–90 秒读取一次 run 状态，临近预期完成或需要及时处理结果时可缩短间隔。仅在 run 阶段变化、失败，或下一步判断确实依赖详细输出时读取 `step_log`；无变化时不要每个轮询周期同时重读完整日志。发现阶段变化后再刷新相关状态与日志，不把“已等待一段时间”本身记录为状态变化。

**核对托管命令退出。**等待 `start_task_execution` 启动的命令时，需要判断退出才用 `get_progress(view="tasks", task_ids=["<执行节点 ID>"])` 读取当前节点的 `execution`。该查询会同步已落盘的退出回执；可复用同一次读取的状态和退出码。`records(record_kind="attempts")` 留给比较旧尝试或查找当前节点视图没有的证据。当前回执仍为 running 时等待下一次有意义的检查，避免立即重读。

- 写操作的身份与状态位于 `structuredContent` 顶层：保存 `session_id`、`task_instance_id`、`root_id`、`epoch`；启动返回的 `attempt_id` 也在顶层，单个目标状态在 `affected`。回执无 `progress`、节点摘要、证据或 `session_work`，不能从中读取完整结果。请求回执另含 `request_id`、`request_task_instance_id`、`request_status`；`route_request` 创建并行实例后使用 `request_task_instance_id`，不要误用当前主任务 ID。
- `get_progress` 默认 `view="overview"`，只含计数、`history_summary`、`session_work_summary` 和至多 20 个 `split_review_due_ids`，没有完整上下文或节点。查询元数据在 `structuredContent` 顶层，数据在 `structuredContent.progress`。
- overview 的 `stage_inventory_due` 也最多列出 20 项；检查 `stage_inventory_due_count` 和 `stage_inventory_due_has_more`。被截断时分页读取 root 的 `tree`，从节点的 `stage_inventory_due_attempt` 收集完整提醒。提交盘点后 revision 改变，下一次盘点须重新读取。

| 需要的数据 | 显式查询参数 |
|---|---|
| 一层或多层子树 | `view="tree", task_id="<起点>", depth=1`；深度 0–8，广度优先分页；注意 `depth_limited`、`child_count` 和 `title_truncated`，不可把简要节点当完整文本。 |
| 指定当前节点、派发、submission、review 和证据 | `view="tasks", task_ids=["<节点 ID>"]`；每次 1–20 个，不附历史记录数组。 |
| 指定节点的追加记录 | `view="records", task_id="<节点 ID>", record_kind="attempts"`；另可选 `invalidated_results`、`reassignment_history`、`stage_inventories`。 |
| 恢复上下文与会话请求 | `view="context"`；完整 `task_context` 与 `session_work`，其中 requests 分页。 |
| 某类历史 | `view="history", history_kind="history"`；另可选 `task_history`、`plan_snapshots`、`plan_revisions`、`context_revisions`、`legacy_history`、`handoffs`、`handoff_closures`、`closure_reviews`。快照是未关闭的时点记录，修订记录包含修改前后；两者不是新的计划轮次。 |
| 确需完整目标实例 | `view="full"`；兼容 `include_history=true`，不能与其他 view 混用。 |

分页的 `pagination` 包含 `offset`、`limit`、`total`、`has_more`、`next_offset`；默认 limit=50，可选 1–200。保留第一页的 `state_revision`，后续页传 `expected_state_revision`，版本变化报错时从第一页重读，不混合不同版本。线性步骤在 tasks/records 中使用字符串索引，如 `"0"`。

`full` 不截断目标 JSON 实例的当前状态、执行记录和归档，包含完整会话请求登记；其他并行实例仅有精简当前快照，其历史须指定各自 `task_instance_id` 单独读取。旧 SQLite 仍受既定只读摘录上限约束，`full` 不表示完整旧数据库。恢复按 overview → context → 必要 tree/tasks；验收前用 tasks 查询目标 submission/evidence，必要时再读 records，不能仅凭小回执接受结果。overview 提示拆分复核时，用 tree/tasks 定位全部相关节点；IDs 的 has_more 不可忽略。

`get_ready_tasks` 是显式分页读取，只返回 `structuredContent.ready_tasks` 与分页元数据，不带整树或 `session_work`。`dependencies` 保留前驱状态、策略和 `inherited_from`；完整依赖摘要/证据按前驱 ID 去重放在 `structuredContent.dependency_evidence[id]`，派发时一并读取和传递。

主会话首次创建任务并完成粗粒度规划后，立即调用一次 `render_progress` 自动请求进度页；已有打开的进度页随同会话轮询复用。用户明确要求打开/重开时可再次调用。继续、恢复、普通读取和更新不重复展示；用户关闭或宿主拒绝后不自动重开。入口只返回 `bootstrap` 身份与一次性展示意图，面板自行调用 app-only `get_progress_view`，模型不要调用这些 App 工具。资源首选 `fullscreen`；最终是否以标签页打开取决于宿主支持，不能把工具回执当作已打开的证据。旧消息不会追溯缩小，旧 iframe 需重新打开才能加载新版资源；UTF-8 字节测量不能当作 token 数或原生宿主消息展开耗时。
