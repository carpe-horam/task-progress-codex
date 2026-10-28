# 计划与实际执行脱节：控制边界及可实施方案

日期：2026-10-28（Asia/Shanghai）。性质：只读研究与方案设计，未实施、未安装、未启动测试聊天或模型 turn、未更改真实业务节点。本报告只新增此文件。

研究基线：当前 `codex/fix-thread-state-compat`，HEAD `8a9b6dc4e2d18fa1bcc03350859ece364d4ee6a2`，含未提交的会话状态修复；旧设计固定为 `v2` 提交 `247071efdf0007508c332a5bed4a07f7bba0ee68`。未切换分支。当前交付状态以 [本次会话状态修复报告](2026-10-28-thread-state-fixes.md) 为界，旧审计不自动等于当前缺陷。

## 1. 结论

插件可以把“下一步该做什么”变成可领取、可恢复、可核对的动作，并拒绝插件内的错误状态转换。这能明显减少漏绑定、漏验收、重复归档、旧结果覆盖和多执行者抢同一动作。它仍不能单独保证主会话在调用其他工具之前先领动作，也不能仅靠自己发出的提醒让一个闲置主会话继续执行。

短期可直接扩展插件hooks，接入Codex已有的工具前后回调，在受支持且已信任的路径上做局部强制、回执同步及结束复核，无需先重建一套宿主。当前插件尚未接这些回调。完整工具覆盖、故障时保证不放行、持久执行事件和闲置会话自动唤醒则仍需更深宿主集成；仅有协议定义、定时器或更强硬的文案不等于拥有控制权。

建议增量吸收 v2 的动作、回执、恢复机制，保留当前已修复的职责、独立复核、历史和界面语义。整版回退 v2 无法消除宿主缺口，还会重新引入迁移与兼容验收成本。

## 2. 已核查事实及证据强度

| 事实 | 来源 | 能说明什么／不能说明什么 |
| --- | --- | --- |
| 当前 hooks 只有 `SessionStart` 和 `UserPromptSubmit`，前者匹配 startup/resume/compact/fork，命令超时2秒 | `plugins/task-progress/hooks/hooks.json:4–28` | 配置存在；不能据此宣称当前聊天已信任或实际触发 |
| hook 输出是 `additionalContext`，解析/OSError返回0，明确 fail-open | `hooks/task_progress_hook.py:19–74` | 是提醒入口，没有读取账本、领取动作或阻断工具的实现 |
| 当前 `next_actions` 是按节点遍历生成的字符串列表 | `server.py:881–913` | 会提示绑定、等待、复核、综合、启动；不是持久化队列，也没有认领权或租约 |
| 当前更新检查依赖、attempt、计划执行角色、待复核保护、父级综合；subagent结束进入待复核 | `server.py:1191–1299` | 插件数据转换有硬检查；声明角色仍来自调用参数 |
| 身份/模型来源明确标为未获宿主核验，agent绑定同样如此 | `server.py:435–443,1345–1366` | 有审计来源；不构成角色隔离、真实执行证明或主会话授权凭证 |
| 共享服务是带认证的loopback MCP请求服务，业务隔离仍依赖显式session_id | `shared_service.py:1–7,31–41,80–95`；`server.py:2618,2680` | 认证可保护本机端口；共享bearer不证明调用者属于哪个thread、角色或attempt |
| 本次真实 `get_progress` 可返回当前实例、attempt、revision和下一动作 | 本研究的真实插件读取 | 证明当前读取链路可用；不证明遗漏的工具调用已自动被观察 |
| 本机CLI 0.147.0生成的协议含item/turn事件、thread读取和动态工具 | `/tmp/task-progress-repair-20261028/codex-schema/`，下节给出具体定义 | 是协议能力证据；本次未验证桌面会话事件可被本插件订阅 |

源码行号针对上述未提交工作区快照。已有修复报告中的167项Python和59组浏览器回归是此前验证记录，本研究没有重新运行，也不把它们当成新方案已通过。

### 本机协议可以支撑什么设计

本次直接读取生成的JSON Schema，而非凭搜索摘要推断：

