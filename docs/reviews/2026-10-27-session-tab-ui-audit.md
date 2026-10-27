# Task Progress 会话卡片与标签页专项审查 — 2026-10-27

用户反馈的三类现象——重复/互相抢占、关闭后空白或不恢复、旧任务/刷新不一致/跳动——均有对应的实现缺陷。四个浏览器生命周期场景已复现；另有同源多挂载去重缺陷、关闭与续租乱序风险，以及高度通知冲突。

核心原因是：**任务数据的新旧顺序、当前显示位置、存活视图的归属、注册请求是否已返回**被混合用于决定显示与隐藏，缺少统一的生命周期约束。应整体修正这些交界，逐个修补 CSS 难以消除问题。

本轮仅审查和整理证据，没有修改插件实现、测试源码、安装副本或其他会话的任务数据；只维护本会话的审查进度。此前泛审查报告保持不变。

## 版本与验证边界

- 工作区 `/Users/machengling/.codex/worktrees/4d11/task-process`，HEAD `8be27675b7815310d93e58cae6f3c8cca9ea957a`。
- 已安装 0.1.3 与该工作区仅版本号不同；本轮再次按上一轮的 [SHA-256 绑定](2026-10-27-audit-evidence/source-binding.json) 核对，未漂移。
- 浏览器：Chrome 156.0.8078.48。原始 app.html + 原始 server.py + 独立临时 SESSION_DIR + 模拟 MCP Apps 父宿主；每个请求返回的都是实际服务结果，时序场景只延迟真实响应，不改写产品函数或伪造业务状态。
- 截图与 trace 采集于 2026-10-27，Asia/Shanghai。截图外层的标题、固定高度 iframe 和虚线边框属于测试宿主，不能当作原生 Codex 留白大小的证据；截图中的模式标题是初次挂载标签，后续模式切换以 trace 中的请求与登记为准。
- 真实 Codex MCP Apps 可读到 1 个现有进度标签页，本轮同 session 新任务已在该标签页正常刷新；没有再次调用 render_progress。该正常路径不排除时序缺陷。
- CUA 明确禁止访问 `com.openai.codex` 外层窗口，未绕过限制。因此未完成真实标签栏关闭/重开、焦点与实际重复标签数量的端到端操作验证。[现场观察](2026-10-27-tab-audit-evidence/native-native-observations.json)

## 症状与根因对应

| 用户现象 | 已确认的根因 | 证据层 |
| --- | --- | --- |
| 标签页开着但内容停在旧状态 | UI01：旧 inline 检查响应在模式切换后仍被采纳，关闭 fullscreen 的自动刷新 | 浏览器 + 真实后端 |
| 关闭后会话卡片一直只剩折叠入口 | UI02：退出视图仍占 latest_inline，原卡又停止检查，没有自动恢复路径 | 浏览器 + 后端登记序列 |
| 两张卡片都当自己是最新、重复展示 | UI03：同源多挂载没有唯一 view owner | 后端登记序列 |
| 标签页已离开，会话卡片仍隐藏数十秒 | UI04：关闭/降回 inline 没有完整释放 fullscreen 租约 | 两个浏览器场景 + 后端租约检查 |
| 关闭后恢复又消失 | UI05：迟到的旧续租可以重新建立已释放租约 | 后端序列；原生宿主乱序条件待验证 |
| 隐藏位置短暂跳动 | UI06：隐藏通知为 0，ResizeObserver 又报告 10 | 浏览器真实通知记录 |

## UI01 / P2 — 可见标签页被旧响应暂停刷新

位置：`app.html:275-285`，尤其 `282-285`；相关 `209`、`1413-1418`。

复现顺序：会话卡片发出 check_ui_view；服务端返回 hidden_by_fullscreen，但响应延后交付。关闭另一个标签页，将这张卡片切为 fullscreen 并成功登记，然后交付旧检查响应。最后启动后台任务并等待 5.6 秒。

实际：标签页保持可见，自动刷新文案变成“自动刷新暂停”；后台 root 已是 running，界面 root 仍为“可执行”，get_progress 调用数保持 1→1。旧响应重新把 hiddenByFullscreen 设成 true，canPoll 因而始终为 false；心跳续租不会清除此状态。重新切换模式等外部操作可以恢复，但它不会自行恢复。

