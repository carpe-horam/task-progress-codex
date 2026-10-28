# Task Progress 修复独立复审

工作区：/Users/machengling/.codex/worktrees/3e36/task-process。日期：2026-10-28。只读审查实现与测试；隔离复现仅使用临时目录，不访问真实任务内容、不修改真实任务、不安装、不提交。

## 结论

独立审查发现的三处兼容边界已反馈原 owner，当前源码均已修正，未留需继续修复的阻断项。已读取 UI owner 最终历史浏览器回归通过日志，并独立查看 SQLite-only 导出截图；最终历史边界无未解决项。

## 发现、证据及复核

1. 无当前 JSON 且 SQLite 损坏时，get_progress 原本吞掉 unavailable，返回 progress=null 与“没有活跃进度跟踪”。已用临时 home、临时 SESSION_DIR 和内容为 corrupt 的数据库文件复现。修正后 server.py:get_progress 对 unavailable 创建独立只读 archive envelope，_format_detail 明确任务存在性及原结果均未知。独立复跑 compact/full 均见 mode=legacy_archive、legacy_history_unavailable，且未创建 JSON。
2. shared_service.load_core(--session-dir) 原本只切换 SESSION_DIR，新增 LEGACY_DB_PATH 仍指向默认真实缓存路径，违背 CLI 的 isolated data directory 语义。未读取真实库；通过源码确认。当前显式 session-dir 将 archive 固定为 session_dir.parent/progress-v3.sqlite3；默认生产路径不变，新增 unit 覆盖路径选择。
3. 旧节点 payload 损坏时 adapter 实际返回 node.details_unavailable=true，UI 最初只检查 task.details_unavailable。隔离 legacy fixture 复现：task flag=None、node flag=True。当前 app.html:legacyDetailsIncomplete 同时检查节点和任务，并用于卡片、HTML、XLSX；browser fixture 已改为 node-only shape，避免虚假覆盖。

## 其他风险边界

- server.py:_tool_result 默认只删返回体中的 task_history/history/legacy_history；history_summary 仍分别统计当前实例归档任务、当前任务旧计划和独立 SQLite 实例。原存储未删除，显式 include_history=true 保留归档中的历史轮次。
- app.html:historyBinding/reconcileHistory 按实例、state_revision、epoch、revision 和摘要绑定完整缓存；ensureFullHistory 检查完整性并拒绝失败与落后响应。没有加载到完整现代 JSON 历史时不会调用保存导出。
- SQLite adapter 采用 mode=ro/query_only、显式列、事务、有界字段与任务/节点数；不迁移、不构造当前协议 submission/review/accepted。SQLite-only HTML 和 XLSX 现有专用分支，不再虚构现代任务轮次、顺序步骤或 0/0 完成统计。
- clear_progress 墓碑 legacy_history_cleared 在新 init 后仍保留。独立临时目录复核 clear→get_progress(None)→init→full：无 SQLite 历史复活。
- 初始化保护不改变 request_id 已绑定重试分支；new_task_reason=None 时指纹字段保持旧形状。运行中与待验收均不能通过 new_task_reason 绕过；待完成新目标必须给出理由，真实完成根可正常开始下一目标。
- spawn 配置 agent_name 不再充当回执；只有明确 follow-up 工具可用已有执行者立即绑定。绑定与 submission/review 的既有门禁保持。

## 验证界限

主会话负责全 Python regression，UI owner 串行运行浏览器回归，本复审未重复全跑。这里对源码与具体疑点做独立最小复现，不表示已安装插件或已修正目标业务会话的历史状态。

## 最终 UI 证据复核

已读取 /tmp/task-progress-ui-20261028/legacy-suite/test_history_loading_app.log（4组通过结果）与 /tmp/task-progress-ui-20261028/history/results.json，确认覆盖compact首屏和刷新、缓存保留与失效、落后full响应、加载失败不保存、JSON/SQLite来源分离、有界摘录与损坏详情。新增测试还包含SQLite-only HTML/Excel现代计划负断言及node-only details_unavailable真实shape。已独立查看 /tmp/task-progress-ui-20261028/history/sqlite-only.png：页面仅显示SQLite只读来源、旧库任务数、旧状态原值和验收边界，没有虚构当前计划、顺序步骤或完成率。

主会话仍需接收UI owner剩余广泛生命周期测试的终态；该回归由其原owner串行运行，不属于本节点重复执行范围。
