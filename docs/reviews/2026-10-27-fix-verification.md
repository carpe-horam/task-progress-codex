# Task Progress 修复复核 — 2026-10-27

结论：**部分问题已修复，尚不能认定前两轮发现已全部解决。** 会话/标签页专项报告 UI01–UI06 的原始触发序列均通过正向复核；扩展到请求执行超时与乱序后，仍有一条会让卡片再次隐藏的路径。首轮 F01–F07 全部仍可复现，E01 的两个 hook 仍未受信任。

本轮只复核并新增报告/证据，未继续修改产品代码、安装缓存、hook 信任状态或重启服务。

## 当前版本与证据边界

- 工作区：`/Users/machengling/.codex/worktrees/4d11/task-process`，HEAD `8be27675b7815310d93e58cae6f3c8cca9ea957a`，验证对象包含上一轮未提交修复。
- `server.py` SHA-256：`abc3ff032397f01feb7c30cf4b09898bdf9da6b2eb61d3eef9e8df667ed7a2aa`。
- `app.html` SHA-256：`ad6f30590a18c9b62d0a864be1c79aa820ae87d84420b8884762554da6eff913`。
- 两文件与安装目录 `/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.1.3` 一致，复核前后无漂移。[最终源码绑定](2026-10-27-fix-verification-evidence/final-source-binding.json)
- Chrome 156.0.8078.48，真实当前 app.html / server.py，临时 session 与导出目录，模拟 MCP Apps 父宿主；截图/trace 采集于 2026-10-27 19:02–19:05（Asia/Shanghai）。时序控制只延迟真实请求的转发或响应，没有伪造业务结果。
- 原证据脚本包含“旧缺陷应当发生”的断言，本次使用正向验收副本并逐项记录 PASS/FAIL；不能用旧脚本失败推导修复成功。
- 本轮未执行真实 Codex 外层标签栏关闭/重开，也未验证原生宿主的请求调度频率。截图的标题、固定 iframe 高度和虚线属于测试宿主，不代表 Codex 实际留白尺寸。

## 会话与标签页原发现

| 编号 | 当前结论 | 本次观察 |
| --- | --- | --- |
| UI01 迟到 inline 检查暂停 fullscreen 刷新 | 原场景通过 | 交付旧 hidden_by_fullscreen 响应后仍每 5 秒刷新，get_progress 1→2，root 更新为进行中。 |
| UI02 当前 owner 退出后原卡不恢复 | 原场景通过 | 新 owner 关闭后旧合格卡恢复完整并读取当前任务，check 继续 3→4；低于高水位的首次历史挂载仍不可抢占。 |
| UI03 同源双挂载同时 latest | 原场景通过 | 两个实际 iframe 中仅一个 latest，另一个 superseded；当前 owner 关闭后另一个恢复。 |
| UI04A 登记响应未到便关闭，漏注销 | 原场景通过 | teardown 发出 unregister，服务端释放已接受登记；注销先到、初次登记后到也由墓碑拒绝。 |
| UI04B 旧标签降回 inline 被拒绝，残留全屏租约 | 原场景通过 | 返回 superseded 时登记已变 inline、expires_at=null，原卡恢复，无需等待 35 秒。 |
| UI05 注销后迟到续租复活 | 原序列通过 | 已注销 view_id 后续登记返回 superseded；原 inline 保持 latest。 |
| UI06 隐藏高度 0→10→0 抖动 | 原场景通过 | 隐藏期间高度通知仅 0；真正恢复可见后重新报告正高度。 |

另有快速 inline/fullscreen 切换（响应在超时前返回）、跨任务恢复和旧任务结果防覆盖两个浏览器场景通过。上述结论绑定已重放的序列，不包含下面的扩展超时条件。

证据：[浏览器逐项报告](2026-10-27-fix-verification-evidence/ui/verification-report.md)、[浏览器结果](2026-10-27-fix-verification-evidence/ui/results.json)、[服务端报告](2026-10-27-fix-verification-evidence/registry/report.md)、[服务端结果](2026-10-27-fix-verification-evidence/registry/results.json)。

## R01 / P2 — 请求超时后，旧全屏登记仍能重新隐藏卡片