建议：每个 check/register 响应绑定发出时的视图生命周期、模式和转换序号；只允许仍匹配当前上下文的响应改变显示。hidden_by_fullscreen 只能影响对应 inline 视图，不能关闭 fullscreen 刷新。

证据：[浏览器结果](2026-10-27-tab-audit-evidence/ui-results.json)、[完整请求顺序](2026-10-27-tab-audit-evidence/ui-late-check-fullscreen-trace.json)、[截图](2026-10-27-tab-audit-evidence/ui-late-check-fullscreen.png)。

## UI02 / P2 — 最新视图退出后，唯一存活卡片也不恢复

位置：`server.py:1423-1428`、`:1462-1467`、`:1489-1498`；`app.html:264-267`、`:1386-1389`、`:1424-1426`。

复现顺序：原 inline 来源为 revision 1；打开 fullscreen；后台推进至 revision 2，标签页刷新；标签页返回 inline，以新来源成为 latest_inline；原卡被判 superseded；再正常关闭并注销刚返回的标签页。

实际：剩余 iframe 只有原卡，仍为 `inline-stale compact`。服务端 views 里也只剩原卡，latest_inline 却仍保留已移除视图的 revision 2 来源。原卡的 check 次数在额外等待 3.3 秒后仍为 2→2，因为 superseded 分支永久停止了检查。此时已没有 fullscreen 租约，等待 35 秒也不能依靠租约到期恢复。

后端独立复现还证明：原卡重新 register 仍会被已退出来源的水位拒绝。它不是合法历史卡片应当折叠的正常情况，而是当前没有任何存活视图拥有完整 inline 显示权。

建议：把“最新已发放来源水位”与“当前存活的显示 owner”拆开；fullscreen 的数据刷新不能自动升级为 inline 所有权。owner 注销时建立明确的恢复规则，并让暂时失去显示资格的卡片能够收到恢复通知或继续有限检查。不能直接放开所有旧来源，否则会重现旧卡抢占新卡的问题。

证据：[关闭后浏览器 trace](2026-10-27-tab-audit-evidence/ui-newer-tab-close-trace.json)、[截图](2026-10-27-tab-audit-evidence/ui-newer-tab-close.png)、[后端完整序列](2026-10-27-tab-audit-evidence/registry-results.json)。

## UI03 / P2 — 同一个结果的两个挂载实例同时是 latest

位置：`server.py:1423-1427`、`:1458-1467`。

复现：两个不同 view_id 使用完全相同的 state_revision、view_sequence、task_instance_id 登记 inline。两次登记均成功，两个 check 均返回 latest。

原因：latest_inline 只保存来源三元组，没有唯一 owner view_id；同源实例无法区分。宿主重新挂载或保留两个相同结果的 iframe 时，插件不能让其中一个让出完整展示和轮询。

建议：同源挂载也需要明确唯一 owner 或 mount generation。来源排序负责防旧数据，owner 身份负责去重，不能互相替代。

证据：[same_result_two_mounts](2026-10-27-tab-audit-evidence/registry-results.json)。本项已通过后端实际协议序列确认，没有在原生宿主主动制造重复标签页。

## UI04 / P2 — 退出 fullscreen 的两条路径残留租约

### A. 注册已被服务端接受，响应未到便关闭

位置：`app.html:319`、`:1439-1442`。

服务端已经登记 fullscreen，但前端尚未收到成功响应，因此 viewRegistered=false。此时执行 resource-teardown，前端跳过 unregister。关闭后 3.2 秒原卡仍为 tab-hidden，注册表中 fullscreen 租约还剩约 28 秒；trace 中 unregister 调用数为 0。

### B. 较旧标签页降回 inline，被来源排序拒绝

位置：`server.py:1423-1424`、`:1427-1428`；`app.html:307`、`:264-267`。

旧来源 tab 已登记 fullscreen，后来有更新来源的 inline 卡片。tab 切回 inline 时登记返回 superseded，前端折叠并停止心跳，服务端却提前返回，没有撤掉它原来的 fullscreen 记录。实际 UI 已离开 fullscreen，当前完整会话卡片仍因该记录隐藏。

浏览器看到 main=tab-hidden、返回的 tab=inline-stale compact；后端受控时间确认第 34 秒仍 hidden_by_fullscreen、第 36 秒恢复 latest。35 秒是登记租约，不是用户必须等待的合理交互约定。

建议：关闭必须覆盖已经发出但尚未确认的登记；模式离开 fullscreen 时，应原子释放旧租约，再独立判断该视图能否成为 inline owner。登记资格被拒绝不能保留与实际模式不符的旧登记。

