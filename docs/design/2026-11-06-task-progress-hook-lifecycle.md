# Task Progress 会话执行钩子方案

日期：2026-11-06

状态：方案已进入实施。阶段 A/B 已在当前 worktree 实施；真实宿主验收未完成，required 门禁和 Stop auto-continue 保持关闭。

目标：减少开工、派发、阶段切换和收尾时遗漏 Task Progress 调用，同时控制提醒噪音。

## 1. 设计决策

采用分期方案：

1. **修正事件语义**：区分派发、追加工作、等待、编辑、命令启动和查询；等待超时默认静默。
2. **让提醒读取状态**：通过宿主观察到的 Task Progress 成功回执建立关联，读取服务端小投影，只提示当前缺少的动作。
3. **按能力启用门禁**：guided 为默认模式；required 仅对已验证的主体关联和明确工具范围执行调用前检查。第一阶段不启用 required。

hook 可以维护关联、提示记录和诊断事实。任务树、attempt、执行回执、提交、独立验收和最终结果继续由 Task Progress 的正式接口维护。hook 不自动创建任务、派发代理、打开面板或把工具成功解释为任务完成。

本方案细化 [既有启动可靠性设计](2026-10-27-task-progress-startup-reliability.md) 中的生命周期入口、会话绑定和执行中提醒部分。

## 2. 当前问题与对应改动

| 当前行为 | 后果 | 方案 |
| --- | --- | --- |
| spawn、followup、wait 共用 delegation | 等待返回也提示绑定新 agent ID | 分成 dispatch、followup、wait；每类有独立动作 |
| Pre/Post 只输出静态提示 | 无法知道计划、attempt 或验收是否缺失 | 读取权威小投影，按缺口提示 |
| 每类工具的 Pre/Post 分别限频 | 同一轮可能收到多条意思接近的提醒 | 事件重放去重与语义限频分开，使用共享预算 |
| 全部未知工具落入 other | 低价值查询也触发通用 Post 提示 | 已知查询静默，未知工具仅可占用一次通用 Pre 预算 |
| Task Progress 自身工具完全过滤 | 无法从正式调用回执建立宿主关联 | Pre 放行；Post 观察成功回执但不注入普通工具提醒 |
| SubagentStart 文案假定已有插件任务 ID | 未传派发身份时无法照做 | 有明确身份才引用；否则要求读取派发上下文，不猜 root |
| 缓存故障时每次输出提示 | 高频工具调用放大故障噪音 | 高频通用提示降级为静默；诊断保持 unknown |
| 只有 POSIX command | 原生 Windows 缺可执行路径 | 每个 handler 配置 commandWindows，使用现有 .cmd launcher |

## 3. 事件处理流水线

~~~mermaid
flowchart TD
    A[宿主 hook 事件] --> B[校验事件与重放去重]
    B --> C{是否 Task Progress 工具}
    C -->|Pre| P[直接放行]
    C -->|Post| O[验证成功回执并观察关联]
    C -->|其他| D[精确工具分类与适配]
    O --> L[更新关联与诊断后静默返回]
    D --> E[读取当前关联和权威状态小投影]
    E --> F{模式及覆盖能力}
    F -->|guided 或能力未知| G[按缺口选择短提醒]
    F -->|required 且范围精确| H[检查本次操作的状态条件]
    H -->|满足| P
    H -->|明确不满足| I[返回可恢复的拒绝原因]
    H -->|读取失败或主体不明| G
    G --> J[语义限频与预算]
    J --> K[additionalContext 或静默]
~~~

同步 handler 保持 2 秒宿主上限。内部本地读取使用可超时的锁等待和约 500 ms 的软预算；超时退出为可见降级，不等待 MCP 连接、网络或外部模型。具体耗时目标在真实宿主样本中验证。

## 4. 工具分类

分类使用规范化后的**完整工具名**及注册适配器；不因末尾出现 agent、write 或 run 就匹配任意 MCP 工具。matcher 用于宿主路由，业务分类仍需核对事件中的 canonical tool_name。

