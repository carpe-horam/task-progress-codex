# 0.2.0 后端职责协议只读审计

基线：`52f7b19abc452ce1bf2aa7cd23485df7db2028f6`；2026-10-27（Asia/Shanghai）。

结论：**服务端保存了计划分配和执行快照，但没有把“计划负责人、执行结果上报者、主会话验收者”建成三个独立的约束。父/root 的综合验收只受子节点终态和依赖条件约束，不限定为主会话。** Skill 中的职责规则目前主要依赖调用方遵守。

本轮没有修改产品或测试源码、安装服务、提交代码或访问真实任务数据。复现脚本在导入模块期间临时替换 `Path.home()`，避免导入时创建真实缓存目录；导入后再将 `SESSION_DIR` 指向 `TemporaryDirectory`。所有案例均仅直接调用临时模块，不连接已安装 daemon。输出内的模型、agent 标识和证据地址均为 fixture。

## 复现入口

```sh
python3 -B docs/reviews/2026-10-27-responsibility-evidence/backend_repro.py
```

脚本执行成功，7 个案例及其断言均通过；`backend-results.json` 记录源码 SHA256、commit、实际返回状态与函数位置。这里的“通过”表示所列缺口被复现，不表示职责约束正确。

## 已复现发现

| 案例 | 实际行为 | 代码依据 |
| --- | --- | --- |
| `planned_subagent_started_as_main` | 计划为 `subagent`，启动传 `main_session`，成功；计划 assignment 被覆盖，没有单独 `planned_assignment` 保留旧计划。 | `server.py:563-566` 保存计划；`server.py:909-924` 创建 attempt，915 行直接覆盖节点 assignment。 |
| `planned_main_started_as_subagent` | 反方向同样成功；启动参数不必与计划 execution_mode 一致。 | `server.py:1043-1047` 只检查启动元数据；随后进入 `_begin_attempt`。 |
| `attempt_snapshot_protected_but_no_caller_scope` | 已 running 的 assignment 不可改，缺失/错误 attempt_id 被拒绝；但能读该 session 的调用者可取得当前 attempt_id，省略 assignment 直接完成该任务，还能以 subagent 配置启动原分配给主会话的兄弟叶节点。 | `server.py:1017-1031` 仅校验实例/attempt/快照；`get_progress` 自 1427 行返回节点与 attempt。函数签名没有可信 caller 或节点 scope。 |
| `subagent_attempt_starts_unbound_and_rejects_later_dispatch_binding` | 只有 execution_mode、model、reasoning_effort 即能启动；没有 agent_name、tool_identifier、dispatch_text。模拟 spawn 返回后，同 attempt 补写这些字段被拒绝：`当前 attempt 的执行快照不可修改；不一致字段：tool_identifier, dispatch_text, agent_name。` | `server.py:432-441` 仅要求 subagent 的 model/reasoning；`server.py:1027-1031` 把首次补充派发标识也当作快照变更拒绝。 |
| `worker_result_releases_dependency_without_main_acceptance` | subagent 执行快照的 producer 一次 `finished/resolved` 后，consumer 立即 ready。没有独立 review/acceptance 状态或记录，无法区分执行者上报和主会话接受。 | `server.py:586-591` 直接用 finished/resolved 放行；`server.py:1073-1081` 直接写终态；节点无 acceptance/review 字段。 |
| `subagent_assignment_can_synthesize_parent_and_root` | 子任务未终态时综合验收会拒绝；所有子任务终态后，group 和 root 均接受 `assignment.execution_mode=subagent`，创建 `kind=synthesis` attempt，最终整体 completed。 | `server.py:1058-1072` 只有子终态/依赖校验；`_begin_attempt` 不限制 synthesis 必须 main_session。 |
| `root_resolved_accepts_open_and_failed_children_without_evidence` | 一个子任务 finished/open，另一个 failed；root 仍能以 subagent assignment、空 summary、空 evidence 提交 resolved，整体状态成为 completed。 | `server.py:1058-1072` 只要求子任务 terminal，未要求子结果 resolved；`server.py:682-683` 根 outcome=resolved 直接决定整体 completed。 |

最后一个案例说明“所有子任务结束”和“用户目标验收通过”并非同一条件。某些业务可以允许非必要子任务失败后整体完成，但当前协议没有明确记录豁免、主验收决定或其证据，无法将这种合理豁免与错误收尾区分。

## 派发标识绑定的闭环缺口

`skills/track-progress/SKILL.md:132` 要求先 `update_task`、再实际派发；148 行的启动示例却填写“实际返回的子智能体标识”。服务端允许没有该标识时启动，但不允许在同 attempt 后补字段。实例和 attempt 因此可以正确创建，却缺少与真实派发返回值的结构化关联入口。脚本仅模拟一个派发返回标识，没有实际 spawn 子智能体；它证明的是 API 无法补绑定，并未把 fixture 当作真实宿主回执。

工具标识和 dispatch_text 在 spawn 前可以已知，实际 agent 标识则通常要等调用返回；不宜为了规避快照检查而预填未知 ID，也不宜把一次逻辑执行拆成虚假的重试 attempt。

## 服务端约束与身份认证边界

本轮直接 API 复现**没有认证出任何实际主会话或子智能体**。“subagent”来自 assignment 参数，代表调用方的执行配置声明，不代表可信请求主体。发现应表述为“服务端接受 subagent 声明来综合验收父/root”，而不能夸大为“已经通过真实宿主身份冒充测试”。

静态代码进一步确认：

- `shared_service.py:161-172` 校验 Host/Origin 和当前用户共享的 Bearer；172 行后放行请求。这里没有绑定 Codex thread、turn、agent 或节点授权。
- `server.py:395-429` 从工具参数规范化 assignment；`server.py:444-450` 明确把 provenance 标记为未被 Codex 宿主验证。
- `server.py:980-991` 的业务更新接口没有可信 caller 参数；`server.py:2167` 起的 `handle` 仅传入 JSON-RPC message，不传认证主体。
- `task_instance_id` 与 `attempt_id` 防止迟到、旧实例、旧尝试覆盖；它们可通过进度读取获得，不是有主体绑定的授权凭证。

因此，仅新增可由调用者填写的 `caller_role=main_session` 不能形成安全权限边界。即便暂时没有宿主认证通道，仍可独立修正业务契约：保留并约束计划 assignment；给真实派发回执独立绑定阶段；将叶执行结果与主会话接受分开；让 synthesis 的声明角色和验收证据符合规则。若要强保证“只有获派子智能体能更新自己的节点”，还需可信宿主身份/回执或由可信协调方签发并限定 session/instance/node/attempt 的能力凭证；当前代码尚未提供此机制。

## 已存在且有效的保护

本轮正向控制确认：运行中 assignment 不可改、运行中更新需要当前 attempt_id、旧 attempt 被拒绝、子任务未终态时父任务综合验收被拒绝。缺口集中在角色责任与验收状态建模，不能据此声称整个任务状态机都无约束。

本审计范围只包含已提交 0.2.0 的当前实现；没有实施修复，也没有重新运行已通过的 108 项完整回归。