证据：[关闭注册中视图](2026-10-27-tab-audit-evidence/ui-close-during-register-trace.json)、[降回 inline](2026-10-27-tab-audit-evidence/ui-older-tab-return-trace.json)、[租约时间检查](2026-10-27-tab-audit-evidence/registry-results.json)。

## UI05 / P2，条件性风险 — 迟到续租可以复活已经关闭的标签页登记

位置：`server.py:1497`、`:1427-1428`。

后端顺序：fullscreen 登记 → unregister → inline check=latest → 已在途的旧 heartbeat 随后到达 → 相同 view_id 再次 register 成功 → inline 又变 hidden_by_fullscreen。

这是确定可重放的服务端序列；实际发生需要宿主将续租与关闭请求以该顺序交付/执行，本轮没有验证原生宿主的发生频率。不能把这一条件性路径说成当前现场必然发生。

建议：unregister 终结特定 mount/lease generation，旧 generation 的续租必须拒绝；新挂载使用新身份。仅设置前端 disposed 无法撤销已经发出的请求。

证据：[关闭/续租结果](2026-10-27-tab-audit-evidence/registry-unregister-race-results.json)、[复现脚本](2026-10-27-tab-audit-evidence/registry-repro_unregister_race.py)。

## UI06 / P3 — 隐藏高度被其他通知覆盖

位置：`app.html:197-198`、`:1500-1502`；body 最终 padding=5px。

真实浏览器通知记录为：main 高度 0 → 紧接着 10 → 下次约 3 秒检查后 0。syncPresentation 明确要求隐藏，但 ResizeObserver 从 documentElement 读取到剩余 padding，再发出非零高度。

按最后一条通知调整 iframe 的宿主可能产生短暂 10px 空白或位置跳动。测试宿主固定 500px 的外框不是该缺陷的真实大小，不能据此宣称出现 500px 空白。

建议：所有高度通知通过同一个函数生成；隐藏状态稳定返回 0，只有真正恢复可见时才重新测量内容。

证据：[高度通知序列](2026-10-27-tab-audit-evidence/hidden-height-sequence.json)。

## 现有测试存在覆盖断点

- `python3 tests/test_progress_view_modes.py -v`：5 项通过，主要验证元数据、展示入口和存储隔离。[日志](2026-10-27-tab-audit-evidence/native-test-progress-view-modes.log)
- 原样运行一次 `tests/test_view_mode_regressions.cjs`：exit 1。其第 113 行初始化没有 atomic_reason，第 119 行启动空 root 被 planning_required 拒绝，尚未执行 124–134 行的关闭/恢复断言。[原始日志](2026-10-27-tab-audit-evidence/ui-original-test_view_mode_regressions.log)
- 该失败属于测试夹具与新规划契约不一致，不能当作产品关闭流程的失败证据，也不能声称该关闭恢复回归已经通过。
- 本轮 [浏览器复现脚本](2026-10-27-tab-audit-evidence/ui-repro.cjs) 的四个场景均使用合法 atomic_reason，exit 0，成功断言异常行为；[后端复现脚本](2026-10-27-tab-audit-evidence/registry-repro_registry.py) 也使用隔离数据。
- 所有命令均不在 Dagu 白名单，本地执行；浏览器测试串行，没有安装新依赖。没有重复运行与本轮范围无关的完整后端套件。

## 建议修复顺序与验收

1. 先修 UI01 和 UI02：响应必须绑定当前模式/生命周期；明确 inline owner、退出和恢复规则，避免停刷或无法自动恢复。
2. 合并修 UI03–UI05：将登记、模式迁移、注销、续租作为一个一致协议，区分数据来源顺序与存活视图身份；确保关闭后的旧请求不能复活登记。
3. 修 UI06，统一隐藏状态的高度报告。
4. 更新合法测试夹具，保留本轮乱序/关闭中断/同源挂载/视图恢复场景；最后在真实 Codex 验收关闭、重开、切换标签、同会话新任务、历史卡片重挂载及焦点行为。

最低验收结果应是：同时只有一个有效完整 inline owner；可见 fullscreen 持续读取最新任务；关闭完成后不存在旧租约；已有卡片自动恢复；迟到响应不改变新模式；隐藏期间只报告 0 高度。真实宿主限制与代码修复结果应分别报告。
