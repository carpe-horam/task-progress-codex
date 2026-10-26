# Task Progress 功能审计 — 2026-10-26

本报告保留修复前基线的检查结果。后续修复与回归结果见 [功能修复与验证](2026-10-26-fix-verification.md)。

检查结论：当前版本存在 **11 项可复现功能问题**，其中 4 项应优先修复。问题集中在分层依赖、重试后的验收状态、重规划隔离和卡片更新顺序。另记录 2 项接口/异常输入健壮性问题，不计入这 11 项。

本次完成的是检查与证据整理；没有修改插件实现、发布、提交或推送。

## 检查对象与证据边界

- 工作区：`/Users/machengling/.codex/worktrees/ef11/task-process`。
- Git HEAD：`dafb74ae624337100f05f5479fd67940e17bf7b3`，初始工作区干净、detached HEAD。
- 已安装版本：`/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.1.0`。
- 安装目录和工作区的 `server.py`、`app.html`、`.mcp.json`、POSIX launcher、`SKILL.md` 逐字节相同。因此这里的源码问题适用于当前安装副本；这不代表已检查远端最新版本。
- `server.py` SHA-256：`b020c0784b4658338d8f0a16191f94bb4bd210baccf2beb10a2e0c5ad98cdcb6`。
- `app.html` SHA-256：`ab93d57b36221a39453f013c0f2ae77c8687c74ece608087d247ba743276f7f8`。
- 后端使用原函数与公开操作顺序，在临时 `SESSION_DIR` 中复现；并检查真实 launcher / stdio。测试没有操作用户已有任务数据。
- UI 使用 **Chrome 155.0.8059.85 + 原始 app.html + 原始隔离 Python 服务 + 模拟 MCP Apps 父窗口**。没有改写产品前端函数。记录的 UI 故障已在真实浏览器复现，但没有据此宣称已在 Codex 原生宿主验证其发生频率。
- 浏览器导出的 `save_export_files` 请求由模拟宿主截获，将真实生成的文件写入临时目录；原始 Python 保存函数另有独立后端场景验证。因此导出检查不能视为生产宿主落盘的完整端到端证明。
- XLSX 校验覆盖 ZIP CRC、全部 XML、三张表行数、任务/轮次隔离、两次重试记录及 Unicode。未用 Excel/Numbers 打开，也未验证宿主 `downloadFile` 分支。
- 本次命令均预期低于 1 分钟且不在 Dagu 白名单，按 `dagu-test-runner` 本地运行。没有安装依赖、变更 Dagu 或创建额外 worktree。

## 优先修复的问题

### F01 / P1：重规划生成的根 ID 可与旧任务冲突，旧 attempt 能写入新计划

位置：`server.py:259`、`server.py:762-765`、`server.py:680-683`。

复现：创建合法叶任务 `root-e1` → 启动并取消，保存旧 `attempt_id` → 重规划到 epoch 1 → 新根自动命名为 `root-e1` → 用旧 task instance 和旧 attempt 上报 `finished/resolved`。

实际：请求被接受，**新计划根任务变成 completed**，摘要是旧叶任务的迟到结果。新根是 pending，因此只针对 running 节点的 attempt 检查不起作用；重规划保持 task instance 不变，实例检查也不能拦截。

预期：新生成的根 ID 也必须在整个 task instance 生命周期内唯一，旧轮次/旧 attempt 的回报必须被拒绝。

建议：生成根 ID 时检查当前和历史 ID，或使用与用户 ID 不冲突的身份方案；更新入口同时绑定计划轮次和尝试身份。不能只在 `decompose_task` 检查历史 ID。

证据：`replan_generated_id_collision`。

### F02 / P1：分组任务的前置依赖没有传递给子任务

位置：`server.py:461-465`、`server.py:691-693`。

复现：根下创建 A 和 G，设置 G 依赖 A；再把 G 拆成 G1。

实际：A 仍是 pending 时，ready 列表已经包含 G1，并允许 G1 running。就绪判断仅检查叶节点自身的 `depends_on`。父任务综合验收也只检查直接子任务是否结束，没有检查父节点自身的前置依赖。

影响：调用方按插件的 ready 列表派发，会提前执行本来受前置条件约束的工作。

建议：把祖先的依赖约束纳入后代就绪判断及阻塞证据，并在分组验收时校验自身依赖。

证据：`ancestor_dependency_bypass`，输出 `ready=[a,g1]`、`a=pending`、`g1=running`。

### F03 / P1：重试绕过前置依赖检查

位置：`server.py:653-658`。

复现：B 依赖 A，依次完成 A、B；重试 A 使其 running，再重试 B。

实际：B 也能进入 running，尽管 A 尚未完成。依赖检查只在旧状态为 pending 时执行，终态重试跳过该检查。

影响：重试时的实际执行顺序与初次执行不同，B 可以消费尚未重新验证的 A 结果。

