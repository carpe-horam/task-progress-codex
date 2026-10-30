## 按需读取与小回执

进度内容默认不附在写操作或展示入口的会话消息里；仅显式查询才返回对应数据。优先读最小范围，不在每次 mutation 后机械地查询整树或全量。

- 写操作的身份与状态位于 `structuredContent` 顶层：保存 `session_id`、`task_instance_id`、`root_id`、`epoch`；启动返回的 `attempt_id` 也在顶层，单个目标状态在 `affected`。回执无 `progress`、节点摘要、证据或 `session_work`，不能从中读取完整结果。请求回执另含 `request_id`、`request_task_instance_id`、`request_status`；`route_request` 创建并行实例后使用 `request_task_instance_id`，不要误用当前主任务 ID。
- `get_progress` 默认 `view="overview"`，只含计数、`history_summary`、`session_work_summary` 和至多 20 个 `split_review_due_ids`，没有完整上下文或节点。查询元数据在 `structuredContent` 顶层，数据在 `structuredContent.progress`。
- overview 的 `stage_inventory_due` 也最多列出 20 项；检查 `stage_inventory_due_count` 和 `stage_inventory_due_has_more`。被截断时分页读取 root 的 `tree`，从节点的 `stage_inventory_due_attempt` 收集完整提醒。提交盘点后 revision 改变，下一次盘点须重新读取。

| 需要的数据 | 显式查询参数 |
|---|---|
| 一层或多层子树 | `view="tree", task_id="<起点>", depth=1`；深度 0–8，广度优先分页；注意 `depth_limited`、`child_count` 和 `title_truncated`，不可把简要节点当完整文本。 |
| 指定当前节点、派发、submission、review 和证据 | `view="tasks", task_ids=["<节点 ID>"]`；每次 1–20 个，不附历史记录数组。 |
| 指定节点的追加记录 | `view="records", task_id="<节点 ID>", record_kind="attempts"`；另可选 `invalidated_results`、`reassignment_history`、`stage_inventories`。 |
| 恢复上下文与会话请求 | `view="context"`；完整 `task_context` 与 `session_work`，其中 requests 分页。 |
| 某类历史 | `view="history", history_kind="history"`；另可选 `task_history`、`context_revisions`、`legacy_history`、`handoffs`、`handoff_closures`、`closure_reviews`。 |
| 确需完整目标实例 | `view="full"`；兼容 `include_history=true`，不能与其他 view 混用。 |

分页的 `pagination` 包含 `offset`、`limit`、`total`、`has_more`、`next_offset`；默认 limit=50，可选 1–200。保留第一页的 `state_revision`，后续页传 `expected_state_revision`，版本变化报错时从第一页重读，不混合不同版本。线性步骤在 tasks/records 中使用字符串索引，如 `"0"`。

`full` 不截断目标 JSON 实例的当前状态、执行记录和归档，包含完整会话请求登记；其他并行实例仅有精简当前快照，其历史须指定各自 `task_instance_id` 单独读取。旧 SQLite 仍受既定只读摘录上限约束，`full` 不表示完整旧数据库。恢复按 overview → context → 必要 tree/tasks；验收前用 tasks 查询目标 submission/evidence，必要时再读 records，不能仅凭小回执接受结果。overview 提示拆分复核时，用 tree/tasks 定位全部相关节点；IDs 的 has_more 不可忽略。

`get_ready_tasks` 是显式分页读取，只返回 `structuredContent.ready_tasks` 与分页元数据，不带整树或 `session_work`。`dependencies` 保留前驱状态、策略和 `inherited_from`；完整依赖摘要/证据按前驱 ID 去重放在 `structuredContent.dependency_evidence[id]`，派发时一并读取和传递。

主会话首次创建任务并完成粗粒度规划后，立即调用一次 `render_progress` 自动请求进度页；已有打开的进度页随同会话轮询复用。用户明确要求打开/重开时可再次调用。继续、恢复、普通读取和更新不重复展示；用户关闭或宿主拒绝后不自动重开。入口只返回 `bootstrap` 身份与一次性展示意图，面板自行调用 app-only `get_progress_view`，模型不要调用这些 App 工具。资源首选 `fullscreen`；最终是否以标签页打开取决于宿主支持，不能把工具回执当作已打开的证据。旧消息不会追溯缩小，旧 iframe 需重新打开才能加载新版资源；UTF-8 字节测量不能当作 token 数或原生宿主消息展开耗时。
