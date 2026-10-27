# 当前源码 UI / HTML 导出正向复核（2026-10-27）

原 UI01–UI06 的重放场景通过正向验收；F03–F06 均仍复现。此外，扩大乱序边界后发现一个条件性残留：fullscreen 登记请求在宿主执行前延迟超过 15 秒，前端超时后已成功降回 inline，旧请求随后执行仍可复活 fullscreen 租约，令会话卡片重新隐藏约 33 秒，直到租约过期恢复。

本轮只验证，不修复代码。所有新文件、隔离 session、导出文件均位于本目录。没有修改产品代码、安装副本、原报告，也没有访问用户其他会话数据。

## 绑定与方法

- 工作区 `/Users/machengling/.codex/worktrees/4d11/task-process`；HEAD `8be27675b7815310d93e58cae6f3c8cca9ea957a`，产品是当前未提交修改版本。
- app.html SHA-256 `ad6f30590a18c9b62d0a864be1c79aa820ae87d84420b8884762554da6eff913`。
- server.py SHA-256 `abc3ff032397f01feb7c30cf4b09898bdf9da6b2eb61d3eef9e8df667ed7a2aa`。
- 首轮前后散列一致，补充场景后再次核对未变。见 [source-binding.json](source-binding.json)、[source-before.sha256](source-before.sha256)、[source-after.sha256](source-after.sha256)。
- Chrome `156.0.8078.48`，headless，Playwright 复用已有安装。截图/请求 trace 于 2026-10-27 19:02–19:05（Asia/Shanghai）采集。
- 使用原始当前 app.html 和真实 server.py，独立 SESSION_DIR。模拟 MCP Apps 父宿主只转发 JSON-RPC、通知模式变化并延迟选定真实请求或响应；业务工具结果均来自真实服务，没有改写产品 JS 函数或伪造任务/租约结果。
- 导出走真实 save_export_files，只为隔离写入，将测试进程 Path.home 的返回目录定向本目录的 home/；未改 HOME 环境变量、业务函数或返回结果。真实导出路径和文件大小保存在结果和 trace 内。
- 老 evidence 脚本只复制作为结构参考；所有验收断言改为修复后的正向行为。单项失败均被记录，不把旧缺陷断言失败当作修复成功。脚本进程 exit 0 代表完整采集完成；具体 PASS/FAIL 以结果 JSON 为准。
- 本轮命令未命中 dagu-test-runner 白名单，本地、串行、独占浏览器执行。主会话维护 verify_ui 进度节点，本子验证没有另建进度根或面板。

## 逐项结果

| 项目 | 正向结果 | 观察与证据 |
| --- | --- | --- |
| UI01 迟到 inline check | PASS | 在 fullscreen 成功登记后交付旧 hidden_by_fullscreen 响应，自动刷新仍为每 5 秒；5.6 秒后 get_progress 从 1→2，root 从可执行更新为进行中。[trace](UI01-late-check-trace.json) / [截图](UI01-late-check.png) |
| UI02 新标签更新、返回 inline、关闭 | PASS | 原卡先被折叠，关闭新 owner 后原卡恢复完整、读到进行中；check 从 3→4，并非停止检查。[trace](UI02-newer-tab-close-trace.json) / [截图](UI02-newer-tab-close.png) |
| UI03 同源两个挂载 | PASS | 两个实际 iframe 同结果挂载后仅 second 的 check=latest，first=superseded；second 关闭后 first 自动恢复。[trace](UI03-same-source-owner-trace.json) / [截图](UI03-same-source-owner.png) |
| UI04A 登记响应未到便关闭 | PASS | 服务端已接受 fullscreen 登记但响应被持有；teardown 发出一次 unregister，租约删除、原卡恢复。[trace](UI04A-close-during-register-trace.json) / [截图](UI04A-close-during-register.png) |
| UI04B 旧 fullscreen 返回 inline | PASS | 旧来源被 superseded，但其 fullscreen 租约已经撤掉，更新的 main 恢复完整。[trace](UI04B-older-tab-return-trace.json) / [截图](UI04B-older-tab-return.png) |
| UI05 已注销 mount 的迟到续租 | PASS（原序列） | unregister 后同 view_id 的 fullscreen register 返回 superseded，inline 仍 latest；新 view_id 可以正常登记。[真实后端序列](UI05-late-heartbeat-trace.json) |
| UI06 隐藏高度 | PASS | main 隐藏后 size-changed 仅有 0，没有原先紧接 10；关闭 tab 恢复后重新报告正高度。[trace](UI06-hidden-height-trace.json) / [截图](UI06-hidden-height.png) |
| 快速模式切换 | PASS（响应在超时前返回） | 持有 fullscreen 登记成功响应，快速 inline→fullscreen→inline，再交付；最终只有 inline 登记、无 fullscreen 租约，自动刷新正常。[trace](rapid-mode-switch-trace.json) / [截图](rapid-mode-switch.png) |
| 跨任务恢复 | PASS（本场景） | A 的旧卡被 B 新卡替换；B 卡关闭后 A 挂载自动展示 B。随后再次推送 A 旧结果不能覆盖 B。[trace](cross-task-recovery-trace.json) / [截图](cross-task-recovery.png) |
| F03 线性部分完成 HTML | FAIL，仍复现 | 源 status=in_progress、completed=1；HTML 最终交付仍为“已解决”，已结束仍为 0/2。[快照](F03-snapshot.json) / [trace](F03-linear-partial-trace.json) / [报告截图](F03-report.png) |
| F04 失败根任务 HTML | FAIL，仍复现 | 根与两叶均失败，源 status=failed；最终交付仍“待汇总验收”。[快照](F04-snapshot.json) / [trace](F04-failed-root-trace.json) / [报告截图](F04-report.png) |
| F05 根验收摘要/证据 HTML | FAIL，仍复现 | HTML 根节点数=0，唯一摘要和证据均缺失；源快照与实际 XLSX 均含两个标记。XLSX ZIP CRC 正常。[快照](F05-snapshot.json) / [trace](F05-root-evidence-trace.json) / [报告截图](F05-report.png) |
| F06 归档耗时 | FAIL，仍复现 | 归档 running 任务先显示已用0秒，真实等待2.1秒并刷新后已用3秒；仅测试页面将墙钟增加1小时后显示已用1小时0分3秒。[trace](F06-archived-time-trace.json) / [截图](F06-archived-time.png) |

