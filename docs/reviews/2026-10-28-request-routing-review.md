# 会话请求队列与恢复边界独立审查

审查时间：2026-10-28，Asia/Shanghai。范围仅限本轮新增请求分流、并行实例隔离、持久上下文和验收进度；此前历史兼容修复不重新归为本轮变更。审查者只新增本文件，未修改产品代码、测试或真实会话 JSON，未安装、提交或推送。

## 审查结论

初始审查确认两项 P2 界面/恢复指引缺陷，均已及时发送主会话并交原 owner 修正，最终源码复核已闭环，详见下文。当前未发现额外可复现的数据串写、队列丢失、迟到 attempt 覆盖或把待验收结果计为已验收的问题。该结论限于静态审查、定向测试及隔离最小复现，不等于宿主真实执行约束或跨平台崩溃恢复证明。

## 确认发现

### P2：已归档 foreground 仍生成必然失败的“只读查看”入口

初始位置：`plugins/task-progress/app.html:1567-1569`；对应实例选择限制为 `plugins/task-progress/server.py:332-343`。

复现：创建 A，登记队列 B 和并行 P；完成 A 的叶任务与根综合验收，调用 `activate_request(A, B)`。A 被放进 `task_history`，仍保留于 `session_work.requests`，但不在 `session_work_state.instances`。初始界面为所有非当前实例添加“只读查看”，所以也为 A 添加入口；点击会调用 `get_progress(task_instance_id=A)`。

隔离实测得到：`task_instance_id 不匹配；旧任务或其他实例的请求已拒绝。` 这不是写保护错误：界面将不可用的读取操作展示为可执行入口，且把旧 foreground 标为“并行”。应保留归档写保护，改为在历史任务区域查看旧 foreground。

处理状态：已交 UI owner；其修订把请求列表限定为独立并行实例和未启动队列，旧 foreground 继续由历史任务区域展示。修后验证见后文记录。

### P2：切换队首后，完成提示仍指向已经归档的来源任务

初始位置：`plugins/task-progress/server.py:292-294`、`plugins/task-progress/app.html:1565-1566`。

复现同上。A 收尾并激活 B 后，B 与仍运行的 P 的 `return_to.task_instance_id` 均保留 A。初始服务端只在目标仍是当前 foreground 时更新根位置；其他来源锚点没有状态语义。初始 UI 对任何 `return_to` 无条件显示“完成后返回原任务”。

隔离实测确认两者均指向已进入 `task_history` 的 A。保留来源身份本身有用，但必须区分“继续当前未完成任务”与“查看已结束来源记录”，否则引导会让主会话错回已归档任务。

处理状态：已交 backend/UI owner；最终服务端将来源身份保存为 `origin_return_to`，把行动锚点 `return_to` 指向当前 active/pending_review foreground，主任务已结束时为空。UI 仅对当前活动锚点显示“完成后返回”。最终状态见后文。

## 已核对的机制与验证

| 风险 | 核对结果 |
| --- | --- |
| 主任务与并行任务同名节点 | 独立 `task_instance_id` 选取网络；不同 attempt 的更新/绑定/验收被拒绝；并行叶执行限制为 subagent。 |
| 修改丢失与持久化 | `_session_lock` 使用进程内锁及文件锁；foreground 与所有并行实例写入同一原子替换 JSON，parallel 写回只更新其实例。 |
| FIFO / 幂等 | 队首显式激活；原任务未收尾不能激活；登记同 ID 同原始指纹不重复创建，修改后的请求不被登记重试还原；成功激活响应丢失后可重试。 |
| 归档与迟到写 | 激活替换 foreground 后旧实例写被拒绝；并行实例继续留在独立实例字典中；clear 留请求 tombstone，旧登记不能恢复已清理工作。 |
| 取消 | 未启动并行可取消，任何已创建 attempt 的并行不能用管理入口假装停止；取消实例拒绝后续写入且不返回可执行任务。 |
| 原文与 context | 原文保留空白且不可覆盖；当前显式 goal/constraints/acceptance/resume 与 revision 独立持久；rebuild 保留上下文和历史尝试。 |
| 重启 | 新模块读取同一隔离目录可恢复请求列表、当前上下文与完成证据；不需要迁移或触碰真实缓存。 |
| 主卡片与待验收 | 非 foreground 实例结果先按 session_work.foreground 过滤；只读预览不改 latestProgress；pending/rejected/legacy-unrecorded/invalidated 结果不计入已验收比例。 |

实际执行：

