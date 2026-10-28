# 并行任务以同级根节点展示

用户要求：独立并行任务应直接接在当前任务的 root 后面，与其同级，并保留自己的子任务。

## 交付行为

- 同一个「任务网络」中，先展示主任务 root，再按登记顺序展示并行任务 root；每个根拥有自己的子树、状态和验收结果。
- 移除原先并行请求列表中的「只读查看」切换入口，避免并行任务只能切换查看或者重复展示。
- 尚未启动的排队任务仍在队列中。队列激活后成为主任务，原主任务进入历史，并行根仍可查看。
- 顶部百分比明确标为主任务进度；并行任务完成不会增加主任务完成比例。
- 不同实例即使使用相同 root/child ID，展开状态和 DOM 仍互相隔离。取消的并行任务保留可查，未执行子项显示「未执行 · 请求已取消」。

## 数据与回归

`progress.parallel_tasks` 从与主任务相同的响应快照生成，每个实例使用相同 `state_revision`，不递归携带历史或其他实例。取消显示仅改变投影，不覆盖原始业务执行状态与摘要。

191 项 Python 测试通过。最终 6 项浏览器测试串行通过：parallel_roots、request_queue、app、interaction、history_loading、responsibility；覆盖同级顺序、同名节点隔离、待验收刷新、主进度口径、展开与选中文字保持、队列激活、历史、取消和旧缓存兼容。最终源码 hash 与测试记录一致，`git diff --check` 通过。

截图来自 2026-10-28 的 Playwright / Headless Chrome，使用真实隔离 Python 服务及构造场景。它们验证插件页面渲染和交互，不代表已经自动刷新用户当前打开的 Codex 旧卡片。

- [主任务 A 与并行 P1、P2 同级](2026-10-28-parallel-peer-roots-evidence/01-peer-roots-pending-accepted.png)
- [队列激活后仍保留并行根](2026-10-28-parallel-peer-roots-evidence/02-activated-queue-retains-completed-peer-roots.png)
- [取消请求与未执行子项](2026-10-28-parallel-peer-roots-evidence/03-cancelled-peer-root.png)
- [最终浏览器测试及源码标识](2026-10-28-parallel-peer-roots-evidence/final-browser-verification.json)
- [Python 全量测试日志](2026-10-28-parallel-peer-roots-evidence/python-all.log)

## 本机安装核验

已备份旧插件缓存至 `/tmp/task-progress-peer-roots-20261028/plugin-cache-before`，更新本机缓存并使用既有 service manager 安装。当前运行版本：

`/Users/machengling/Library/Application Support/Task Progress/releases/0.2.0-b1a5873e82d6702f0916`

服务健康检查通过。核对 7 个文件的源码、缓存及存在的运行副本 hash 一致。安装后通过实际 HTTP MCP 读取当前会话：主任务与之前实跑的独立并行 P 同时存在于同一响应，均使用 `state_revision=122`，P 带自己的 2 个节点，保持已完成状态。未改写 P 的业务节点。

[安装及当前会话数据证据](2026-10-28-parallel-peer-roots-evidence/installed-verification.json)

已有卡片若仍显示旧布局，需要重新打开进度视图以加载新版页面。此改动未扩大历史与导出的范围；没有提交或推送代码。