建议：每次新 attempt 启动均检查当前依赖策略，而非只检查首次 pending → running。

证据：`retry_bypasses_dependency`，A 和 B 同时 running。

### F04 / P1：已验收任务重试后仍显示完成，且父任务无法再次验收

位置：`server.py:505-506`、`server.py:649-655`、`server.py:658-679`。

复现：完成叶任务 A 并完成根任务；随后对 A 使用 `retry=true`。

实际：整体 `status=completed`，同时 `percent=0`、`executing_count=1`。父任务保留旧 resolved；父任务重复提交 finished 被“已结束任务不能覆盖结果”拒绝，尝试重开父任务则被“分组任务不能 running”拒绝。

影响：卡片和 API 会把仍在执行的任务判为已完成，而且不能按通常流程重新综合验收。

建议：明确重试策略：禁止在已终结祖先下直接重试，或原子地使祖先验收失效并允许重新综合验收。不要仅修改百分比显示。

证据：`retry_under_completed_root`。

## 其他确认的功能问题

### F05 / P2：接受子任务依赖祖先的执行死锁

位置：`server.py:348-370`。

G 下的 G1 依赖 G，这个计划被接受，但 G1 等 G 完成、G 又等 G1 结束。ready 列表为空；启动 G1 与完成 G 均被拒绝。当前环检测把层级边和依赖边都建成 G → G1，未表达“父任务验收等待子任务”的条件。

建议：拒绝子任务依赖其祖先，并进一步检查层级完成约束与依赖组合产生的环。可通过取消/重规划恢复，不是不可恢复的数据损坏。

证据：`ancestor_dependency_deadlock`。

### F06 / P2：运行中的叶任务拆分后，原 attempt 无法正常取消或失败收尾

位置：`server.py:406-407`、`server.py:649-650`、`server.py:748-749`。

启动 A 后把 A 拆成 A1，A 保留 running 和原 attempt，却已成为分组。随后上报 A cancelled/failed 都被拒绝；重规划也因为 A 仍 running 被拒绝。此时还会出现父节点 running、`executing_count=0`，因为统计只数叶任务。

这与 skill 的长任务执行中评估拆分流程直接相关。当前只能先结束所有子任务，再把 A 报为 finished/open 等间接收尾，无法如实记录原 attempt 被取消。

建议：明确执行中拆分的迁移语义，收束并保留原 attempt，或在拆分入口要求先结束该 attempt；不要让其进入不能正常结束的状态组合。

证据：`running_leaf_split`。

### F07 / P2：父任务综合验收的执行者元数据被丢弃

位置：`server.py:658-679`、`server.py:691-710`。

子任务结束后，用带完整 assignment 的 `finished/resolved` 验收父任务。响应成功，但父节点 `assignment={}`、`attempt=0`、`attempts=[]`、`started_at=null`。赋值只发生在启动 running 时，而分组任务又禁止 running。

skill 要求记录主会话综合验收的实际配置，但当前 API 无法保留这次验收配置和尝试。初始化的 `main_session` 不能代替每次验收的配置。

建议：给父任务综合验收建立单独的记录路径，保存 assignment 与验收时间/尝试；沿用不可变快照规则。

证据：`parent_synthesis_assignment_dropped`。

### F08 / P2：取消根任务后，整体状态错误地变成“待汇总验收”

位置：`server.py:505-516`。

新建未拆分根任务并取消：`root_status=cancelled`，但整体 `status=waiting_synthesis`、`percent=100`。整体状态分支处理了 failed，却没有处理 cancelled。

建议：整体状态显式保留根的 cancelled 终态，避免 UI 和消费 API 的调用方继续等待不存在的验收。

证据：`cancelled_root_overall_status`。

### F09 / P2：多层 ready 任务的祖先被误标为依赖阻塞

位置：`server.py:477-485`。

结构 root → G → G1，G1 无任何依赖且 ready：G 显示 decomposing，root 却显示 blocked。状态汇总只传播直接子节点的 ready，没有传播 decomposing。

建议：递归传播“后代有可执行工作”的状态，使根状态与 ready 列表一致。

证据：`nested_ready_group_looks_blocked`，`root_status=blocked`、`ready_tasks=[g1]`。

### F10 / P2：迟到的刷新响应会让卡片倒退

位置：`app.html:799-810`、`app.html:840-847`。

真实浏览器复现：暂缓返回一次 running 状态的 `get_progress` 响应；后台完成 A，并向卡片发送更新后的工具结果；再释放早先刷新响应。

实际：卡片从 `1 / 2 项执行结束` 倒退到 `0 / 2`，A 显示进行中，而后端仍是 finished/resolved。`receiveResult` 只校验 session，没有比较 task instance、epoch 或 revision。

影响：发生请求乱序时暂时误报进度；此时点击导出还会使用这个旧的 `latestProgress`。下一次成功刷新通常可以恢复，但不能消除已导出旧快照的问题。

