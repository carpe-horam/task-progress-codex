# visual-diff 会话历史任务与当前状态审查

对象：Codex 会话 `01a16cc6-d9c6-7c60-8600-19166106f7a5`，标题“简化 visual-diff 界面体验 (2)”。审查日期：2026-10-28，时间均为 Asia/Shanghai。最终状态快照采集于 **10:23:16**，任务数据最后写入于 **10:21:43**，`state_revision=677`。会话仍在执行，本文是有时间边界的审查。

**结论：部分机制符合现行协议，但整个会话的状态维护不属于健康的正常使用。** 后端正确保留了待验收结果、阻止了未验收依赖、没有把总目标标为完成。主要问题来自历史存储分叉、持续目标被多次初始化、真实执行者结束后任务仍运行、派发记录与实际调用不符，以及主会话沿用旧式完成操作而没有进行独立验收。另外确认一处离线报告标签缺陷。

上一次简查将 `awaiting_review` 的汇总优先级直接定为缺陷，判断过重。当前源码明确规定待验收优先于运行项；它容易误导，但不构成状态计算与现行实现不一致，也不是依赖死锁。下面依据完整历史与宿主回执重新分级。

## 1. 审查范围与证据来源

- 通过 `read_thread` 读取父会话及三个实际执行者，核对运行、完成和失败工具调用；未向这些会话发送消息。
- 对当前插件 session `e30c494a-948a-4dc5-be03-4f14246bcadc` 的 15 个归档任务、当前任务及各轮历史做静态审查，共 32 个计划快照。
- 根据同一会话的真实初始化/调用记录，进一步定位两套较早 JSON session 和两套 SQLite session；合计 **26 个任务实例、42 个计划快照**。这不是全部业务工作均已验收的声明。[完整实例与轮次索引](2026-10-28-visual-diff-thread-state-evidence/history-index.json)
- 选择性读取该会话的三份本地 rollout 文件，只提取相关任务工具调用、派发配置和用户目标边界。记录内的内部续跑提示不作为新用户目标，旧 token、加密消息正文和其他会话数据不进入报告证据包。
- 当前工作区 HEAD 为 `8a9b6dc`。工作区、已安装 0.2.0 和实际 daemon runtime 的 `server.py` / `app.html` 哈希一致；skill 的工作区与安装副本也一致。[源码绑定](2026-10-28-visual-diff-thread-state-evidence/source-binding.json)
- 对照当前 `plugins/task-progress/skills/track-progress/SKILL.md` 与 `docs/responsibility-protocol.md`。旧 v3 的 `task_open/task_advance/task_decide` 记录按其历史格式理解，没有套用当前新增的 review 字段追责。
- 本次只增加审查文件。目标任务数据、业务代码、插件实现、安装及服务配置均未修改。后端测试与复现使用隔离数据。

## 2. 当前状态：哪些正常，哪些需要处理

| 指标 | 初始快照，revision 666 | 最终快照，revision 677 | 正确解释 |
|---|---:|---:|---|
| 叶任务 | 21 | 23 | 分组不进入分母 |
| 执行已结束 | 18 | 20 | 包含已提交但待验收的子任务 |
| 已解决 | 11 | 11 | 主会话直接完成或已被接受的 resolved 结果 |
| 待主会话验收 | 7 | 9 | 原始 submission 保留，节点 outcome 仍为 null |
| 运行中 | 2 | 1 | 最终正在执行真实 Docker PPTX 比较 |
| 可立即执行 | 0 | 0 | 只计算依赖已满足的 pending 叶任务 |
| 依赖阻塞 | 1 | 2 | 当前都受未验收前驱影响 |
| 整体状态 | awaiting_review | awaiting_review | 当前实现将待验收放在运行项之前 |

[前后快照与节点记录](2026-10-28-visual-diff-thread-state-evidence/live-summary.json)

以下行为符合现行协议：

1. `20/23` 是执行结束数量，不是完成率意义上的“20 项已验收”。11 resolved +9 pending review =20 ended，计数没有错误。
2. `remaining-sdk-optional-tools` 被 `remaining-neutral-image`、`remaining-app-modes` 的待验收状态阻塞；`runtime-ai-docker-call` 被 `runtime-host-auth-status` 阻塞。未验收 submission 连 `dependency_policy=finished` 也不能放行，这是设计要求。
3. `planning_status=planned` 表示完成粗规划，与最终交付状态无关。父/root 自身仍 pending，而子任务正在执行，是分组聚合模型的正常形态。
4. 只读“核实主机状态”可以提交 resolved，即使核实结果是主机离线；它完成的是调查目标，不能由此推断真实 AI 调用已具备条件。下游必须读取结果证据。
5. 旧归档缺少 `review`、`planned_assignment` 或宿主模型元数据，不能自动解释为验收失败或伪造完成，也不能补造历史 accepted 记录。