- `ServerNotification.json` 的 `HookEventName` 含 `preToolUse`、`postToolUse`、`subagentStart`、`subagentStop`、`stop` 等；这不是当前插件已注册这些hooks的证据。
- `v2/ItemStartedNotification.json` 和 `v2/ItemCompletedNotification.json` 包含 `threadId`、`turnId`、item和生命周期时间；`ServerNotification.json` 定义 `item/started`、`item/completed`、`turn/started`、`turn/completed`、`thread/compacted` 等消息。
- `ThreadItem` 可表示command、file change、MCP、dynamic tool、协作调用。协作项包含 `senderThreadId`、`receiverThreadIds`、`agentsStates`；`model`和`reasoningEffort`的说明是“requested”，不能冒充实际模型运行配置。
- `v2/ThreadReadParams.json` 的 `includeTurns` 可要求rollout中的turn/items；`thread/turns/list`、`thread/items/list` 可作为恢复接口候选。跨连接、桌面现有聊天可见性和完整性未验证。
- `v2/ThreadStartParams.json` 的 `dynamicTools` 和 `DynamicToolCallParams.json` 的 `threadId/turnId/callId/tool/arguments`，可供自己管理的app-server客户端实现执行适配器。它只路由注册的动态工具，不会自动代理所有内置工具或现有桌面聊天。
- `ClientRequest.json` 含 `turn/start`、`turn/steer`、`turn/interrupt`。协议存在不自动授权插件往别的聊天发消息或自动创建turn，也不证明此共享MCP服务已拥有桌面进程的连接。
- `ServerRequest.json` 的command/file/permissions approval请求属于授权流程，不能直接当通用任务调度门禁；并非每种操作、每种权限模式都会请求批准。

