## 展示、恢复与清理

`init_task_network` 与 `init_progress` 只返回身份与状态小回执，不触发展示。主会话首次创建任务并完成粗规划后调用一次 `render_progress`，自动请求进度页；用户明确要求重开时再调用。面板通过 app-only 读取独立加载视图，资源首选 fullscreen，让支持该元数据的宿主在 JavaScript 初始化前选择展示模式；挂载后只消费最新的一次性意图并按宿主能力请求切换。同一会话的新任务复用原会话 ID；已打开的视图通过轮询读取最新任务。fullscreen 标签页打开时会话内卡片隐藏，关闭后恢复；消费过的意图不会因轮询、重挂载或继续任务而再次请求打开。普通状态更新、读取和后台轮询不得调用展示入口。恢复任务时按需要先 `get_progress` 读取状态。子智能体若在主会话任务树中执行，只更新获派节点，不调用展示入口，不另开主会话面板。纯文本客户端没有交互面板时，按需显式查询并简洁汇报。不要手工拼接假进度，或把任务卡片描述成日志面板。

- `session_id` 是 Codex 会话的数据边界；所有读取、更新、渲染、重规划和清理操作显式传入它。本会话内多个任务复用同一个 ID，以 `task_instance_id` 区分任务实例。
- 每个 Codex 会话使用自己生成并保存的 ID，不能把其他会话的 ID 复制为本会话身份。用户授权的跨会话审查可以读取明确关联的 ID，但不转移任务所有权。其他会话的任务不得阻止本会话初始化。
- 恢复先调用 overview，再用 context 读取 `task_context` 和分页的 `session_work`，按需读取 tree/tasks；继续原任务或按队列显式激活，不用初始化恢复。若已知 ID 的本会话数据不存在，说明实际情况，确需开始新任务时仍使用该会话 ID 初始化；首次没有已知 ID 时让初始化工具生成并保存。
- `init_task_network` 的同一次初始化重试可复用稳定的 `request_id`；首次省略 `session_id` 时使用全局唯一键（建议 UUID），收到会话 ID 后可带该 ID 重试。同一任务重规划后重试返回当前计划；绑定任务已归档或清除时旧键会被拒绝，不能用它恢复旧任务或覆盖当前任务。清理任务内容仍保留请求哈希和身份墓碑，以防迟到请求重建已清除任务。
- 自动展示使用公开的 [OpenAI 资源展示模式](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#display-modes) 与 [MCP Apps 模式请求](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx#display-modes)。宿主未挂载或不支持时保留手动入口；请求成功不等于可见标签页。历史会话没有公开标记，宿主在 JavaScript 前创建的重复页只能在挂载后请求退回 inline，无法保证不短暂出现。旧版无意图结果不自动请求切换。
- fullscreen 标签页每 10 秒续租；关闭时释放登记，宿主未发 teardown 时租约 35 秒后过期。会话内卡片在标签页打开期间隐藏，关闭后恢复；普通更新只改数据，不应为了刷新卡片重复调用 `render_progress`。不要用清理进度来关闭或重开面板。
- 任务数据按插件 `session_id` 隔离。当前公开 MCP Apps 上下文未定义“当前会话是历史/归档会话”的状态标志，因此不要声称插件能主动隐藏已经保存的历史会话卡片；若宿主后续提供该标志，应再增加历史会话抑制。
- `task_history` 保存本会话内已归档的旧任务；HTN 的 `history` 保存同一任务重规划产生的旧计划轮次。初始化创建新 `task_instance_id` 并归档旧任务；`rebuild_task_network` 保持任务实例不变，只递增 `epoch`。两种历史不能混写，也不能用重规划代替开始新任务。
- `get_progress` 默认只返回 overview；`history_included=false` 表示未加载历史正文，不表示没有历史。查询历史优先分页 history，需要完整目标实例时才用 full；面板轮询和导出由 App 独立读取，模型无需代其加载。
- 旧 SQLite v3 记录通过独立只读来源展示，不能当作当前 JSON 任务的验收结果。两种存储中的标题或状态冲突须保留来源并核对证据；仅存在旧库记录时也不可误报“从未建立任务”。兼容读取不迁移数据、不补造 review，也不授权清理旧库。
- 一旦本会话存在历史任务，当前任务的写操作必须传回当前 `task_instance_id`，避免旧任务的迟到更新写入新任务。建议每次当前任务写操作都传入，包括 `decompose_task`、`update_task`、`bind_task_agent`、`review_task`、`reassign_task`、`rebuild_task_network` 和 `update_step`。`clear_progress` 仅接收 `session_id`，不支持 `task_instance_id`，其语义是删除本会话所有进度；清空后首次及后续新任务的写操作都必须传当前 `task_instance_id`，以拒绝清空前不带实例 ID 的迟到请求。初始化工具负责创建新实例，保存其新返回的 ID；后续写操作不得继续使用已归档实例 ID。`attempt_id` 和 `epoch` 仍分别标识当前任务中的尝试与计划轮次，不能代替 `task_instance_id`。
- 完成后用 tasks 核对根结果，用 context 读完所有请求分页并确认会话剩余工作，在最终回复说明结果及未解决项；有队列则继续显式激活，除非用户要求停止。任务完成、开始新任务或关闭视图都不触发清理；未归档 Codex 会话的当前任务、历史、计划与证据必须保留。所有已登记工作收尾后开始新目标，才用同一 `session_id` 初始化。
- `clear_progress` 仅用于已归档 Codex 会话的全量数据清理。当前插件没有 Codex 归档事件或归档状态与插件 `session_id` 的可信映射，不能自行确认某个会话已归档，也不会自动响应归档。仅有插件 ID、任务完成状态、`task_history` 或用户提出删除请求，都不构成已归档依据。用户明确要求清理时，按 [cleanup-task-progress-history](../../cleanup-task-progress-history/SKILL.md) 核对用户或宿主提供的明确归档依据、目标插件 ID、保留期限与删除范围；归档状态或对应关系无法确认就不调用清理工具。
- `clear_progress({"session_id":"<已确认归档会话的插件 ID>"})` 会清除该 ID 下的当前任务与 `task_history`，不支持仅删除某个历史任务。不要迁移或删除旧版全局状态，也不要扩大到其他未明确指定且未确认归档的会话。

若环境提供 `set_thread_title`，可在重要阶段更新标题；不要为标题频繁调用工具。