| 类别 | 已知入口 | Pre 行为 | Post 行为 |
| --- | --- | --- | --- |
| progress | Task Progress 正式工具 | 始终放行 | 观察版本化成功回执；不循环提醒 |
| dispatch | spawn_agent；已确认对应入口的 Agent matcher | 检查待派发节点/attempt，提醒先 start 再 spawn | 成功且有真实 ID 才提醒 bind；失败不绑定 |
| followup | followup_task | 明确复用已有 agent；新获派工作先准备对应 attempt | 投递成功不等于工作完成，不提示绑定新 ID |
| wait | wait_agent、wait_threads、已登记的运行轮询 | 默认静默，不创建 attempt | 超时、运行中、仅通知静默；新 pending submission 才提醒查证据并 review |
| edit | apply_patch；已适配的确定写入接口 | 首次实质操作前检查工作上下文 | 有实际缺口或重要状态变化才提示 |
| execution_start | Bash；已适配的 Dagu/外部任务启动操作 | 确认当前 attempt；尚无适配器时只给条件式提示 | 识别运行句柄或终态；不自动写任务结果 |
| execution_poll | 已登记的轮询/等待操作 | 不重新要求启动任务 | 仅新终态或权威状态缺口触发提示 |
| query/bootstrap | 工具发现、规则/skill读取、进度查询、已适配只读查询 | 静默放行 | 静默 |
| unknown | 无法确认语义的本地/MCP工具或编排包装器 | guided 下可使用一次通用开工预算 | 默认静默 |

补充规则：

- Dagu 的查询、启动、取消和等待按工具公开的 action 枚举或独立工具名区分，不按整个命名空间统一算执行。
- followup 是补充现有工作还是追加独立工作，由主会话判断并调用正式接口；hook 不解析自由文本替主会话决定。
- wait_threads 可能等待其他用户拥有的 chat，不自动解释为本任务的子代理提交。
- Bash 不使用 cat、rg 等关键词判断只读，也不从任意 shell/JavaScript 文本推测副作用。无可靠结构化适配器时，Bash 与包装器保持 guided。
- code mode 以宿主实际发出的嵌套工具事件为准，不解析外层脚本来“发现”内部调用。
- 当前回调不检查工具结果；本方案第二期才按版本化工具适配器读取必要的结构化状态字段。未知结果保持 unknown，不读取 transcript。

## 5. 各生命周期事件的策略

| 事件 | 处理规则 |
| --- | --- |
| SessionStart startup | 一条短开工提示，包含粒度判断和 skill 入口；不附完整职责全集 |
| SessionStart clear | 保留任务数据与会话锚点，撤销当前轮次执行选择，提示恢复确认；与 clear_progress 区分 |
| SessionStart resume/compact | 保留会话关联，重新核验 instance/epoch/attempt，提示 overview → context；不新建 root |
| SessionStart fork | 新宿主作用域默认未绑定；保留来源供说明，不复制父级可写任务归属 |
| UserPromptSubmit | 提醒判断继续、补充、独立请求、原子工作或停止；不使用关键词自动判断意图，也不自动初始化 |
| PreToolUse | 对明确操作读取状态并决定静默、提醒或可选拒绝；Task Progress 自身及恢复操作始终放行 |
| PostToolUse | 先观察 Task Progress 回执，再处理其他工具的结果；工具副作用已发生，普通提醒不替换/隐藏原始结果 |
| SubagentStart | 有明确派发关联时给精确节点；否则只提示沿用派发上下文中的身份。不能阻止子代理启动，不把宿主 session_id 当插件 UUID |
| Interrupt | 只记录中断观察，停止本轮 hook 续跑资格；不取消实际任务、不阻止中断 |
| Stop/SubagentStop | 默认不注册续跑逻辑，见下文 |
| PreCompact/PostCompact | 不承担上下文恢复；使用 SessionStart(compact) |
| Pre/PostModelRequest | 不用于提醒；这两个事件的输出仅供诊断 |

入口提示约 150–250 tokens；过程提醒约 40–100 tokens，最多给出当前目标引用、具体缺口和下一动作。SessionStart 与同轮 UserPromptSubmit 合并开工信息；若没有可确认的相同 turn 关系，优先精简两者，不靠时间接近猜测同轮。

### Stop 的保留方案

Stop 发生于 turn 结束，并不表示任务完成。它的 block 会请求自动续跑，不能复用普通 additionalContext 回调来做收尾门禁。