- 本地 `python3 -m unittest discover -s tests -p test_request_queue.py -v`：13/13 通过，约 0.4 秒。已读取 `dagu-test-runner`；本命令不命中白名单，且远低于用户给定 1 分钟门槛，所以本地执行。
- 临时 Python 最小复现复用 `tests/test_request_queue.py` 的 fixture，直接设置独立临时 `SESSION_DIR`，完成 A→B 切换后验证旧实例读取错误及 B/P 来源锚点；没有修改已有测试。
- 临时并发最小验证：8 线程同时登记 24 个不同请求（12 queued、12 parallel），最终含 foreground 的 25 条请求全部保留、12 个独立实例全部保留、请求 ID 唯一且 foreground 不变。

初始独立后端验证绑定：HEAD `8a9b6dc4e2d18fa1bcc03350859ece364d4ee6a2`；`server.py` SHA256 `b1eeb8cc1a5da60c8b51c2c0875d295f42c71df0d34de209ef6608c90c45b3eb`；`test_request_queue.py` SHA256 `732bd728c50b89407cec4676e537b8451bb44d942a09695c68c359130bbe272a`。工作区有其他已授权改动，HEAD 不能代表全部被审查源码；UI 在初始发现后由原 owner 并行修订，初始行号只定位发现时的代码。

## 证据边界

- 本次并发验证是同进程多线程；未实测多进程高压、断电、磁盘满、Windows 文件锁或所有写入崩溃点。现有激活测试仅注入原子文档替换前失败，证明该点不会半切换 foreground/队列。
- `get_progress(include_history=true)` 返回当前及归档主任务的上下文、当前选中实例的上下文修订、请求修订；并行实例的完整节点/尝试历史需显式按其 ID 另读。不能把默认精简响应当成完整历史。
- 现有离线 HTML/XLSX 导出收集器不收集 `session_work` / `task_context` / `context_revisions`。因此“运行时可恢复”不意味着导出包能还原队列和恢复文本；本轮未扩展导出范围。
- Task Progress 仍依赖调用方遵守主会话/agent 职责、报告真实执行与接受结果。实例/attempt 校验不认证宿主身份，也不能停止外部执行者。
- 本审查未自行重跑无关完整后端/浏览器套件；UI owner 的后续浏览器测试与最终 hash 应单独列明，不能把其回报冒充本审查者的独立运行。

## 修订闭环

两项初始发现均已闭环，无遗留审查阻塞项。最终检查绑定如下：

| 文件 | SHA256 |
| --- | --- |
| `plugins/task-progress/server.py` | `f32705c3fff85c75d5a7d087e7b11348f68fd20c5c7a9e1b3b6f92637cdc23b3` |
| `plugins/task-progress/app.html` | `51a7e83137314222fc0538dfee74be8685ccd59e419b5e527a3d43777f05f5e7` |
| `tests/test_request_queue.py` | `bb7ee58fdcfbb83ffa4663457112fb5da117bdea3b6054b4e0661c746c2d917b` |
| `tests/test_request_queue_app.cjs` | `35c49d6d3c0d15c79365e1d9af2b308de110aefd40e3df50da1ef37e6dabd4a3` |

- 独立复读最终 `app.html:1554-1562`：列表仅包含独立 parallel 与 queued 请求；来源/行动锚点文案有明确状态判断。旧 foreground 不再触发无法读取的实例接口，归档仍保留。
- 独立重跑前述 Python 队列命令：**17/17 通过，约 0.68 秒**。新增测试覆盖来源锚点、迟到读取响应、迟到 mutation 响应，以及历史 payload 不获得新 foreground 元数据。
- 独立隔离复现最终 A→B：P 的行动锚点为 B、来源仍为 A；B 无自返回锚点；B/P 未结束时 `can_finish_session=false`。
- 主会话另行确认并由 backend owner 修复了锁外重读可能混合旧 progress 与新 document 元数据的问题。独立复读最终 `_capture_response_snapshot`（`server.py:354-358`）及 `_tool_result`：锁内捕获 document、state_revision、view_sequence；历史 payload 的兼容入口不混入后来的 metadata。上述 17 测试中包含可控线程延迟、激活切换与旧视图被 superseded 的验证，已由本审查者实际运行。
- UI owner 回报 `NODE_PATH=/Users/machengling/.codex/worktrees/a672/nslide/node_modules/.pnpm/playwright@1.60.0/node_modules node tests/test_request_queue_app.cjs` 通过；截图与源码身份记录位于 `/tmp/task-progress-queue-ui/source-identities.json`。其测试包含真实后端的两个待验收 subagent、后继依赖阻塞、归档入口消失、返回新 foreground、open 并行如实收尾。本审查者读源码和回执，未独立重跑浏览器命令。

此前确认缺陷的历史复现保留，用于解释修订必要性，不代表其在最终版本仍未修复。后续若产品源码变化，需按新 hash 重新判断相关结论。