本机协议核查止于静态定义。官方[app-server文档](https://learn.chatgpt.com/docs/app-server)亦说明客户端流式接收item生命周期、处理实验性动态工具请求；仍不能把独立CLI客户端与当前桌面app-server视为同一控制通道。

### 官方hooks能力与当前安装要分开

已打开[官方Hooks正文](https://learn.chatgpt.com/docs/hooks)，核实：Pre/Post覆盖Bash（含exec_command）、apply_patch、MCP及多数本地函数，spawn_agent别名Agent；code mode内部调用同样受回调约束。Pre可显式deny或改写输入，Post不能撤销副作用。hosted工具无覆盖，specialized路径可例外；write_stdin不再触发Pre。hook异常、超时或格式错误可能放行，必须实测。SubagentStop有agent/turn标识；Stop可block使现有turn继续，stop_hook_active用于防循环。这些均尚未在本次插件现场验证。

因此短期最有效的宿主接入就是插件hook扩展，并非自建新宿主。宿主已提供接口，插件尚未使用，是工程缺口；部分路径不覆盖、故障可能放行，是机制边界。Stop只能在结束边界检查遗漏，不能保证恢复所有闲置/退出聊天。

## 3. v2 中可吸收的机制与缺口

来源通过 `git show 247071ef:plugins/task-progress/task_progress_v3.py` 和同提交的设计文件读取；以下均未运行其代码。

| 机制 | v2源码位置 | 评价 |
| --- | --- | --- |
| `task_advance(preview/claim)`，事务内去重活跃动作并签发60秒lease；ORCHESTRATE/SYNTHESIZE预建attempt | `task_progress_v3.py:352–387` | 值得吸收；将“读到候选”与“获得处理权”分开 |
| 动作分类PLAN、ORCHESTRATE、COLLECT、SYNTHESIZE、DISPATCH | `:389–402` | 降低主会话自行拼协议的负担；当前实现按遍历顺序生成，不能直接视为设计要求的全局优先级调度已实现 |
| request_id、请求hash与持久回执；scope/expected_revision检查 | `:245–265` | 值得吸收；防响应丢失导致重复提交。全局revision用在并行报告上会带来无关冲突，需按实体版本划分 |
| dispatch_begin再dispatch_confirmed；失败可能为unknown，另有reconcile_result | `:532–585` | 值得吸收；本地事务不能与真实spawn原子提交，unknown不能盲目重试 |
| 主会话 `main_session_transition` 开始/结束使用同一action、attempt | `:590–617` | 补上主会话自身执行的账本空白；仍是调用方填报，不等于自动捕获执行 |
| 两阶段主会话完成须lease仍有效；续租只接受未过期lease | `:586–596` | 长任务/压缩/断连后的恢复需专门设计，不能照搬后声称可无损恢复 |

尤其要复核两个静态风险：`main_session_transition`完成在lease已过期时被拒绝；`lease_renew`不复活过期lease，而 `task_report` 在进入续租分支前又要求能定位assignment（`:527–531`）。因此必须实测主会话长期执行/断连后怎样结算，不能只添加每分钟续租提示。应把控制权过期与外部执行观察分开：过期持有者不能发起新的副作用，但当前attempt的可信终态回执仍可进入对账流程。此为静态风险判断，未宣称已完成故障复现。

v2设计本身已写清宿主边界：`docs/design/task-orchestration-v2.md:15` 限定宿主执行循环活跃期间，MCP不会自行唤醒Codex或调用assistant环境中的 `collaboration.*`。其 `:258–280` 描述停止/对账、回收/综合优先，`:309–320` 区分租约和外部执行生命周期；这些是设计契约，不能用文档替代实现核验。

旧UX实际文件为 `docs/design/task-orchestration-v2-ux.md`。其中 `:13–18,113–117,140–142` 的整树同屏、计划顺序稳定、不因心跳重排、结束计数与成功分开，适合继续采用。它明确撤回四页签、降低信息密度的旧方向，不应借调度改造再次把清单藏起来。

## 4. 控制能力矩阵

| 控制 | 纯MCP领域服务 | 插件hooks接入现有宿主（可短期实施） | 仍需更深集成／限制 |
| --- | --- | --- | --- |
| 动作领取 | 原子领取、去重、fence、版本快照 | Pre核验当前动作，再放行匹配工具 | 未覆盖路径仍可绕过；调用身份映射须实测 |
| 租约 | 处理权续期、陈旧控制写入拒绝 | 工具前后事件辅助判定活跃/终态 | 时间不能证明进程已结束，断连需对账 |
| 执行同步 | 保存声明及来源、缺回执/矛盾 | Post保存真实tool_use_id及结果，SubagentStop辅助回收 | hosted/例外路径与丢失回调需事件流补偿 |
| 恢复检查 | 输出scope和未结算动作 | SessionStart/compact回调与Pre检查恢复凭证 | 缺失/失效回调不能保证恢复，不能猜身份 |
| 工具门禁 | 仅插件内硬检查 | Pre对已验证路径返回显式deny | hook失败可能放行；完整强制需受控执行网关 |
| 防过早结束 | 记录未完成/待复核 | Stop检查未结算项并有界续跑 | 非闲置聊天唤醒；不得无限block用户停止 |
| 主会话角色隔离 | 角色声明及审计 | 使用宿主提供的ID作绑定线索 | 子hook可能共用父session，完整角色认证仍需可信映射 |
| 无人值守执行 | 持久动作/outbox | 现有turn可由Stop续跑 | 宿主退出或闲置时须另有获授权唤醒入口 |

## 5. 推荐的最小架构

```mermaid
flowchart LR
  A[主会话或授权执行驱动] -->|读取/领取/提交| B[Task Progress控制服务]
  B --> C[(状态/动作/事件/回执事务存储)]
  C --> D[现有任务树与历史投影]
  A --> E[真实工具执行]
  H[插件hooks适配器：需信任和验证] -->|可信工具事件及恢复事件| B
  E --> H
  H -->|执行前校验范围与动作| B
```

第一阶段建设B/C/D与更短的动作协议；紧接着以插件hooks接H，先观测后对受支持工具启用Pre门禁、Post同步和Stop结束复核。A仍由现有主会话执行。第三阶段才为hooks覆盖外路径、故障关闭及持久事件补偿评估完整执行网关。每层都独立验收和声明覆盖，不将当前提醒实现说成已完成工具闭环。

最小新接口可以只有三类：

1. `next_action(mode=preview|claim)`：同时返回一个建议主动作、待处理异常和并行工作摘要。持久动作含scope、action_id、attempt_id、input_refs、allowed_operations、fence、expires_at；同一动作和输入指纹不重复创建。
2. `record_event`：保存意图、开始、真实回执、观察、提交、复核、挂起、恢复；调用方声明与宿主事件不可互相覆盖。业务结果仍须现有独立复核。
3. `recover`：读取快照和未确认动作，输出对账项及唯一建议动作；默认只读，不因恢复归档、重试或接受结果。

优先级建议：处理停止/未知副作用 → 补绑定/回收报告 → 独立复核 → 综合验收 → 新工作。不是“任何待验收存在就阻断所有无关任务”；依赖独立且不争用资源的工作可以继续。动作顺序与界面计划顺序分开，不能为了表示调度优先级让任务树不断跳动。

旧MCP写入口若保留，必须最终调用同一领域事务，不能成为绕过claim的后门；迁移期可先允许legacy调用并显式标为未受动作协议保护。不要用两个可写真相源维持同一任务。

### 事件与回执的建议契约

以下为设计示例，字段不是当前插件已提供的能力：

```json
{
  "event_id": "uuid",
  "request_id": "stable-id-for-retry",
  "scope": {
    "installation_id": "uuid",
    "session_id": "uuid",
    "task_instance_id": "uuid",
    "epoch": 0,
    "task_id": "inspect",
    "attempt_id": "uuid"
  },
  "action_id": "uuid",
  "fence_generation": 3,
  "expected_entity_revision": 7,
  "kind": "tool.completed",
  "source": {
    "kind": "host_adapter",
    "trusted_source": true,
    "issuer_id": "registered-adapter",
    "connection_epoch": "uuid",
    "source_event_id": "stable-provider-id",
    "source_sequence": 152,
    "thread_id": "host-thread",
    "turn_id": "host-turn",
    "call_id": "host-item"
  },
  "observed_at": "2026-10-28T12:00:00+08:00",
  "payload": {"status": "succeeded", "result_ref": "content-addressed-ref"}
}
```

`trusted_source`由服务端按受认证的入口和issuer登记计算，禁止普通MCP参数自行置true。插件command hook可通过独立受限入口写入，但仍须设计凭证隔离，不能仅靠JSON自报来源。`kind=model_report`只能获得 `claimed` 级别；共享HTTP bearer只证明持有本机凭证，不自动提升为宿主来源。部署可从独立socket权限/配对凭证开始，但真正可信的thread映射仍须宿主给出。

服务端回执应为：`receipt_id`、request_hash、accepted/replayed/rejected、scope、applied_entity_revision、state_revision、event_seq、causal_event_refs、next_action摘要。同request_id同内容返回原回执；同ID异内容冲突；请求回执丢失先查询/重试原ID。并行独立叶使用实体revision，范围修改/重规划使用全局revision；快照排序保留当前state_revision，不混同执行事件序列。

事件只证明对应事实：`tool.completed`并不证明整个节点目标解决；`worker.final`不等于父级已验收；`turn.completed`不等于task完成。command退出码、MCP isError、异步run标识必须按执行器适配，不将一次“提交成功”当作长任务终态。

## 6. 断网、超时、恢复及门禁策略

| 故障窗口 | 应有行为 |
| --- | --- |
| claim响应丢失 | 原request_id重试或查receipt；不领第二个同范围动作 |
| 已领取但effect未开始就崩溃 | lease到期可重新领取；保留原attempt的prepared/abandoned记录，不造执行结果 |
| effect_begin已保存，真实spawn响应丢失 | 标unknown/reconcile_required，查询provider；未证实未创建前禁止重复spawn |
| 工具成功，回填前断连 | 宿主outbox重送同source_event_id；幂等接收；没有可信来源时保留未确认并由负责人核对 |
| 长命令等待超过lease | 不取消外部执行、不释放占用；旧fence禁止新副作用，可信终态允许结算原attempt或进入对账 |
| compact/resume/服务重启 | 先取得当前scope、最后消费序号、未结算动作；补拉缺口，再作新决策 |
| 事件乱序/迟到 | 终态不回退到running；旧attempt只入历史；矛盾保留并触发对账，不择一静默覆盖 |
| 无可信事件/宿主不支持查询 | 显示状态待确认和最近观察时间；不能按经过时间推定成功/失败/取消 |
| 用户改变目标 | 保存目标修订与来源，先收束在途副作用；同目标加范围不新建实例，真实新目标才归档 |
| 插件不可达 | 可用明确受限模式继续只读诊断；完整执行网关应失败关闭并给出恢复入口；插件hook模式须承认宿主可能在hook失败时放行，另报保护降级，不能伪称此模式故障关闭 |

恢复不等于自动继续。用户授权仍有效且目标未取消时，驱动可以继续已授权工作；缺少外部变更授权时，调度资格不增加授权。失联后也不能为了“修好列表”自动结束真实业务节点。

插件hooks门禁应先观测再分范围启用：先验证command、file change、MCP、协作和specialized/hosted路径分别覆盖到哪里，记录实际覆盖矩阵。放行条件是当前可信scope、有效动作、匹配attempt和操作类别；读取账本、恢复和诊断必须有防死锁入口。计划审查不能靠任意shell命令是否包含某字符串判断，否则包装命令、函数编排和嵌套tool会绕过或误拦。若能控制的只是某一组工具，产品应明确称“该组工具受正常回调约束”，不能称“主会话所有行为被强制”。Stop只在有未结算且可推进动作时进行有限续跑，已触发stop_hook_active、用户中止或同一冲突重复时转为具体状态报告，避免循环续跑。

## 7. 不建议采用的替代方案

- 仅增加skill/hook措辞：可以提高遵循率，不能成为机械强制。当前两类提醒甚至不在每次工具执行边界触发。
- 用UI轮询或10分钟提醒作执行心跳：只证明读取活跃；不会产生真实执行回执，也不能唤醒模型。
- lease过期自动标失败、取消或重新spawn：会把控制平面失联错误地当成执行平面结束，可能产生重复外部效果。
- 给普通MCP参数增加 `verified=true`：仍由调用者自报；没有独立信任入口就没有认证提升。
- 让插件直接调用 `collaboration.*`：这些工具属于assistant宿主，不是当前MCP进程自动拥有的Python函数。
- 读取rollout文件就声称实现实时门禁：历史可用于补证和恢复，工具开始之后才看到记录不能阻止该操作；文件格式、保留和可见性也需适配。
- 创建另一个app-server监听“所有聊天”：新的客户端不自动接管桌面连接；还涉及用户授权、身份映射和数据范围。协议存在不证明可用。
- 整版回滚v2：动作框架值得复用，未验证的迁移、租约恢复、角色真实性和宿主执行循环不会因此消失。

## 8. 分阶段验收指标

以下是建议阈值，不是已测结果。

| 阶段 | 必测指标及建议通过条件 |
| --- | --- |
| 插件内动作协议 | 并发领取同一动作仅1个有效fence；重复请求不新增attempt/副作用意图；旧scope/attempt写入100%拒绝；所有待绑定/待复核/可综合状态都有结构化动作 |
| 恢复可靠性 | 覆盖claim、begin、tool-return、report、review五个断连窗口；已开始且结果未知的外部效果重复派发数为0；旧结果污染新attempt数为0；所有恢复都显示明确未确认项 |
| 主会话实际遵循 | 受控同类任务统计实质工具调用的动作绑定率、孤立调用率、漏绑定率、待复核积压时长；设基线后比较，不能只用接口单测宣称改善 |
| 宿主事件适配 | 每个声明支持的工具路径均有start/terminal关联；重复、乱序、断流恢复不丢事件；事件接收到UI更新的p95建议小于2秒，并单列断连恢复时延 |
| 插件hooks局部门禁 | 正常有效回调下，受支持路径无scope/claim的调用阻止率100%；异常/超时/格式错误另测并报告放行率；code mode、spawn、write_stdin分别覆盖；恢复诊断无死锁，Stop无无限续跑 |
| 完整执行网关 | 受保护路径包括回调故障时，均不能绕过；未覆盖路径明确列出；无有效scope/claim的副作用调用阻止率100% |
| 清单可理解性 | 计划顺序不因心跳跳动；执行结束与已解决/待复核计数一致；测试者无需切页即可定位当前项、下一步、未知执行和缺验收；重复实例及误归档为0 |

纯MCP交付应表述为“减少协议遗漏并可审计”；插件hooks通过信任和真实路径验收后，可声明正常回调下相应工具的局部约束；完整执行网关通过故障验收后，才可声明覆盖范围内的强制遵循。