前两期维持关闭。后续若单独启用，必须同时满足：精确关联、确定存在可立即处理的漏收尾动作、没有真实运行/合法等待、没有用户中断或停止、stop_hook_active 为 false，并且本轮/attempt 未续跑过。第二次直接放行。无法可靠识别用户停止时，不启用自动续跑。running 子任务、排队任务或仍未解决的目标本身均不是续跑依据。

## 6. 关联来源与数据模型

关联只说明“宿主在这个作用域观察到某次 Task Progress 调用及其目标”，不认证调用者声明的角色、模型或文件权限。

| 来源 | 可记录的事实 |
| --- | --- |
| 真实 hook 事件 | 实际提供的 host_session_id、turn_id、agent_id、tool_use_id、事件名 |
| 成功服务端回执和新鲜状态 | 插件 session/instance/epoch/task/attempt、状态和 revision |
| MCP 参数中的 assignment、agent_name、thread ID、model | 调用方声明，保留 caller_declared |
| cwd、最新状态文件、唯一 running 节点、transcript | 不作为自动选择当前任务的依据 |

agent_id 缺失保持 unknown；不据此认定主会话。只有 host_session_id 相同也不够：子代理可能共享父级 session ID。工具事件未保证可确认的 actor 时，关联的覆盖范围必须标为 uncertain，不能给整个父会话套用某个代理的 active attempt。

建议结构如下，字段名为方案接口，尚不存在于当前产品：

~~~json
{
  "schema_version": 1,
  "binding_id": "...",
  "binding_revision": 4,
  "host_scope": {
    "session_id": "...",
    "turn_id": "...",
    "agent_id": null,
    "actor_resolution": "unknown"
  },
  "foreground_anchor": {
    "session_id": "...",
    "task_instance_id": "...",
    "epoch": 2
  },
  "active_work": {
    "task_id": "...",
    "attempt_id": "..."
  },
  "pending_dispatches": [],
  "observation_source": "host_observed_tool_receipt",
  "last_tool_use_id": "...",
  "last_state_revision": 42,
  "status": "active"
}
~~~

foreground_anchor、active_work 和 pending_dispatches 分开。父会话给子任务启动 attempt 时，记录待派发项；不能因此覆盖父会话自己的工作位置。多个并行实例也不能争用“最后一次调用就是当前任务”的单槽。

### 正式工具回执的观察规则

| 正式调用 | 关联影响 |
| --- | --- |
| init_task_network/init_progress 成功 | 建立新的任务锚点；不等于已有运行 attempt |
| rebuild_task_network 成功 | 更新明确锚点的 epoch，旧 attempt 失效 |
| update_task/update_step 开始或 retry 成功 | 根据已校验的目标和上下文意图更新工作位置或待派发项；不能把声明角色升级为宿主身份 |
| bind_task_agent 成功 | 将明确待派发项与正式保存的 agent 标识对应；宿主子代理 ID 是否相同仍需适配器核验 |
| review_task/结束 attempt 成功 | 刷新关联目标状态，不自动启动后继 |
| route_request(queue/parallel) | 仅登记请求，不切换父会话当前工作 |
| activate_request 成功 | 明确切换 foreground 锚点；旧引用保留失效记录 |
| get_progress、记录查询 | 核验已有引用；任意查询不隐式切换当前工作 |
| render_progress | 不改变任务选择，不因观察到它再次展示 |
| clear_progress 成功 | 依据现有墓碑使相关绑定失效，迟到回执不能复活 |

观察器只接受精确注册的 Task Progress 工具名、已支持的 receipt_version、成功回执和有效目标状态。错误响应、异常文本或结构不明不建立/推进绑定。hook 必须观察 progress 的 Post 事件，但不在该事件上再输出普通提醒。

## 7. 建议接口与存储

优先复用现有回执和状态投影，避免要求每次工具调用前额外维护一套状态。

### 7.1 版本化回执

为正式小回执增加 receipt_version 与 operation，并为需要区分意图的操作返回有界 context_effect，例如 anchor_changed、work_started、dispatch_prepared、attempt_ended。字段由服务端根据已接受的状态转换生成；参数中声明的角色仍保持未认证。

旧回执无法可靠适配时继续正常返回给模型，观察器仅将关联状态置为 unsupported，不从自然语言“已记录”推断成功。

### 7.2 一个显式选择入口