## 3. 确认的问题

### A. 历史跨 session、跨存储分叉，当前面板无法表达完整会话历史（高影响，兼容性与使用问题）

同一 Codex 会话至少使用过四个真实插件 session：

| session 前缀 | 存储 | 当前可见记录 |
|---|---|---|
| `d621a4c2…` | JSON/schema 2 | “移除远程主机页底部两个区块”，已完成，1 个实例 |
| `03024466…` | JSON/schema 2 | 5 个实例；最新“将 Runtime 注册简化为机器执行一次接入命令”仍 running，最后更新 10-24 18:25:52 |
| `03024466…` | SQLite/schema 3 | “简化 Runtime 主机注册流程”已 resolved，最后更新 10-25 21:20:18 |
| `f9f5aa88…` | SQLite/schema 3 | 素材预览、连接核验、主机注册共 3 个实例；最新已 resolved |
| `e30c494a…` | JSON/schema 2 | 当前主线，15 个历史实例加 1 个当前实例 |

`03024466…` 的两个存储不是同一个 task_instance：JSON 为 `19df4bc4…`，SQLite 为 `bf234d75…`。因此不能直接说同一条数据库记录自相矛盾；真正问题是**同一 session 的不同代际状态没有连接起来**。当前读取返回旧 JSON 的 running，而较新的 v3 完成记录仍在磁盘上。

当前 `server.py:109` 将 session 定位为 JSON 文件，`server.py:177` 的 `_read` 只读该文件。对 `f9f5aa88…` 调用当前 `get_progress` 得到无任务，但以 SQLite `mode=ro` 查询 `progress-v3.sqlite3` 可读到三个实际任务。[只读数据库证据](2026-10-28-visual-diff-thread-state-evidence/sqlite-v3-history.json)

这不是数据已删除的证据，也不能把旧 JSON running 当成现在还有执行者运行。它是当前插件不能覆盖本会话历史格式的兼容缺口；反复省略已有 session_id 初始化，又进一步把正常历史分散到不同面板。

建议：确定一个会话主索引，记录历史 session/存储来源；增加版本明确的旧格式读取或迁移路径。迁移需要保留原实例、原结果和来源，不能以当前同名任务覆盖旧记录。仅修 UI 总状态无法解决这个问题。

### B. 已结束子智能体曾继续显示 running，并混入主会话后续工作（高影响；审查期间已拆分，历史失真仍保留）

`remaining-vd-ownership` 的原 attempt 为 `2bdde88e-5443-4059-8d1f-2730a159f5bd`，09:46:19 启动。实际执行者 `/root/visual_diff_ownership`（thread `01a1802f-fcab-7a72-9017-8c36c5794017`）在 **09:47:41** 已提交最终答复并结束，仅做只读审计，说明旧记录不能安全迁移。

但到 revision 666，它仍被记录为 running，摘要仍写“开始执行”。随后主会话把自己完成的 Visual Diff Docker 上传验收写入同一个 subagent attempt；原 assignment、派发范围和执行者没有变化。[宿主完成回执](2026-10-28-visual-diff-thread-state-evidence/child-01a1802f-fcab-7a72-9017-8c36c5794017.json)，[父会话调用](2026-10-28-visual-diff-thread-state-evidence/recent-host-read.json)

这使卡片的执行者、工作内容、持续时间无法对应真实执行。插件不与 Codex 执行者生命周期联动，因此不能仅凭 `running` 证明工作还在进行。

审查期间，原会话在 **10:15:18** 分解该节点，旧 attempt 被保存为 cancelled；10:17:49 也分解了 `remaining-runtime-ai`。最终已没有这两个原 running 叶节点，新增了比较、历史核账、认证核查与 AI 调用四项。[分解与旧 attempt 证据](2026-10-28-visual-diff-thread-state-evidence/split-transition.json)

这部分当前状态已改善，应避免继续把两个原节点都描述为正在运行。但原子智能体的实际完成结果没有按原 attempt 独立提交/验收，结束时的摘要保存的是主会话后续 Docker 结果，仍需在验收说明里恢复其证据来源。

### C. 待验收积压来自未完成协议操作，旧式重复 finished 已被拒绝（高影响，当前仍存在）

初始 7 份待验收提交分别来自备份审计、剩余项审计、SDK 样例、传输验收、中立镜像、应用模式与可选 SDK 审计；最终新增历史迁移审计和主机状态核查，成为 **9 份**。最早一份提交于 09:18:31，至最终快照已等待约 65 分钟。