建议：同一任务只接受不低于当前 revision 的快照，并区分任务切换、计划轮次及清除事件的次序。

证据：`stale_refresh_rolls_back_card`；`2026-10-26-stale-card.png` 是浏览器模拟宿主截图，不是 Codex 原生 UI 截图。

### F11 / P2：旧卡片较晚加载会抢占最新卡片

位置：`app.html:183-192`、`server.py:983-999`。

先加载最新任务卡片，再加载/重新挂载携带旧工具结果的卡片。旧卡片注册后成为 latest，最新卡片在 5 秒检查中被折叠。注册只携带 session_id 和随机 view_id，服务端无条件采用最后一次注册，无法识别历史消息顺序。

影响：在按需加载历史内容或重新挂载 iframe 的宿主中，任务栏附近的新卡片会提示用户查看“最新卡片”，实际保留的却可能是历史位置的卡片。保留卡片以后可能刷新到新内容，但显示位置选择仍然错误。

建议：由服务端/宿主发放与工具结果顺序绑定的视图标识；拒绝旧生成次序的注册，不能仅用 iframe 启动时间判断新旧。

证据：后端 `view_registration_order` 与浏览器 `old_card_remount_hides_newest` 是同一问题的两层复现，仅计一次。

## 另行记录，不混入主要功能缺陷数量

1. **冲突 assignment 静默忽略。** 对同一 HTN attempt 提交不同 model，接口返回成功，但保存旧 model。旧快照没有被污染；问题是调用方未获知参数被拒绝，且与线性 API 明确报错的行为不一致。证据：`changed_assignment_silently_ignored`。建议明确返回冲突错误。
2. **异常 JSON-RPC 形状会使服务退出。** 向 stdio 输入 `[]` 后再输入 ping，进程抛出 `AttributeError` 并以 1 退出，ping 无响应。JSON 解析成功后未校验对象形状，且 `handle` 外没有异常边界。证据：`malformed_rpc_crashes_server`。正常客户端通常不会发送这种输入，故单列为健壮性问题。

## 已验证正常的范围

- 普通 A → B → 根任务的执行、验收和就绪推进；未满足依赖时初次启动会被拒绝。
- `dependency_policy=finished` 能放行失败前驱，并保留其摘要与证据。
- 普通同层依赖环被拒绝，拒绝操作不改变持久化文件。
- 常规旧 attempt、旧 task instance、缺失新 task instance、旧轮次普通 task ID 被拒绝；F01 是自动生成根 ID 的例外。
- 12 个独立进程向同一隔离 session 并发追加任务，没有丢失节点或产生损坏 JSON。
- 清除某个 session 不影响另一 session；已清除视图返回 missing_progress。
- launcher、MCP initialize、13 个工具登记、原始 HTML 资源读取正常。
- Chrome 中初始渲染、文本转义、手动刷新、展开状态保留、5 秒自动刷新正常。
- 历史任务与历史计划可显示；导出保留 2 个 task instance、3 个任务/轮次组合和 C 的两次执行尝试。
- XLSX/HTML 从真实导出按钮生成；HTML 能加载。XLSX ZIP/XML/行数、中文、emoji、`<script>` 字面文本均校验通过。
- 文件保存路径限制、同名导出避让、0600 权限检查通过。导出保存函数仅写入隔离临时目录。

## 复现材料与运行方式

`docs/reviews/repro_2026_10_26.py`：21 个后端观察场景。结果中 9 项通过、10 项功能异常、1 项接口契约差异、1 项健壮性异常。

`docs/reviews/repro_2026_10_26_ui.cjs`：7 个浏览器检查项，其中 2 项复现 UI 问题，其余 5 项通过；F11 与后端重复，因此合计 11 项独立功能问题。

```sh
python3 docs/reviews/repro_2026_10_26.py > docs/reviews/2026-10-26-backend-results.json
node docs/reviews/repro_2026_10_26_ui.cjs
```

UI 脚本依赖本机已有 Chrome 与 Codex bundled Playwright，路径写在脚本头部；其他机器需调整。脚本退出 0 表示观察流程完成，**不表示插件所有功能通过测试**。

证据文件：

- `2026-10-26-backend-results.json`
- `2026-10-26-ui-results.json`
- `2026-10-26-export-validation.json`
- `2026-10-26-stale-card.png`
- `2026-10-26-fixture-export.xlsx`
- `2026-10-26-fixture-history-export.xlsx`

未覆盖：Windows launcher 实机、Linux 实机、Codex 原生卡片完整自动化、宿主 download-file、Excel/Numbers 实机打开、超长历史压力与进程断电/磁盘耗尽恢复。仓库当前没有随版本维护的测试套件；此次材料是审计复现，不是已集成的回归测试。

修复顺序建议：先处理 F01–F04 的隔离和状态正确性，再处理 F05–F07 的层级生命周期，最后处理 F08–F11 的状态传播与界面次序；修复时把这些观察场景改成断言预期行为的回归测试。