新增 `select_work_context` MCP 工具，支持恢复已有任务时显式建立 actor-scoped 关联。它不接收宿主 session/turn/agent 字段：

- tracked：明确插件 session_id、task_instance_id、epoch，以及可选 task_id/attempt_id，purpose 为 work、dispatch 或 resume；服务端核验目标并返回 context_selection 回执。
- atomic：记录 summary、具体 atomic_reason 和允许的工作类别，返回 work_ack_id；不制造假任务树或打开 UI。由 hook 观察到该回执后仅对当前可确认 turn 生效，下一轮需重判。

该入口表达调用者选择，不能证明粒度判断正确或扩大操作授权。纯文本问答不需要调用。常规 init/start/activate 回执已能无歧义建立关联时，无需额外 select。

### 7.3 本地状态 helper

新增轻量模块，统一 state_paths、锁、只读投影、回执适配和关联索引；hook 不直接导入会初始化服务的 server.py。

本地 read_hook_snapshot 返回固定小投影：

- binding_status、actor_resolution、目标身份及 state_revision；
- planning_status、attempt 状态、dispatch/review 状态；
- stage_inventory_due、split_review_due、是否可综合验收；
- reason_code、一个下一动作及稳定 issue_fingerprint。

不返回完整树、历史、正文或任务附件。任务状态从服务端相同规则和锁读取；不复制一套 ready、outcome 或阶段盘点算法。小投影直接计算所需字段，不先生成 full 再裁剪；单次投影只预计算一次阶段处置等共享信息。

绑定索引放入服务端状态根目录下的 hook-contexts；路径解析从当前 CACHE_DIR 规则抽成共用 helper。键使用宿主作用域摘要，不把原始 ID 拼入路径。提示缓存与任务权威状态继续分开；清理提示缓存不改变任务结果。

### 7.4 并发与迟到回执

- 每个绑定使用锁、binding_revision 和 CAS；sidecar 写入采用原子替换。
- 不要求任务 JSON 与 sidecar 同时原子提交。任务事务先完成，观察器随后更新引用；每次读取重新核验目标，允许短暂 stale，不把 sidecar 当准入真相。
- 同一插件 session 的回执按 state_revision 丢弃迟到状态；跨实例不可比较 revision，必须使用明确的选择操作及其宿主观察顺序。
- 工具事件用 tool_use_id 关联 Pre/Post，重放幂等；失败调用不提交选择。
- 多个 dispatch 独立按 task/attempt 保存。只在精确匹配时把 SubagentStart 的 agent_id 与正式派发项关联，不能按“最近启动的唯一代理”猜测。
- 子代理可能先于父侧 bind 完成启动。过渡期给获派范围的条件式提醒；不借用父级 active_work，不让父侧绑定操作被门禁拦住。
- 无嵌套锁的固定读取流程：读取绑定快照 → 在目标 session 锁内读权威状态/墓碑 → 重验绑定 revision；中途变化则重读一次，仍不稳定则 unknown。
- 这里仍存在 hook 检查与实际操作之间的时间窗口，只能承诺调用前状态检查。原子执行保证需要宿主调度或实际执行入口配合。

## 8. 状态感知提醒

| 权威结果 | 提醒/动作 |
| --- | --- |
| unbound | 使用已有任务恢复或明确选择；不能说“你没有计划”，也不自动初始化 |
| unknown/unsupported | 静默或一次状态未知提示；不解释为缺计划 |
| planning_required | 提示补粗阶段或明确原子理由 |
| ready 但无 running attempt | 提示选择正确 ready 叶并开始 |
| running 且无缺口 | 静默 |
| dispatch 未绑定且实际 spawn 成功 | 提示 bind 正式返回的 ID |
| pending submission | 对有精确关联、明确选择主会话职责的上下文提示查询证据并 review；职责仍是声明，无法确认时用条件句；子代理只提示已提交待验收 |
| stage_inventory_due | 提示阶段盘点；不把 covered/obsolete 当完成 |
| split_review_due | 提示拆分或唯一剩余结果的复核；不终止真实外部运行 |
| 子任务已结束且父级可综合 | 提示主会话 synthesis |
| epoch/attempt 失效、cleared | 提示重新读取身份；不按“最新任务”自动迁移 |
| 工具返回句柄/queued/partial | 保持运行事实；不提示或写入成功结果 |
| 命令终态 | 提醒核验/记录实际回执；命令成功不自动解决任务 |

