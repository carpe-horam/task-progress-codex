# Task Progress 当前使用与显示审查 — 2026-10-27

本次确认 **7 项可复现缺陷，以及 1 项当前安装环境问题**。最优先处理初始化重试可能替换当前任务的问题，其次是 HTML 导出错误报告交付状态、遗漏最终验收记录。审查完成不代表这些缺陷已经修复。

## 范围与证据边界

- 审查工作区：`/Users/machengling/.codex/worktrees/4d11/task-process`，HEAD `8be2767`，开始时工作区干净、detached HEAD。
- 工作区标记版本为 0.1.2；当前安装目录是 `/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.1.3`。`server.py`、`app.html`、manifest 的差异仅为版本号，其余检查文件完全一致。抽查运行进程的 cwd 也指向该 0.1.3 安装目录。见 [源码绑定](2026-10-27-audit-evidence/source-binding.json)。
- 后端复现调用当前真实实现，使用隔离 session 目录；没有修改已有用户任务数据。
- 浏览器复现使用 Chrome **156.0.8078.48**、当前原始 `app.html`、隔离 Python 服务与模拟 MCP Apps 父宿主。截图采集于 2026-10-27（Asia/Shanghai）。导出保存请求由测试宿主写到临时目录；不等于原生 Codex 导出落盘端到端验证。
- 实际 Codex 的 MCP Apps 面板 DOM 能读到本次审查任务及三个执行中的审查阶段；原生截图调用超时，因此不声称完成原生宿主截图、关闭/重开标签页全流程验证。
- 原生 hook 状态来自桌面捆绑 `codex app-server` 的独立只读 `initialize` / `hooks/list` 查询，没有启动模型、创建会话或修改 hook 信任状态；它证明该环境解析到的注册/信任状态，不等于已经观察到活动会话的 hook 执行回执。
- 本轮仅新增审查报告与证据，未修复实现、安装、发布、提交或推送。

## 发现概览

| 编号 | 优先级 | 问题 | 影响范围 |
| --- | --- | --- | --- |
| F01 | P1 | 初始化幂等键不能覆盖首次无 session 重试、重规划和任务切换 | 重复任务；运行中的当前任务被旧请求归档并替换 |
| F02 | P2 | 线性步骤重启没有 attempt 身份隔离 | 旧执行结果错误完成新执行 |
| F03 | P2 | 线性任务 HTML 把第一步结果当成整体交付 | 未完成任务显示“已解决”，结束计数同时错误 |
| F04 | P2 | 失败根任务的 HTML 交付状态误判 | 全部失败仍显示“待汇总验收” |
| F05 | P2 | HTML 任务树跳过根任务 | 最终验收摘要、证据和执行记录缺失 |
| F06 | P2 | 历史快照的运行耗时继续增长 | 已归档任务看起来仍在实时执行 |
| F07 | P2 | hook 直接调用 python3，没有复用插件运行时查找 | MCP 可正常启动，提醒 hook 却可能无法执行 |
| E01 | 环境状态 | 两个提醒 hook 均为 untrusted | 当前不能确认自动规划提醒已启用 |

## F01 — 初始化重试可以替换已经推进的当前任务

位置：`plugins/task-progress/server.py:772`、`:793-815`、`:1090-1091`。

当前 `request_id` 仅存于当前任务对象。首调会先生成新的 session UUID；重规划会删除初始化键；切换任务后旧键不再被检查。

三种隔离复现均成立：

1. 首次调用省略 `session_id`，提供固定 `request_id`；模拟响应丢失后原样重试。得到两个不同的 session 和 task instance，第二个任务没有指向第一个任务的历史关联。
2. 创建 A 后重规划到 epoch 1，再重放 A 的原始初始化参数。当前任务变成新的 task instance，epoch 回到 0，重规划结果被移入历史。
3. 创建 A、切换到 B 并启动 B，再重放 A 的旧初始化请求。当前任务切回全新 pending A，仍在 running 的 B 被归档。B 的执行者继续向原实例报告时将无法更新当前任务。

这与“相同 ID 与内容不会重复归档”的工具契约不符。数据仍留在历史中，**不是物理删除**；但活动任务、执行者与面板显示会失去对应关系。

