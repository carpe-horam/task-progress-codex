## 开工前的短流程

任何实质工作开始前先判断任务粒度并做粗粒度规划。简单问答无需任务树；单步查询或简单操作不强制初始化，若选择跟踪不可再分的单步任务，初始化时用 `atomic_reason` 记录判断。多阶段任务先拆成有清晰结果边界的阶段。准备性读取、收集约束和必要澄清可以先进行，但不要先编辑、运行验证或派发子智能体。

拆分应产生至少两个有独立结果边界的子任务。如果只能拆出一个子任务，就保留原任务，补充说明或调整依赖，不再套一层父子结构。不要为了编号、换执行者或设置依赖而创建只有一个子任务的分组；也不要为凑数量制造无独立结果的步骤。已有分组可以逐项追加真正新增的工作，这不等于把一个叶任务拆成单子任务。

首次建立任务，或原任务已收尾且无待处理独立请求时，优先一次调用 `init_task_network`，传入至少两个阶段的 `tasks`，每项包含 `assignment.execution_mode`；如暂时无法一次确定，可先初始化再立即 `decompose_task`。服务端会把未规划 root 标为 `planning_status=required` 并拒绝直接开工。确为单步时传入具体 `atomic_reason`。可传稳定 `request_id` 安全重试同一个初始化调用。

继续当前任务先 `get_progress`，复用现有任务实例；不要重复初始化。“打开 task process / task progress / 任务进度”意味着读取当前会话已有进度并按需展示，不代表开始新任务。压缩恢复、用户补充范围、阶段切换时检查并更新任务树。子智能体只更新获派节点，不创建主会话 root 或单独展示主面板。

主会话负责理解目标、拆分和编辑任务、调整计划及验收；Task Progress 持久保存这些决策与执行事实，并引导下一步，不能替主会话猜测用户意图。新消息先区分当前目标补充、简短问答、独立请求和明确停止/替换。独立请求不能覆盖仍在处理的主任务：使用 `route_request` 排队，或建立隔离的并行实例交给子智能体。具体流程见 [请求分流与压缩恢复](request-routing.md)。

使用 `update_task_context` 保存目标、约束、验收条件、具体恢复位置和修改理由；原文只记录实际收到的内容，不从摘要补造。压缩恢复、等待返回或插入请求完成后，先查 `get_progress(view="overview")`，再用 `view="context"` 读取 `task_context` 和分页的 `session_work`，按需查指定节点，再继续当前主任务或显式激活队首。一个插入请求完成不表示整个会话工作完成；结束前读完请求分页，检查剩余工作。用户明确停止或取消时服从该指令，不以队列要求强行继续。

“继续”“下一轮”“补做验证”通常仍属于原目标。保存时点使用 `snapshot_task_network`，不停止正在运行的工作；未开始分支的依赖、分组或同级顺序调整优先使用 `revise_task_plan`，重分组还需受影响分组和消费者未开始；需要大范围重构时才使用 `rebuild_task_network`。新实例仅限独立目标并提供 `task_relationship="independent"` 和 `new_task_reason`。切换实例或重规划前须先协调 running 和待复核提交；快照不需要收束它们。

插件 hooks 在以下时机提供上下文提醒：

- `SessionStart`（包括 startup、resume、clear、compact、fork）和 `UserPromptSubmit`：规划、恢复和请求分流。
- `PreToolUse`：工具推进实质工作前确认计划、ready 叶节点和有效 attempt；派发前确认先启动 attempt。
- `PostToolUse`：工具返回后记录重要状态变化和证据；派发返回后绑定真实 agent ID，子任务提交后由主会话独立验收。运行句柄、排队或局部输出不能当成完成。
- `SubagentStart`：子智能体只读取和执行获派节点，使用已有身份提交结果，由主会话验收。

执行过程使用短提示，工具事件去重与语义提醒预算分开；等待、轮询、已知查询和控制操作静默。日常用户更新突出目标、当前 owner、重要约束和决策；完整派发文本、工具配置和执行回执保存在任务账本，需要时再查询，避免每次进度都重复展示长参数。Task Progress 成功写回执会提供版本、操作名和服务端推导的 `context_effect`，hook 可将其与实际收到的宿主 session/agent 字段关联，再按插件的 session、instance、epoch、task 和 attempt 读取有界状态。只有显式 `agent_id` 可用于 actor scope；缺失身份、缓存/状态不可读或旧 epoch/attempt 都降级为 unknown/stale，不推断角色或认证。spawn 关联要求单个明确的待派发 attempt 与成功返回的结构化 host `agent_id`；`agent_name` 仍是调用方声明。

恢复已有计划时，先显式查询其当前 `overview`/`tree`，再调用 `select_work_context`，传入准确的 `session_id`、`task_instance_id`、`epoch` 和目标 `task_id`；目标 attempt 明确时同时传 `attempt_id`。成功回执由 PostToolUse 绑定到实际收到回执的宿主 actor，供后续工具时机检查状态缺口。该选择仅记录观察到的上下文，不认证角色或权限；选择失败、身份缺失、目标过期或回执不可读时继续安全降级。

事件重放与提醒限频标记使用身份哈希，写入 `PLUGIN_DATA`（兼容 `CLAUDE_PLUGIN_DATA`）的 `hook-reminders` 目录；无该目录时使用用户临时目录，标记超过七天后分批清理。任务关联侧车单独写入 `~/.cache/task-progress/hook-contexts`，以哈希宿主作用域命名并原子写入；文件只记录关联和回执观察事实，不认证角色。缺稳定 turn/actor 或缓存不可用时，高频工具回调静默，`SubagentStart` 保留一次通用获派提示。宿主 session ID 仅用于 hook scope，不是插件的任务 session UUID。

这些回调不自动建树、展示面板、写执行回执或改变任务结果，也不读取 transcript；PostToolUse 只观察成功、版本化的正式回执，hook 状态检查只读。required 门禁和 Stop auto-continue 均保持关闭。`SessionStart(source=compact)` 会在压缩后的下一次模型请求前恢复上下文；不使用无上下文注入契约的压缩事件或无条件续跑的 Stop 事件。hooks 需经 Codex 信任审核才会生效，修改已信任的回调脚本后也需重新审核。宿主覆盖不完整；服务端规划校验才是插件内的硬约束。事件契约见 [Codex Hooks 文档](https://learn.chatgpt.com/docs/hooks)。