new submission、终态等只有在适配器有公开结构化证据时才能精确识别。其他工具只提供条件式提示。对于已经由正式 API 登记的命令终态，Post 不再重复要求登记。

## 9. 去重、限频与故障预算

分为两层：

1. 事件重放键：host scope + turn_id + tool_use_id + hook_event_name + policy_version。SubagentStart 用实际 agent_id；入口事件按其实际可用标识处理。
2. 提醒键：host scope + turn_id + reminder_kind + 目标 instance/epoch/attempt + issue_fingerprint。

issue_fingerprint 只包含与该问题有关的状态，不使用每次变化的全局 revision 直接重置提醒；否则任意进度写入都会重复提示。问题修复后静默；retry 换 attempt、计划换 epoch 或新提交可形成新问题。

默认预算：

- 入口一条，通用工作一条；编辑和命令共享通用工作预算。
- spawn 前指导一条、成功后缺绑定一条；followup 默认只在前置缺口时提醒。
- wait 超时和运行中为零条。
- 确认的 pending review、失效身份、阶段盘点按问题各一次，不被不相关查询耗尽预算。
- 同一缺口仍未修复时，用有界冷却提醒，建议不短于 10 分钟；不在每次工具轮询重复。

缺稳定 turn/actor 或缓存不可写时，不把高频通用回调退化成“每次输出”。保留启动和 SubagentStart 的短提示；高频语义提醒静默，诊断标为 dedup_unavailable。去重缓存仅证明发过提示，不能证明做过计划、绑定或验收。

## 10. guided 与 required

guided 默认启用，只输出提醒。required 为每个工具范围单独启用的状态纪律检查，不能被描述为身份授权或完整执行审计。

required 需要通过真实宿主验收：hook 已发现/信任、主体关联可确认、工具事件与拒绝语义有效、准备操作有清晰放行路径。初期范围只考虑直接 apply_patch 和已经实现精确适配的启动/写入接口；任意 Bash、未知 MCP 和包装器不纳入门禁承诺。

对已精确识别的主体和操作，拒绝原因只允许来自当前确定状态：

| 原因 | 恢复动作 |
| --- | --- |
| work_context_required | 先恢复/选择已有任务，或初始化粗计划，或确认真实原子工作 |
| planning_required | 完成粗规划 |
| attempt_required | 启动正确 ready 叶 |
| attempt_stale | 重新查询 instance/epoch/attempt |
| submission_review_required | 由主会话查询并验收前驱结果 |
| split_review_required | 完成正式拆分复核后再继续 |

work_context_required 表示该作用域未确认执行上下文，不声称用户没有任何计划。actor 不明、关联有歧义、状态不可读、schema 不支持或超时均降级为 guided；不据未知事实拒绝。要承诺“首个编辑前检查”，必须额外验收新 turn 的主体关联和上下文确认流程，不能只测试已有绑定的场景。

Task Progress 正式工具、显式上下文选择、发现与规则读取、已适配状态查询、既有执行轮询、取消、用户澄清及如实收尾始终放行。没有结构化只读适配器的 shell 不加入“准备操作白名单”。

需要阻断时，使用 PreToolUse 的 permissionDecision=deny，给一个具体原因和恢复入口；不使用不受支持的 continue:false，不改写原工具参数。required 不授予提交、推送、安装或外部变更权限。

降级状态必须可诊断：configured_mode、effective_mode、降级原因、已验证工具覆盖、最近观察时间分别记录。hooks/list 或宿主报告才是发现/信任的来源；缺 heartbeat 不推断禁用，最近触发也不证明已加载预期版本。

## 11. 平台、恢复与配置

- hooks.json 为每个 handler 配置 POSIX command 与 Windows commandWindows；使用已有 launcher/.cmd，分别验收路径空格、Python 不存在和 timeout。
- hosted 工具及绕过本地 hook 的专用路径保持覆盖未知/不支持；不补造观察或强制保证。
- compact/resume 保留锚点并重查；fork 默认新作用域未绑定；host clear 撤销本轮执行选择而保留任务数据。
- foreground 激活、rebuild、retry 分别改变 instance、epoch、attempt；旧关联不静默重定向。
- clear_progress 使用已有墓碑，历史迟到 hook 不得复活关联或任务。
- hook 脚本/配置更新需要宿主重新信任，升级交付分别检查版本、发现、信任和真实触发。
- 关闭门禁仅改变后续 hook 决策，不改任务历史；可回滚到 guided，已有绑定作为审计记录保留。