位置：[app.html:242](../../plugins/task-progress/app.html#L242)、[app.html:357](../../plugins/task-progress/app.html#L357)、[server.py:1472](../../plugins/task-progress/server.py#L1472)。

当前前端只用本地 generation 拒绝迟到响应，并用 viewRegistrationStarted 串行登记。15 秒 RPC 超时会删除 pending 并释放串行锁，但不撤销宿主尚未执行的请求；服务端登记接口没有模式世代/序号。

确定重放的条件序列：

1. 全屏视图发出 register，模拟宿主暂不转发到服务端。
2. 视图切回 inline；15 秒后旧请求超时，前端补发 inline 登记。
3. inline 登记先真实执行并释放租约，会话卡片恢复。
4. 旧全屏请求在发出 **16,350ms** 后才真实执行，仍返回 registered 并重新建立 35 秒租约。
5. 会话卡片再次变成 tab-hidden，高度变为 0；本次观察约 **33 秒** 后随租约过期和下一次检查恢复。

这要求宿主延迟的是请求执行/转发，且造成后发 inline 先执行；只延迟响应不会产生同一结果，正常未超时切换也受到前端串行保护。真实 Codex 是否以及多频繁产生这种调度尚未验证。已注销 view 的墓碑修复仍有效；此序列没有注销 mount。

建议为每个 view 的模式迁移传递服务端可校验的递增世代，并在旧登记/续租到达时拒绝过期世代；前端单独忽略旧响应无法消除服务端已经发生的副作用。

证据：[正向断言失败](2026-10-27-fix-verification-evidence/ui/timeout-results.json)、[时间线](2026-10-27-fix-verification-evidence/ui/UI05-timeout-timeline.json)、[完整 trace](2026-10-27-fix-verification-evidence/ui/UI05-timeout-late-fullscreen-trace.json)、[重新隐藏截图](2026-10-27-fix-verification-evidence/ui/UI05-timeout-after-late-execution.png)、[到期恢复截图](2026-10-27-fix-verification-evidence/ui/UI05-timeout-late-fullscreen.png)。

## 首轮发现仍未修复

| 编号 | 优先级 | 本次复核结果 | 当前位置 |
| --- | --- | --- | --- |
| F01 初始化幂等失效 | P1 | 无 session 的同键重试仍创建不同 session；重规划后重试重建 instance；A→B running→旧 A 初始化重放仍归档 B 并切回新的 pending A。 | server.py:772、793–815、1090–1091 |
| F02 线性步骤缺少执行尝试隔离 | P2 | A 启动→pending→B 启动→A 迟到 completed 仍被接受；任务完成、assignment 保留 B、note 来自 A。 | server.py:1234–1240、1278–1288、1302–1317 |
| F03 线性部分完成的 HTML 结论错误 | P2 | 源状态 in_progress、completed=1/2，HTML 仍显示“最终交付：已解决”“已结束 0/2”。 | app.html:580、742–743、934–935 |
| F04 失败任务 HTML 误判 | P2 | 根与两叶均 failed，HTML 仍显示“待汇总验收”。 | app.html:935 |
| F05 HTML 缺少根验收记录 | P2 | HTML 根节点数 0，唯一验收摘要/证据均缺失；源快照及真实 XLSX 仍包含这些标记。 | app.html:920–922 |
| F06 归档运行耗时继续增长 | P2 | 真实等待 2.1 秒并刷新后 0秒→3秒；测试页墙钟增加一小时后显示 1小时0分3秒。 | app.html:384–401、1304–1324 |
| F07 hook Python 运行时不一致 | P2 | PATH 无 python3、CODEX_MCP_PYTHON_PATH 有效时，原 hook 退出 127；同脚本经 launcher 执行退出 0。 | hooks/hooks.json:10、22；scripts/launch_progress_mcp:6–17 |
| E01 hook 信任状态 | 环境 | 本次新的原生 hooks/list 仍显示 SessionStart、UserPromptSubmit 均 enabled=true、trustStatus=untrusted。 | 当前安装/宿主信任状态 |

首轮证据：[F01/F02 正向结果](2026-10-27-fix-verification-evidence/registry/results.json)、[F03–F06 浏览器结果](2026-10-27-fix-verification-evidence/ui/results.json)、[F03 报告截图](2026-10-27-fix-verification-evidence/ui/F03-report.png)、[F04 报告截图](2026-10-27-fix-verification-evidence/ui/F04-report.png)、[F05 报告截图](2026-10-27-fix-verification-evidence/ui/F05-report.png)、[F06 截图](2026-10-27-fix-verification-evidence/ui/F06-archived-time.png)、[F07 新复现](2026-10-27-fix-verification-evidence/native/hook-runtime-repro.json)、[E01 新查询](2026-10-27-fix-verification-evidence/native/task-progress-hooks.json)。

## 安装与运行状态

**R02 / P3：缓存版本元数据不一致。** 上一轮将工作区两文件直接复制到 0.1.3 缓存后，缓存 manifest 仍为 0.1.3，但新进程 initialize 返回 serverInfo.version=0.1.2，appInfo.version 也是 0.1.2。这已通过新导入安装副本和实际握手确认，会使运行版本识别不可靠；尚未证明它直接造成显示故障。[版本核查](2026-10-27-fix-verification-evidence/native/installed-version-check.json)

**E02：不能确认所有会话均已加载修复。** 19:01–19:02 的只读进程核对发现多个 cwd 指向安装目录的 Python 服务，其中多数启动于 17:35:42 文件同步之前，也存在同步之后新启动的进程。server.py 在进程启动时加载，未见运行时重新载入逻辑；文件哈希相同不能证明旧进程内存已更新。本轮未把这些 PID 绑定到特定会话，未重启服务，也未核实已有 iframe 的脚本加载版本。[进程快照](2026-10-27-fix-verification-evidence/native/running-processes.json)

## 验证汇总

| 验证层 | 结果 | 解释 |
| --- | --- | --- |
| 既有针对性回归 | 13/13 通过 | 8 项视图归属/租约测试 + 5 项展示元数据测试。绑定工作区版本，不能替代新增边界验收。 |
| 后端正向复核 | 13 场景，8 通过、5 失败 | 失败为 F01 的 3 条路径、F02、R01 协议序列。 |
| 浏览器/协议正向复核 | 14 场景，9 通过、5 失败 | 失败为 F03–F06、R01 超时执行乱序。UI05 原序列在此脚本中直接调用真实后端。 |
| 安装与 hook 核查 | F07、E01 仍在，R02 元数据失败 | 文件同步成立，原生运行时全面生效未证明。 |

后端与浏览器的 R01 是同一残留，不重复计为两个缺陷。所有命令未命中 Dagu 白名单，使用本地隔离数据；浏览器串行。现有测试日志见 [existing-regressions.log](2026-10-27-fix-verification-evidence/native/existing-regressions.log)。本轮不做全量测试、Windows 或原生 Office 渲染验证。

原报告：[首轮深度审查](2026-10-27-current-plugin-audit.md)、[会话/标签页专项审查](2026-10-27-session-tab-ui-audit.md)。本报告记录截至本轮的修复状态，不覆盖原证据。