建议：使用独立于当前任务对象的请求键账本，并定义跨重规划、任务归档和清理的保留规则。首次分配 session 必须能通过同一请求键恢复，或明确要求调用方先持有稳定 session 身份。旧任务请求重放应返回原绑定结果或明确过期响应，不能修改当前任务。

证据：[主要复现结果](2026-10-27-audit-evidence/backend-results.json)、[首次重试结果](2026-10-27-audit-evidence/backend-initial-retry-results.json)、[复现脚本](2026-10-27-audit-evidence/backend-repro_backend.py)、[首次重试脚本](2026-10-27-audit-evidence/backend-repro_initial_retry.py)。

## F02 — 线性步骤的旧结果能结束新执行

位置：`plugins/task-progress/server.py:1234-1240`、`:1282-1288`、`:1302-1317`。

复现：在线性任务中启动步骤 0（执行者 A）→ 重置 pending → 以执行者 B 再启动 → 收到 A 迟到的 `completed` 请求，携带仍有效的 task instance、按接口允许省略 assignment。

实际：步骤及整体任务变成 completed，保存的执行者仍为 B，备注却来自 A。task instance 只能隔离任务，不能隔离同一步骤的多次执行；该接口也没有可供调用方传入的 attempt token。

影响：新执行尚未完成就可能放行下一步。本问题限定于兼容/显式线性模式；HTN 已有 attempt 防护。

建议：线性步骤每次启动生成新 attempt ID，运行更新、结束与重置均核对当前尝试；保存旧尝试并明确旧存储格式的兼容规则。

证据：[linear_late_completion](2026-10-27-audit-evidence/backend-results.json)。

## F03 — 只完成第一步，HTML 已报告最终交付成功

位置：`plugins/task-progress/app.html:548`、`:708-713`、`:902-903`。

复现：线性两步任务，仅完成第一步。真实源状态是 `in_progress`、完成 1/2；导出的 HTML 显示“已结束 0/2”及“最终交付：已解决”。

原因有两部分：线性状态 `completed` / `in_progress` 没有归一到 HTN 的 `finished` / `running`；所有线性步骤都没有 parent，查找第一个无 parent 节点时误把第一步作为任务根。

建议：导出前统一线性状态；整体交付以任务模式和整体状态计算，不能推断第一步等于根节点。

证据：[源快照](2026-10-27-audit-evidence/ui-linear-snapshot.json)、[截图](2026-10-27-audit-evidence/ui-linear.png)、[浏览器断言](2026-10-27-audit-evidence/ui-results.json)。

## F04 — 全部失败被描述为等待验收

位置：`plugins/task-progress/app.html:902-903`。

复现：两个叶任务均 failed，根任务也以 failed 收尾。源任务明确 `status=failed`，HTML 顶部仍将最终交付及下一关口标成“待汇总验收”。

原因：汇总判断只看 `ended === total && open === 0`，没有考虑根的 failed/cancelled，失败叶节点也被计入 ended。

建议：按根任务明确的验收/失败/取消结果优先判断；只有所有执行已结束且尚无最终验收结果时，才显示待验收。失败、取消与未解决应保留独立状态。

证据：[源快照](2026-10-27-audit-evidence/ui-failed-delivery-snapshot.json)、[截图](2026-10-27-audit-evidence/ui-failed-delivery.png)。

## F05 — HTML 遗漏根任务的最终验收记录

位置：`plugins/task-progress/app.html:888-890`。

复现：完成两个叶节点后，在根任务写入独有验收摘要 `ROOT_ACCEPTANCE_UNIQUE_SUMMARY` 和证据 `ROOT_ACCEPTANCE_UNIQUE_EVIDENCE`，随后导出。

实际：HTML 从根的子节点开始绘制，根节点计数为 0，验收摘要与证据均不可见。XLSX 中仍保留这两个标记且 ZIP CRC 正常，因此这是 HTML 导出遗漏，不是后端数据丢失。

影响：读者看到全部子任务及交付结论，却无法核对主会话最终验收、总体验证限制或根节点的失败收尾原因。

建议：保留根任务的可展开验收区域，或单独渲染根的摘要、证据、执行尝试；历史任务也应用同一规则。