09:58:29，主会话对 `remaining-app-modes` 和 `sdk-optional-tools-audit` 再次调用 `update_task(finished, resolved)`；`read_thread` 中两次调用的 status 均为 **failed**。当前 `server.py:1143` 明确拒绝覆盖待验收 submission，要求改用 `review_task`。[失败调用原记录](2026-10-28-visual-diff-thread-state-evidence/recent-host-read.json)

当前九项 review 均未记录接受或退回；提取到的直接任务调用中也未出现主会话 `review_task`。此外，09:44 后尝试 `rebuild_task_network` 失败，符合 `server.py:489` 对待验收提交的保护。这些不是“任务完成但界面没刷新”，而是独立验收步骤确实未完成。

隔离内存克隆中接受初始七项后，`remaining-sdk-optional-tools` 立即变成 ready，blocked 从 1 降到 0；这证明依赖门禁按设计工作，未出现后端死锁。该实验不表示真实任务已经被验收。[隔离后端证据](2026-10-28-visual-diff-thread-state-evidence/contract-backend-evidence.json)

建议由原主会话逐项检查范围、文件与证据后接受或退回。不能为清空计数而批量 accepted，也不能将“已执行审计”升级为其所审业务功能已成功。

### D. 同一持续目标被当作新任务，重复节点进一步破坏计划连续性（中高影响，使用问题）

10-26 13:48 用户明确要求实施应用自治与远程能力复用。之后 17:58:20、18:01:21、18:17:42 的“第6轮/第7轮/第8轮”均调用 `init_task_network`，创建不同 task_instance，旧任务进入 task_history；期间未见对应的新用户目标，主目标仍持续。至少这三次应使用原任务的追加分解或重规划，而不是把轮次存为新任务实例。[用户目标和初始化原文](2026-10-28-visual-diff-thread-state-evidence/goal-and-reinitialization.json)

当前 e30 session 的 15 个历史实例中，3 个冻结在 waiting_synthesis，3 个 pending，3 个 open，6 个 completed。部分旧根在叶任务全 resolved 后未综合验收就被归档；实例 `53b327cb…` 甚至保留归档时 running 的 root。归档快照本身可以合法保存未完成状态，但这些连续切片不能被误当成各自已完成的总目标。

同一当前轮还保留两个重合的 OpenSpec 4.2 目标：

- `remaining-sdk-optional-tools`：pending，等待镜像和应用模式验收。
- `sdk-optional-tools-audit`：无上述依赖，已经完成相同 SDK 可选工具范围并提交。

宿主记录显示，前者启动失败后另建了后者；原节点未取消、未说明被替代，也没有把审计结果接为其前驱。独立先做审计可以合理，但必须明确剩余实现/消费验收范围，否则门禁释放后可能重复派发同一工作。[派发与失败调用](2026-10-28-visual-diff-thread-state-evidence/host-spawn-turn.json)

建议保留原目标和历史，不重写旧验收。为当前轮消除重复范围，补充哪些历史成果被本轮接受、哪些原目标仍 open。当前根标题“本机可验证发行交付”不能自动替代完整应用自治目标的验收。

### E. 派发配置与真实 spawn 不一致，bound 不等于真实绑定（中高影响，使用与可观测性问题）

09:46 的三个新执行者具有同样差异：

| 节点 | Task Progress 记录 | 实际 spawn / 宿主回执 |
|---|---|---|
| remaining-neutral-image | fork none；agent `neutral-platform-image` | fork all；`/root/neutral_platform_image` |
| remaining-app-modes | fork none；agent `app-modes-e2e` | fork all；`/root/app_modes_e2e` |
| remaining-vd-ownership | fork none；agent `visual-diff-ownership` | fork all；`/root/visual_diff_ownership` |

这三个节点在实际 spawn 返回前就已有 `dispatch.status=bound`，绑定的是预填别名。实际 spawn 未显式传 model/reasoning，记录却写了具体派发模型/强度；不能据此确认实际用了记录中的模型。[逐项比较与原日志行号](2026-10-28-visual-diff-thread-state-evidence/dispatch-comparison.json)

当前 skill 要求新执行者 start → spawn → bind，并统一 fork none。服务端允许将已知实际执行者用于 follow-up，因此“启动即 bound”并非所有场景都错误；本例是三个新 spawn，名称与回执又不一致，不能按 follow-up 解释。协议也明确声明不认证宿主身份，故这是调用记录失真，不是已实现认证被绕过。

建议以后保存工具真正返回的执行者标识，按实际 spawn 配置填写字段；无法核实的实际模型保留未知。插件应区分计划别名和运行回执，避免同一个 agent_name 同时表示两者。