首轮结果：[results.json](results.json)，13 场景中 9 PASS / 4 FAIL；[执行日志](run.log)。F03–F05 的真实 HTML 与 XLSX 位于 `home/Downloads/Task Progress Exports/`。

## 条件性残留：已降回 inline 后，超时旧 fullscreen 请求复活租约

该场景不是正常模式切换必然发生；条件是宿主把一个 fullscreen register 请求的**执行**延迟超过前端 15 秒 RPC 超时，且它在更晚的 inline 登记之后才真正执行。

实际顺序：

1. tab fullscreen 已登记，较新 main inline 被其租约隐藏。
2. 宿主拦住 tab 的一次 fullscreen register，使其尚未进入真实服务。该请求由定时刷新触发重新登记。
3. tab 切回 inline。旧登记仍在途，因此前端串行保护等到其 15 秒超时才补发 inline 登记。
4. inline 登记真实执行，返回 superseded，同时释放 fullscreen 租约；main 恢复完整，size=256。
5. 在旧请求发出后 16,350ms 才将它送入真实服务。它仍返回 registered，后端登记再次成为 fullscreen，尽管 tab 的实际宿主模式已是 inline。
6. main 下次检查后重新 tab-hidden，size=0；5 秒后仍隐藏。该段 size=0 持续 33,000ms，直到 35 秒租约自然到期后的下一次检查恢复 size=256。

因此“只用前端 viewRegistrationStarted 串行化便能完全防止模式登记乱序”不能通过扩大边界的验收；后端缺少模式转换世代/序号，较旧 register 无法识别自己已经过时。已注销 mount 的 tombstone 能挡住 UI05 原序列，但正常 inline 降回没有终结 mount，挡不住此序列。

[正向断言结果（FAIL）](timeout-results.json) / [完整请求与高度 trace](UI05-timeout-late-fullscreen-trace.json) / [关键时间线](UI05-timeout-timeline.json) / [登记状态快照](UI05-timeout-conditional-observed.json) / [重新隐藏时截图](UI05-timeout-after-late-execution.png) / [租约到期恢复截图](UI05-timeout-late-fullscreen.png) / [日志](timeout-run.log)。

## 复跑入口与边界

- 首轮：`node /tmp/task-progress-fix-verify-ui/verify.cjs`
- 条件性超时场景：`node /tmp/task-progress-fix-verify-ui/timeout-verify.cjs`
- 未验证真实 Codex 标签栏关闭、重开、焦点、宿主请求调度发生频率或安装副本行为。这里只证明模拟宿主协议下当前源码的行为与确定可重放的乱序条件。
- 宿主标题/固定 500px iframe/虚线边框是夹具，截图不能证明 Codex 实际留白尺寸；隐藏高度结论来自真实 size-changed 通知。
- 本轮不包含原生 Excel/Numbers 渲染、Windows 行为、长时间多次跨任务压力或全量后端回归。已归档时长的 +1小时仅影响测试页 Date.now，真实等待增长也已独立记录。