证据：[源快照](2026-10-27-audit-evidence/ui-root-evidence-snapshot.json)、[浏览器断言](2026-10-27-audit-evidence/ui-results.json)。

## F06 — 归档快照继续显示增长的运行时长

位置：`plugins/task-progress/app.html:352-369`、`:1272-1292`。

复现：任务 A 正在 running 时，在同一 session 初始化任务 B，A 成为带固定 `archived_at` 的历史快照。展开 A，真实等待并刷新后，“已用 1 秒”变成“已用 3 秒”；受控墙钟再增加一小时后显示“已用 1 小时 0 分 3 秒”。

原因：历史树调用同一个 `appendElapsed`，没有传快照截止时间；无 finished_at 时使用 `Date.now()`。

历史快照可以保留“归档时仍在执行”，但不能据此暗示现在仍在执行。插件已没有这份归档记录的实时状态更新通道。

建议：历史任务按 archived_at/快照时间冻结耗时，文案使用“截至归档已用”，并明确“归档时运行中”。不要为了冻结显示而伪造任务完成或取消。

证据：[真实等待与受控时间结果](2026-10-27-audit-evidence/ui-results.json)。

## F07 — hook 与 MCP 使用了不同的 Python 查找规则

位置：`plugins/task-progress/hooks/hooks.json:10`、`:22`；对照 `plugins/task-progress/scripts/launch_progress_mcp:6-17`。

hook 写死调用 PATH 中的 `python3`，MCP launcher 却支持 `CODEX_MCP_PYTHON_PATH`、`CODEX_PYTHON_PATH` 及补充路径。

隔离复现：PATH 不含 python3，但配置了有效 `CODEX_MCP_PYTHON_PATH`。原 hook 命令退出 127、无提醒输出；同一个 hook 脚本通过插件 launcher 执行退出 0，正确返回 additionalContext。

影响：依赖捆绑 Python 或较精简 GUI 环境的用户可能看到 MCP 工具正常，却始终没有启动提醒。本机常规 PATH 中有 Python；这里是确定的条件性缺陷，不声称它已经导致本机 hook 失败。

建议：hook 复用插件的运行时解析入口，并新增配置路径有效、PATH 无 Python 的启动覆盖。

证据：[运行时差异复现](2026-10-27-audit-evidence/integration-hook-runtime-repro.json)。

## E01 — 当前两个提醒 hook 都还未受信任

本次只读原生 `hooks/list` 返回 SessionStart 和 UserPromptSubmit 两个 Task Progress hook，均为 `enabled=true`、`trustStatus=untrusted`。

这不是 hook 注册丢失，也不是本轮代码测试失败。它说明安装与发现已经完成，但还不能认为当前环境获得了已信任的自动提醒。服务端规划校验仅在插件被调用后生效，不能保证从未调用插件的任务也会自动建立计划。

建议：把“已安装、MCP 可用、hook 已发现、hook 已信任、最近真实触发”分开显示或提供诊断入口；按宿主正常信任流程处理后，再验收隐式触发。不应直接修改 trusted_hash 或绕过信任审核。本轮未修改信任配置。

证据：[仅含本插件的原生查询结果](2026-10-27-audit-evidence/integration-task-progress-hooks.json)。

## 验证与修复顺序

现有回归通过，但没有覆盖上述新增边界：

- 后端 `test_server.py`：42 项通过，约 1.31 秒；涵盖依赖、重试失效、分组验收和多进程写入等既有修复。
- `test_task_startup_contract.py`：8 项通过，约 0.09 秒。
- `test_app.cjs`：真实 Chrome 的现有界面回归通过。
- 新增隔离复现确认 F01–F07。浏览器复现串行运行，未安装依赖，全部命令均不在 Dagu 白名单，按 skill 本地执行。
- HTML 导出截图已人工复核；没有启动 Excel/Numbers，没有进行 Windows、断电持久化或大规模历史性能验证。

建议按 **F01 → F03/F04/F05 → F02 → F06/F07** 修复，并分别增加最小回归。E01 应作为当前使用环境的独立待处理项，避免把“代码修好了”与“自动提醒已生效”混为一谈。

完整脚本保留于本报告的 evidence 目录；原脚本中的输出目录仍指向 `/tmp/task-progress-audit-20261027-*`，便于重放时保持真实任务数据隔离。
