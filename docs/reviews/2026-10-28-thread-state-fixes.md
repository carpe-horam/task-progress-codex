# Task Progress 会话状态修复交付

2026-10-28，本机 0.2.0 已更新；源码保留在 `codex/fix-thread-state-compat`，未提交或推送。依据此前 [会话审查](2026-10-28-visual-diff-thread-state-audit.md) 修复。

## 已完成

- 默认读取和更新省略历史正文，保留历史计数；展开或导出时读取完整 JSON 历史，防止迟到响应覆盖新状态。在目标会话 revision 698 的隔离副本中，进度 JSON 从 1,071,138 降到 215,613 字节，减少 79.87%。
- SQLite v3 历史恢复独立只读展示，实测找回两套 session 下的 1＋3 个实例。保留原始 resolved/open，不合并冲突、不补造验收。损坏、缺失详情和截断有明确标记；旧库故障不影响当前 JSON 导出。
- 初始化不能归档 running 或 pending review；其他未完成任务切换需记录新目标原因。新 spawn 的计划别名不再成为绑定回执，待复核提示给出 review_task 所需参数。
- 面板同时显示运行和待复核数量，全部待复核时不再误标待汇总验收；仅有 SQLite 历史时，面板和 HTML/XLSX 不虚构当前计划或完成率。
- 技能和 hook 补充同目标继续、真实执行回执、重复节点及待复核错误处理说明；隔离服务同时隔离旧库路径。
- 实际升级遇到 launchctl bootstrap exit 5，最初回滚也失败；已手动恢复旧服务，再补上卸载、端口释放等待及有界重试。新安装已成功，回滚以认证健康检查为准。现象与短暂卸载滞后相容，不能单凭 exit 5 证明唯一原因。

## 验证

- 最终全部 Python 回归 **167 项通过**。
- 浏览器回归 **9 套件、59 组通过**：面板10、审查回归7、交互15、视图模式3、共享服务3、生命周期6、历史4、职责8、导出3。使用真实 app.html、隔离后端和模拟 MCP Apps 宿主；不等同于所有原生桌面交互路径。
- 独立复审发现的三个边界已修正并复核；`git diff --check` 通过。
- 本机服务健康，运行目录 `0.2.0-a6d161aade4d1f98c878`；server、app、shared_service、service_manager、legacy_history 五个文件在源码、已安装缓存和实际运行目录的 SHA256 一致，技能与 hook 缓存也一致。
- 当前 Codex 会话的真实插件调用及认证 HTTP 完整历史读取成功。另用 Homebrew Codex CLI 0.147.0 新建的两个无聊天客户端探测未通过：列表忽略旧脚本的 serverName 过滤参数，修正脚本选择后仍未暴露工具详情；此独立探测不记为通过，未创建聊天或模型 turn。

## 状态与使用边界

升级后目标任务实例 `bb94ed82-aee5-44cf-8836-a22ee77cc239`，revision 700，仍为 awaiting_review：9 项待复核、3 项执行中。此次没有改写其业务节点、接受待复核提交或合并旧存储；这些状态需要原任务负责人依据执行证据处理。

旧 SQLite 每次最多取100个实例、每实例100个节点及有限长度字段，属于带限制说明的兼容摘录。没有宿主提供的可信身份和生命周期事件，插件仍不能证明模型、执行者身份或强制其他工具遵守计划。已打开的旧面板和已加载技能可能需要重新打开视图或在方便时重启 Codex，才能加载最新前端及协议说明。

证据见 [修复证据目录](2026-10-28-thread-state-fix-evidence)、[安装核验](2026-10-28-thread-state-fix-evidence/installed-verification.json)、[独立复审](2026-10-28-thread-state-fix-evidence/independent-review.md)、[源码绑定](2026-10-28-thread-state-fix-evidence/source-binding.json)。原插件缓存备份保留于 `/tmp/task-progress-repair-20261028/plugin-cache-before`。