## 12. 实施分期与文件归属

| 阶段 | 具体交付 | 完成条件 |
| --- | --- | --- |
| A：事件纠偏 | 拆 dispatch/followup/wait/query，缩短入口文案，共享限频预算，缓存故障降噪，Windows override | 等待不误触发绑定；没有通用 Post 洪泛；平台回调可实际触发 |
| B：状态感知 guided | 共用状态 helper、版本化回执、正式工具 Post 观察器、绑定索引、小投影、显式选择入口 | 能定位当前真实缺口；恢复/并行/迟到回执不串任务；未知状态可诊断 |
| C：可选 required | 精确工具适配、准备操作放行、窄范围拒绝与恢复、能力覆盖报告 | 新 turn/子代理/故障/中断的真实宿主矩阵通过后按范围启用 |
| 后续候选 | 有界 Stop/SubagentStop 收尾提示 | 独立验收，不作为 A/B/C 的前置，不默认自动续跑 |

建议文件拆分：

- hooks/hooks.json：事件与平台命令配置。
- hooks/task_progress_hook.py：有界入口、事件分发和输出格式。
- hook_runtime.py（新增）：分类适配、回执观察、提醒决策与诊断。
- state_paths.py、hook_context.py（新增）：共享路径、关联索引、锁和状态小投影；只复用权威状态语义。
- server.py：版本化回执、选择入口及共用投影调用；不把 hook 诊断写成任务 outcome。
- skill/startup、execution-lifecycle、发布说明：同步模式与覆盖范围。

按文件 owner 顺序集成：先统一 helper/回执契约，再改 hook 决策，最后文档及验收；共享 server.py 不并行覆盖。

## 13. 验收矩阵

以下场景仍是实现验收矩阵；截至本轮尚未运行测试或真实宿主验收。

| 场景 | 必须观察到的结果 |
| --- | --- |
| 新请求首次编辑 | guided 一条具体提示；已验证 required 范围内缺上下文则拒绝，补规划/attempt 后可恢复 |
| 简单问答、读取skill、工具发现 | 无假任务树、无多余门禁 |
| spawn 成功/失败 | 先 start，成功后 bind；失败不绑定，不把尝试写为成功 |
| followup 追加与补充现有工作 | 沿用真实 agent；不凭工具名强制新 attempt |
| wait 超时、仍运行、多个通知 | 默认静默，无新 bind 或完成判定 |
| 子任务新提交 | 精确一次提示，主会话 query/review 后才放行依赖 |
| 父级 main leaf 与多个 subagent 并行 | 待派发项不覆盖父工作位置；主体不明时降级，不串写 |
| ready/无attempt、pending review、盘点/拆分到期 | 每个真实缺口一次；解决后不重复旧提醒 |
| Bash 运行句柄、非零退出、queued | 运行与终态区分，不自动解决任务或中断实际命令 |
| 正式工具成功/失败/旧schema | 成功回执才观察；失败/旧schema不解锁、不从文本猜状态 |
| 任意查询其他实例 | 不切换当前工作；route parallel不覆盖foreground |
| 并发/重放/倒序Post、rebuild/retry/clear | 幂等，旧epoch/attempt和墓碑阻止迟到复活 |
| resume/compact/fork/host clear | 沿正确锚点恢复，不继承父级可写归属、不清任务数据 |
| 缺agent/turn、cache不可写、状态损坏/锁超时 | 高频通用提示不洪泛；unknown/降级可见，工作可恢复 |
| 用户中断或明确停止 | 不阻拦、不续跑、不假取消外部进程 |
| code mode嵌套调用、hosted工具 | 记录实际覆盖，不把未触发当已保护 |
| macOS/Linux/原生Windows | 路径含空格、Python缺失、timeout和重新信任有实证 |
| 普通更新与面板关闭 | 不再render或抢焦点 |

