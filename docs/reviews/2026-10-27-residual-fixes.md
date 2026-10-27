# Task Progress 剩余问题修复验收 — 2026-10-27

本轮继续修复 [上一轮复核](2026-10-27-fix-verification.md) 中的 F01–F07、R01、R02，保留 UI01–UI06 的已有修复。工作区 HEAD 为 `8be27675b7815310d93e58cae6f3c8cca9ea957a`，验收对象包含未提交改动；没有提交或推送。

## 修复范围

| 项目 | 修复行为 | 验证 |
| --- | --- | --- |
| F01 初始化幂等 | 持久化请求账本绑定会话和实例；无会话 ID 重试不重复分配，重规划返回原实例当前计划；已归档、清理或替换的请求拒绝重放。 | 首次、别名、跨进程、重规划、替换/清理及写入失败回归。 |
| F02 线性尝试隔离 | 每次启动生成 attempt_id；更新、完成、重置、重开校验当前 ID；保存完成/取消历史。 | 缺失、旧 ID 均拒绝且不改状态，正确 ID 可完成；旧存储兼容收尾后严格校验。 |
| F03 线性导出 | 归一化步骤执行状态，交付结论由整个清单计算；数据表预览保留线性步骤行。 | 部分完成 1/2、运行中、全完成三种真实导出，并点击预览验证两个步骤名称。 |
| F04 失败/取消导出 | 根终态优先于叶任务完成统计；计划快照不再被错误标注为仍在运行。 | failed/cancelled 根状态与报告交付结论。 |
| F05 根验收记录 | HTML 保留根节点、验收摘要、证据和执行尝试；证据索引包含根证据。 | 当前和归档任务的真实 synthesis/group_termination 记录，并点击证据表验证根证据。 |
| F06 归档耗时 | 以归档/计划快照时间冻结未结束任务耗时，标明归档时状态。 | HTN 与线性非零时长在时间推进、刷新后保持不变。 |
| F07 Hook 运行时 | 两个 hook 统一使用 MCP Python launcher。 | 空 PATH、带空格路径、两个 Python 环境变量及异常输入。 |
| R01 模式请求乱序 | 前端发送独立 mode_generation，后端持久化最高模式世代；来源过期或其他提前返回也保留模式事实。 | 真正超过 15 秒才执行的旧 fullscreen 请求被拒；跨任务降回 inline、无旧登记、重复全屏和注销变体。 |
| R02 版本不一致 | 版本统一由 manifest 派生，资源服务替换前端版本占位符。 | MCP 握手与两个资源 URI 均跟随测试 manifest 9.8.7。 |

## 验证与证据

Python 完整套件：`python3 -B -m unittest discover -s tests -v`，**83/83 通过，3.764 秒**。包括 42 项既有后端、24 项新增状态/视图回归、8 项启动契约、5 项视图元数据、3 项 hook、1 项版本来源测试。测试使用隔离临时目录，不修改用户任务数据。

浏览器使用已安装 Chrome 和 Playwright，运行真实 `app.html` / `server.py`，通过模拟 MCP Apps 父宿主控制请求时序。新增回归通过实际 `resources/read` 读取 HTML，验证版本注入；既有脚本直接读取源码并明确替换为测试版本。测试脚本串行，本地运行；未命中 Dagu 白名单。

- 新增 `test_app_audit_regressions.cjs`：最终 **7 组全部通过**，绑定下方最终 server/app 哈希。
- 既有 `test_app.cjs`：10 项通过；`test_export.cjs`：3 项通过；`test_view_mode_regressions.cjs`：3 项通过。三脚本先通过，之后的小范围 HTML 文案、统计及表预览修正由最终新增回归覆盖，未重复跑无关脚本。
- Chrome **156.0.8078.48**，采集日期 2026-10-27；浏览器截图中的虚线、固定 iframe 区域为测试宿主，不表示 Codex 实际留白。
- [Python 完整日志](2026-10-27-residual-fix-evidence/python-tests.log)、[浏览器验收摘要](2026-10-27-residual-fix-evidence/browser/result-summary.json)、[浏览器证据说明](2026-10-27-residual-fix-evidence/browser/README.md)、[后端定向验收](2026-10-27-residual-fix-evidence/backend-summary.json)。

本轮修复了既有浏览器夹具中的过期 API 参数、隐藏元素可见性等待及导出表头定位；保留原行为断言，没有用期望缺陷发生的旧审计脚本冒充正向回归。

## 兼容边界

- 无 session_id 的 request_id 必须全局唯一；有 session_id 时按会话隔离。清理任务内容仍保留请求哈希与身份墓碑，避免迟到重放重建已清理任务。
- 新线性尝试要求 attempt_id。旧存储无 ID 的既有尝试只允许兼容收尾或重置，之后采用新协议。
- 对排序已推进而任务文件尚未写入的不确定半写状态，拒绝不确定重放，避免覆盖较新任务；未提供断电/fsync 保证。
- 浏览器证据验证插件行为，不等同真实 Codex 外层标签栏的完整端到端验收，也不证明既有 iframe 或旧 Python 进程已自动更新。

安装结果、最终文件哈希和宿主状态在下方验收记录中绑定；旧审查报告和失败证据保留，不改写历史结论。

## 安装与实际生效

通过正常 CLI 安装流程更新至 **0.1.4**：先将失效的 marketplace 来源从 `ef11/task-process` 更新为当前 `4d11/task-process` 工作区，再执行 `codex plugin add task-progress@task-progress-codex --json`。安装目录为 `/Users/machengling/.codex/plugins/cache/task-progress-codex/task-progress/0.1.4`。没有直接覆盖旧版本缓存或修改 hook 信任配置。

2026-10-27 19:31:59 的安装核验确认：server、app、manifest、hooks、launcher、skill、MCP 配置均与工作区一致；通过安装副本实际 launcher 启动的新进程，MCP initialize、v3 资源和兼容 v2 资源均报告 **0.1.4**，无未替换版本占位符。[安装核验](2026-10-27-residual-fix-evidence/installed-verification.json)

最终源码绑定（未提交）：

- `server.py`：`cf78c49224cd8b4e04c3b6b1d9eb467180cf929bfc8f60ae1a1b1e08008fbed4`
- `app.html`：`8a6971236e5dad167187f172ac3ae41ffcb23aba23c6b006ce6fd72f13f99d1d`
- [完整哈希记录](2026-10-27-residual-fix-evidence/source-binding.json)

**环境项仍需宿主操作，不能标记为已经全面生效：**

1. E01：新查询已发现 0.1.4 的两个 hook，均 enabled=true、trustStatus=untrusted。需在 Codex 正常信任流程中确认，代码安装不会自行授予信任。[hook 状态](2026-10-27-residual-fix-evidence/task-progress-hooks.json)
2. E02：安装后仍发现 **22 个**进度 Python 进程的 cwd 为 0.1.3，没有发现持久运行的 0.1.4 进程。没有将 PID 映射到具体会话，没有终止用户进程或重启 Codex。旧会话和已加载卡片应在重启 Codex 后重新打开/加载；当前证据只确认新启动副本可用，不确认旧界面自动更新。[进程快照](2026-10-27-residual-fix-evidence/running-processes.json)

结论：本轮发现的代码缺陷已修复并通过上述回归，0.1.4 已安装；旧会话重载和 hook 信任仍待宿主操作。
