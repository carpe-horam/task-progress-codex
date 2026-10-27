# F03–F06 / R01 前端修复与浏览器验收

当前 app 已冻结，SHA-256 `8a6971236e5dad167187f172ac3ae41ffcb23aba23c6b006ce6fd72f13f99d1d`。
最终新增完整回归绑定后端 `cf78c49224cd8b4e04c3b6b1d9eb467180cf929bfc8f60ae1a1b1e08008fbed4`；manifest=0.1.4。

## 改动

- F03：线性 completed/in_progress 归一到 finished/running；交付由模式和整体任务状态判断，第一步不再被当作根；任务清单表预览保留全部线性步骤。部分完成、运行中、全部完成分别验收。
- F04：根 failed/cancelled 优先展示“执行失败”/“已取消”；无关闭时间的计划称“快照时间”，不推断仍在运行。已结束未解决统计包括 failed/cancelled，截图中的两个失败叶节点现在显示2。
- F05：HTML 保留根节点、根摘要、证据和综合验收/分组收尾 attempt；同样适用归档任务，叶任务计数不增加；证据索引表预览也保留根证据。
- F06：归档树按 archived_at 或快照截止时间冻结；HTN 和 linear 运行记录显示“归档时进行中”“截至归档已用”。未修改后台状态或伪造结束时间。
- R01：前端独立 modeGeneration 初始0，仅 host displayMode 真正变化时递增；register/heartbeat 携带 mode_generation，progress revision 变化不推进模式世代。原 viewGeneration 响应保护保留。
- appInfo.version 使用 `__TASK_PROGRESS_VERSION__` 由真实资源服务注入；三个直接源码测试使用明确 test-source 夹具版本，新增回归实际校验 manifest 注入。

只修改 app.html 与 tests/*.cjs；server、hook、manifest、技能和安装目录由主会话/其他代理负责。

## 结果

- 新增 `tests/test_app_audit_regressions.cjs`：最终7组全部通过（含实际超过15秒的延迟执行请求、跨任务旧source inline gen2先到/新source fullscreen gen1后到）。[日志](audit-regressions.log)
- `tests/test_app.cjs`：10项通过。[日志](test-app.log)
- `tests/test_export.cjs`：3项通过，真实导出、XLSX CRC、按表头校验任务标题。[日志](test-export.log)
- `tests/test_view_mode_regressions.cjs`：3项通过。[日志](test-view-modes.log)
- 既有三个脚本通过后，按主会话要求仅补充HTML文案、已结束未解决统计及线性任务/根证据表预览；最终新增7组对这些项做显式断言，未重复运行其他三个浏览器脚本。
- JS 语法与 git diff --check 通过。全程串行、复用既有 Playwright，没有安装依赖。

修复了既有测试夹具本身的断点：view 初始化补 atomic_reason；export 派发补 assignment；test_app 不再先要求被fullscreen隐藏的标题可见；test_export 不再访问已不存在的 DATA 全局或使用过期列索引。新增 linear 操作均捕获并携带当前 attempt_id。

## 关键证据

- [线性部分完成](report-01.png)：最终交付尚未完成，已结束1/2。
- [失败根总览](report-04.png)：执行失败、2项已结束未解决、计划为快照时间。
- [失败根记录展开](report-04-root-expanded.png)。
- [根验收摘要/证据/attempt展开](report-06-root-expanded.png)。
- [归档HTN耗时](F06-archive-htn.png)、[归档linear耗时](F06-archive-linear.png)。
- [模式世代trace](R01-mode-generation-trace.json)。
- [超时旧请求被拒、卡片保持可见trace](R01-late-execution-rejected-trace.json) / [截图](R01-late-execution-rejected.png)。
- [跨任务模式世代真实服务序列](R01-cross-task-mode-trace.json)。
- 每份report-NN.html都有对应snapshot、截图和app RPC trace。[源码绑定](source-binding.json) / [结果汇总](result-summary.json)。

## 命令

以下四条均以 `NODE_PATH=/Users/machengling/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules` 运行：

```
TASK_PROGRESS_BROWSER_ARTIFACTS=/tmp/task-progress-frontend-fix node tests/test_app_audit_regressions.cjs
node tests/test_app.cjs
node tests/test_export.cjs
node tests/test_view_mode_regressions.cjs
```

所有命令未命中 Dagu 白名单，按 skill 本地隔离执行。

## 验证边界

Chrome 156.0.8078.48，截图于2026-10-27采集；原始当前app由真实resources/read加载，业务结果来自隔离真实server。只有宿主请求/响应调度是模拟的，旧请求的业务结果没有被伪造。真实Codex是否产生相同时序及其发生频率仍未实测；固定iframe外框和标题属于夹具。没有修改安装缓存或操作真实Codex外层标签栏。末尾展开图只改变已导出静态HTML的details显示状态。
