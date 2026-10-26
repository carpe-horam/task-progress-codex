# Task Progress 功能修复与验证

2026-10-26：已在 `codex/fix-progress-state-and-views` 工作树实现前次审计的 11 项功能修复，以及冲突 assignment、异常 JSON-RPC 输入两项健壮性修复。基线为 `dafb74ae624337100f05f5479fd67940e17bf7b3`。

本报告中的完整回归记录针对修复后的工作树源码。随后将插件、服务端和界面的版本号统一升级为 0.1.1，并通过 Codex 官方 CLI 安装、启用本地修复版；安装核验见 `2026-10-26-install-verification.json`。版本号升级未改变功能逻辑，安装核验确认全部 8 个插件文件与工作树一致，并检查实际启动器、MCP 握手、13 个工具及界面资源。未向远程发布。

## 修复结果

| 审计项 | 修复行为 | 主要回归 |
| --- | --- | --- |
| F01 重规划 ID 冲突 | 自动根 ID 避开所有历史 ID；任何带错 attempt 的更新都会被拒绝 | `test_generated_root_does_not_reuse_any_historical_id`、`test_stray_attempt_cannot_finish_an_unstarted_task` |
| F02 分组依赖漏传 | 子任务继承全部祖先约束及其原始放行策略；验收也检查依赖；历史视图/导出同步计算继承依赖 | `test_group_dependency_blocks_children_and_parent_acceptance`、`test_inherited_policy_and_evidence`、`inherited_dependencies_in_history_and_export` |
| F03 重试跳过依赖 | 每次新 attempt 均检查当前依赖；不能让运行中的消费者依赖的结果失效 | `test_retry_rechecks_current_dependency`、两个 running consumer 测试 |
| F04 重试后仍完成 | 重试使已结束祖先回到待验收，保留历史验收；子任务完成后可逐层重新验收 | `test_retry_reopens_all_ancestors_and_preserves_acceptances` |
| F05 层级执行死锁 | 用开始/完成事件图联合检查层级与依赖；祖先依赖和跨分组等待环被拒绝 | `test_ancestor_dependency_deadlock_is_rejected`、`test_cross_group_completion_cycle_is_rejected` |
| F06 运行中拆分卡住 | 需当前 attempt ID；原执行尝试保存为 cancelled，节点转为分组；拒绝的拆分不改变原尝试 | `test_split_running_task_closes_attempt_and_can_be_completed`、`test_invalid_split_does_not_close_running_attempt` |
| F07 验收配置丢失 | 父任务验收要求完整 assignment，生成独立 synthesis attempt，保存时间、摘要与配置 | `test_synthesis_requires_and_persists_execution_metadata`、导出校验 |
| F08 取消状态错误 | 根任务取消后整体状态明确为 cancelled | `test_nested_ready_status_and_cancelled_root` |
| F09 多层就绪误报 | 子分组的 decomposing 状态向上递归传播 | `test_nested_ready_status_and_cancelled_root` |
| F10 旧响应回退卡片 | session 级 state revision 在任务切换和清除后继续递增；界面拒绝旧响应，导出使用已接受的新快照 | `stale_refresh_is_ignored`、`stale_task_notification_is_ignored`、`clear_and_recreate_order` |
| F11 旧卡片抢占 | 卡片使用首次工具结果的状态版本、发放序号和任务身份注册；刷新不得替换卡片来源 | 后端视图排序测试、`old_card_remount_does_not_hide_newest` |
| 冲突 assignment | 同一 attempt 的配置冲突明确报错，不再静默忽略 | `test_attempt_metadata_is_immutable_on_updates_and_completion` |
| 异常 JSON-RPC | 校验请求及参数形状，返回协议错误后继续处理后续请求 | `test_stdio_recovers_from_malformed_requests`、真实 launcher 握手测试 |

## 操作规则与兼容性

- 分解 running 节点前，调用方先协调实际执行者停止/移交，再向 `decompose_task` 传当前 `attempt_id`。插件只记录状态，不会停止进程或子智能体。不能先将节点设为终态再分解。
- 父任务直接提交 `finished` 时必须附完整 assignment。服务端记录一次独立验收，无需先把分组设为 running。
- 叶任务重试后，所有失效的祖先验收需要重新提交；旧 attempt 和旧验收仍保留。
- 使用重规划实际返回的 `root_id`，不能假定一定叫 `root-e1`。
- 旧版本已经保存的依赖死锁仍可读取、归档和重规划；不会自动修改用户历史任务来伪造成功。已有不一致状态应通过新的计划恢复。
- 老卡片没有来源序号时可继续显示进度，但不能抢占有顺序信息的新卡片。
- 清除操作删除任务、历史和视图登记，只保留两个不含任务内容的顺序计数器，避免迟到响应复活旧任务。

这些规则已同步到仓库中的 `track-progress/SKILL.md`、工具描述及 README。

## 验证

执行目录：`/Users/machengling/.codex/worktrees/ef11/task-process`。

```sh
python3 -m unittest discover -s tests -v
NODE_PATH=/Users/machengling/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules node tests/test_app.cjs
git diff --check
```

- **26 个后端测试通过**，最终日志记录耗时 0.248 秒，包括 12 进程并发写入、旧计划读取、线性模式版本递增、实际 launcher/MCP 握手。
- **10 个浏览器检查项通过**，浏览器为 Chrome 155.0.8059.85；运行真实前端和真实隔离服务端，使用模拟 MCP Apps 父窗口。
- XLSX ZIP CRC、XML、Unicode、2 个任务实例、3 个任务/轮次组合及 synthesis/retry 记录通过独立校验；三张表分别有 7、4、7 行（含标题）。
- `git diff --check` 通过。
- 命令按预期短于一分钟选择本地执行。补充 UI 测试中的消息确认辅助逻辑曾等待未返回，已终止该次测试并修正测试桩；最终完整检查通过。这不是产品修复后仍存在的失败。

证据：`2026-10-26-fix-backend-results.txt`、`2026-10-26-fix-ui-results.json`。可重复运行的测试位于 `tests/`；之前 `repro_2026_10_26*` 及审计输出保留为修复前记录，不覆盖为“通过”。

## 源码身份

完整回归时的文件 SHA-256（版本号升级前；0.1.1 安装文件哈希另见安装核验 JSON）：

| 文件 | SHA-256 |
| --- | --- |
| `plugins/task-progress/server.py` | `abdafbbcd935a377b40a609372c1090f32fcf6ce8be0762f8f34cba2fcea1934` |
| `plugins/task-progress/app.html` | `25b606d045fa710a2a235fde5032e857b4b5df9d78eed312d577488eedadee3a` |
| `plugins/task-progress/skills/track-progress/SKILL.md` | `8a1f24326c61cc542c14f61f636d79d0f38e705a0f7a971059fe3787e2cd8d8d` |

## 验证边界

尚未验证原生 Codex 宿主、宿主 download-file 分支、Excel/Numbers 打开以及 Windows/Linux 实机。浏览器测试截获导出保存请求并写入临时目录；没有覆盖用户的 Downloads 或已有 session。后续安装核验使用安装目录中的独立 MCP 进程，不代表已有 Codex 聊天连接已切换；安装时已提示重启 Codex 以加载新进程。
