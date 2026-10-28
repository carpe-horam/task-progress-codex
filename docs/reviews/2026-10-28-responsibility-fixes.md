# 职责与复杂任务委派修复验收

日期：2026-10-28。分支：`codex/task-progress-main-session-tab`。基线：`52f7b19abc452ce1bf2aa7cd23485df7db2028f6`。版本保持 **0.2.0**。本报告描述基线之后的代码修复与隔离验收；本轮没有升级已安装插件或后台服务。

## 修复结果

| 原问题 | 当前行为 |
| --- | --- |
| 复杂任务没有并行机会就留给主会话 | skill、示例和 hooks 明确复杂分析、实现、多文件修改、专门验证及评审默认委派，支持同一 agent 串行执行依赖链；主会话自执行须具体理由。 |
| 启动时静默覆盖计划分工 | `planned_assignment` 与实际 attempt 分开，角色不匹配拒绝；pending 叶任务用 `reassign_task` 显式改派并保留理由和历史。 |
| spawn 返回后不能补真实 agent ID | `bind_task_agent` 独立绑定当前 attempt，不修改不可变 assignment；未绑定的新 subagent attempt 不能提交 finished。 |
| 子智能体自报 resolved 就放行 | `update_task` 提交进入待验收，`review_task` 独立接受/退回；待验收阻断所有依赖策略。接受 open 报告仍保持 open。 |
| 父/root 可以空记录解决未完成子任务 | 综合验收要求主会话声明、摘要、证据及全部直接子项 resolved；重试使消费者及父级旧验收失效，并保留历史。 |
| 下一步与 UI 只展示执行者 | 按实际状态提示派发、绑定、复核、综合验收；面板、HTML、Excel 分别显示计划、派发、执行提交和验收记录。 |
| 旧记录混入新验收语义 | 历史未记录的验收保持未知，包括已知 main_session 角色的旧记录；真实旧 synthesis 的原始摘要和证据仍保留。 |

协议细节见 [responsibility-protocol.md](../responsibility-protocol.md)。原始缺陷与复现保留在 [审查报告](2026-10-27-responsibility-audit.md)，该报告及其复现结果绑定旧基线，不应当作当前缺陷结论。

## 独立复审与验证

独立复审首轮发现两处遗漏：未启动叶任务可直接 `finished/open` 放行 `finished` 依赖；带主会话角色的旧记录被 UI 推断为无需复核。均交回原实现者修复并补回归。第二轮原始复现、24 项职责测试及最终 UI 源码绑定复核通过，未发现剩余阻塞。[独立复审与两轮证据](2026-10-28-responsibility-fix-evidence/peer-review.md)。

| 验证 | 结果与证据 |
| --- | --- |
| `python3 -B -m unittest discover -s tests -v` | **139 项通过，7.934 秒**，覆盖状态机、实际 HTTP、规划示例、hooks、存储兼容及服务管理器隔离测试。[日志](2026-10-28-responsibility-fix-evidence/python-tests.log) |
| 最终职责浏览器测试 | **7 组通过**，实际 app/server + Chrome 156，包含 DOM、HTML、XLSX、known-role legacy 与真实旧 synthesis，起止源码哈希一致。[说明](2026-10-28-responsibility-fix-evidence/ui/README.md) |
| 既有 UI 回归 | app 10、导出 3、交互 15、异常时序 7、共享服务 3，共 **38 组通过**；这些运行早于最终 legacy 分支修正，最终改动由上述 7 组覆盖，不将早期结果冒充最终源码重跑。[命令与绑定](2026-10-28-responsibility-fix-evidence/ui/validation.json) |
| 最终视图回归 | `test_view_mode_regressions.cjs` **3 组通过**，包括重复 fullscreen 降级、关闭后 inline 恢复。[日志](2026-10-28-responsibility-fix-evidence/view-mode-tests.log) |
| 原生 Codex MCP | 两个临时 app-server 客户端连接同一隔离 daemon，均发现 **17 个工具**，明确检查 `reassign_task` / `bind_task_agent` / `review_task`；UI 资源版本 0.2.0，未创建聊天。[结果](2026-10-28-responsibility-fix-evidence/codex-isolated.json) |
| 交付检查 | `git diff --check` 通过；manifest 版本仍为 0.2.0；最终 server/app 与独立复审和 UI 证据匹配。[源码 SHA-256 清单](2026-10-28-responsibility-fix-evidence/final-source-binding.json) |

测试均为短命令且不在 Dagu 白名单中，按 `dagu-test-runner` 本地执行；浏览器测试串行运行。状态与进程测试使用临时目录和自建进程，没有删除用户任务或终止用户旧进程。

## 验收边界

- 这是工作流约束。角色、agent ID、模型与验收者仍由调用方填报，HTTP 凭据不认证 Codex 主/子会话身份；插件不能阻止模型在其他工具中自行工作。
- 新职责流程使用 HTN；旧线性 API 继续兼容，不补造历史验收。
- 浏览器使用真实 app 与隔离服务，MCP Apps 父宿主由测试模拟；原生检查验证工具发现和资源读取，没有验证桌面所有调度时序，也未真实派发测试 agent。
- 本轮交付为当前分支代码提交。实际运行中的已安装插件和共享服务尚未升级，加载新协议需要后续更新安装。