验证分三层：纯决策/适配与并发回归；隔离状态下的服务端绑定/墓碑测试；真实 Codex 的触发、信任、拒绝及恢复样本。模拟事件通过不能替代真实宿主验收。测试遵循项目 dagu-test-runner 路由，优先最小相关范围。

建议记录指标：首次实质操作前确认率、同一缺口重复提示数、误拒绝数、恢复成功率、按工具的实际覆盖，以及 hook 延迟。阶段 A/B 的目标是减少遗漏和噪音；阶段 C 才对明确覆盖的范围报告调用前检查效果。

## 14. 当前实施记录

- 阶段 A：实现精确事件分类、等待/轮询静默、dispatch/followup 分离、事件重放去重与语义提醒预算、失败或身份缺失时高频回调静默；每个已注册 handler 配置 Windows `.cmd` launcher。
- 阶段 B：成功 mutation receipt 增加 `receipt_version=1`、`operation`、服务端推导的 `context_effect`；`read_hook_snapshot` 在现有 session lock 内按显式插件身份读取有界 projection；`hook_state.py` 延迟加载只读 reader。
- 集成：hook 只在 PostToolUse 的成功 Task Progress 回执上更新本地 actor 关联；`select_work_context` 可为已有实例显式建立 actor-scoped 选择，并校验当前 session/instance/epoch/task/attempt；成功 dispatch 必须返回结构化 `agent_id` 且存在唯一待派发项才建立 child scope 映射。相同 state revision 的上下文选择以 `view_sequence` 防止迟到覆盖。attempt retry 回执同时携带新执行模式，恢复新 attempt 的 active 或 pending-dispatch 状态并撤销旧 attempt 映射；child 的终态回执通过哈希父 binding 索引撤销 parent 侧映射。`SubagentStart` 仅对 snapshot 中仍为 running 的目标注入插件任务 ID。状态关联只证明宿主观察到的工具/回执事实，不认证调用者角色。侧车位于 `~/.cache/task-progress/hook-contexts`，身份仅用于哈希命名；写入使用本地锁和原子替换。
- 汇总/待验收触发：实例级只读 hook snapshot 返回最多 20 项 pending-review 与 synthesis-ready 标识及总数。synthesis-ready 只包括仍为 pending、依赖已满足且所有子项已结束/验收的父节点；父节点自身已终止、已接受 closure、根级已移交关闭，或当前盘点标记为 obsolete/covered 时不重复提醒。父 actor 在 `PostToolUse` 等待子代理返回后、以及下一次 `UserPromptSubmit` 获取提醒；按首个待办的 task/attempt 指纹限频。派发 child 的 sidecar 保留 actor scope 到当前被派发任务，避免 child 的提交回执改写关联来源后收到实例级父级验收提醒。提示只要求查询当前 submission/evidence 后复核，不自动收尾。没有精确绑定或状态不可用时不推断。
- 定向验证：`test_response_protocol.py` 16 项、`test_hook_context.py` 12 项、`test_hook_runtime.py` 7 项、`test_stage_inventory.py` 16 项、`test_handoff_closure.py` 12 项通过；三个 Python 模块 `py_compile` 和 `git diff --check` 通过。独立复核未发现 P0–P3；真实 Codex/Windows 宿主事件仍需宿主验证。
- `hook_state.py` 按需从本地 `server.py` 加载只读 snapshot 函数，尚未抽为设计中建议的独立共享状态模块；Windows 实机加载、宿主 `tool_response` 形态、不同 actor/turn 的真实触发和缓存权限仍需真实宿主验收。required 门禁继续关闭。

## 15. 依据与范围

- [当前 hook 实现](../../plugins/task-progress/hooks/task_progress_hook.py)
- [当前 hook 配置](../../plugins/task-progress/hooks/hooks.json)
- [当前服务端状态与回执](../../plugins/task-progress/server.py)
- [既有启动可靠性设计](2026-10-27-task-progress-startup-reliability.md)
- [官方 Codex Hooks 文档](https://learn.chatgpt.com/docs/hooks)，2026-11-06 核对；发布文档是事件行为依据，未以 GitHub main schema 推断当前支持。

实现额外覆盖了待验收汇总提醒及服务端/child actor 的资格边界。required 门禁未启用；此设计记录不宣称宿主触发或安装运行时已验证。