### F. 离线 HTML 在全部结果待验收时误标“待汇总验收”（中影响，已复现的实现缺陷）

`plugins/task-progress/app.html:984–990` 的最终交付标签仅检查 ended==total 且 open==0，没有排除 pending review。两个子任务都已提交、都未验收时，后台正确返回 awaiting_review，HTML 却显示“待汇总验收”。实际下一步仍是逐项 review，父/root 尚不能综合验收。

这只影响显示，后端没有放行。当前目标仍有未结束叶任务，尚未触发该边界。[复现结果](2026-10-28-visual-diff-thread-state-evidence/contract-export-evidence.json)，[实际生成的边界 HTML](2026-10-28-visual-diff-thread-state-evidence/contract-all_pending-export.html)

修正应让 pending review 优先于 waiting_synthesis 标签，同时保留 execution-ended 的定义，不改历史 outcome、不补造旧 review。

## 4. 次要问题与不应扩大解释的历史现象

- 当前轮初始 11 个 main_session 叶节点的 planned_assignment 未写自执行理由，但启动 assignment 已写；符合服务端启动门禁，却与 skill 要求“计划和启动都记录”不完全一致。四项 Docker 构建理由主要是共享缓存/串行；这不能单独证明复杂任务不可委派，但也不能仅凭任务标题断言每条构建命令都必须委派。
- 两个初始 running 节点超过 10 分钟后，多个 get_progress 已返回拆分提醒，直到 10:15/10:17 才实际拆分。该遗漏现已部分纠正，旧节点取消不能被当作实际子执行者在该时刻才结束。
- 历史 `fc1b85b1…` 中根于 10-26 10:56:32 finished/open，verify 第三次重试发生在 11:04:32–11:05:52，旧祖先验收未失效。它是旧记录时序缺口；根始终 open，没有假冒全完成。当前独立职责测试已经覆盖重试失效，不能把旧缺口直接宣称为现行后端仍可复现的漏洞。
- 汇总优先级会让“待验收”掩盖仍在执行的信息；建议同时显示运行数和待验收数。文本摘要 `server.py:1422` 把 ready_count 称为“待执行”，应改为“可执行”，因为 blocked pending 不在其中。当前网页元信息已经使用“可执行”。
- 初始快照仅 compact UTF-8 JSON 就有 **1,011,807 bytes**；去掉 task_history/history 后为 156,281 bytes。每次 get_progress 都返回完整历史和重复 attempt/evidence，约 85% 数据是历史。对持续多天任务，应增加当前态摘要与按需分页历史，减少工具输出截断及上下文负担；这不是已证明的服务延迟或状态损坏原因。

## 5. 建议处理顺序

1. 原会话先独立验收当前九份提交，确认正在执行的比较与真实 AI 前置条件，消除重复 SDK 范围。保留调查成功与业务成功的区别。
2. 对已结束执行者的旧 attempt，记录真实完成时间、原始结果与主会话后续工作来源；不要把旧 cancelled/raw history 改写成事后成功。
3. 统一该 Codex 会话的任务入口，并为 JSON/schema2 与 SQLite/schema3 建立可查的历史来源/迁移关系。先恢复可见性，再决定是否迁移。
4. 修复离线报告的 review/synthesis 标签边界；同步改善总状态和 ready 文案。
5. 插件增加明确协议版本/能力提示和紧凑读取，使用流程坚持实际 spawn 配置、真实绑定与独立 review。宿主没有提供可信身份时，继续保留“调用方填报”的边界。

## 6. 验证与限制

- 24 项现行 responsibility 后端测试通过；只运行与计划角色、派发、提交/验收、依赖、重试和旧记录兼容直接有关的测试。[日志](2026-10-28-visual-diff-thread-state-evidence/contract-responsibility-tests.log)
- 原快照的计数与状态由当前后端重新计算完全一致；接受提交的实验只作用于内存克隆，目标 session 未写入。
- 离线报告使用原 app 纯函数与隔离任务生成，确认标签缺陷；没有执行完整浏览器布局或桌面宿主交互测试。
- 目标会话正在持续变更。本报告区分 revision 666 的问题、审查期间已发生的拆分和 revision 677 的仍存状态，不能拿初始 running 数当永久当前值。
- 没有重新验收该会话的 Docker、PPTX、模型调用或业务产物。本文评价的是任务账本、宿主执行事实与插件协议的一致性，不替代原任务的业务验收。

补充静态审查：[历史与计划审计](2026-10-28-visual-diff-thread-state-evidence/history-audit.md)、[实现与隔离复现](2026-10-28-visual-diff-thread-state-evidence/contract-audit.md)。这两份子报告绑定初始 revision 666，涉及实时状态的结论以本文最终快照为准。
