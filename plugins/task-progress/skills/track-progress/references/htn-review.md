### 3. 提交、复核与综合验收

子智能体用 `update_task` 提交其获派叶节点的结果（主会话也可代转原始报告，但不能省略独立复核）：

<!-- delegation-example:submit -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "attempt_id":"<当前尝试 ID>",
  "execution_status":"finished",
  "outcome":"resolved",
  "summary":"已确认根因，提交报告供主会话复核。",
  "evidence":["<文件路径、报告或可复核的结果链接>"]
}
```

此调用保存原始 `submission`，节点为 `execution_status=finished`、`outcome=null`、`review_status=pending`，显示 `awaiting_review`。执行结束计数可以增加，已解决计数和依赖不得提前放行。不能用通用 `update_task` 覆盖待验收提交、直接接受或跳过 review 重试。

主会话先用 `get_progress(view="tasks", task_ids=["inspect"])` 读取当前 submission、attempt 和 evidence，核对获派范围、文件、验证及风险；需要旧尝试时再分页读取 records。小回执只证明状态写入，不能替代证据复核。完成后调用 `review_task`：

<!-- delegation-example:review -->
```json
{
  "session_id":"<本会话 ID>",
  "task_instance_id":"<当前任务实例 ID>",
  "task_id":"inspect",
  "attempt_id":"<当前尝试 ID>",
  "decision":"accept",
  "summary":"已复核报告与证据，接受本次调查结果。",
  "evidence":["<主会话复核的证据或验证结果>"],
  "reviewer":{"execution_mode":"main_session"}
}
```

`decision=accept` 采用提交的原始 outcome，不能把 open 升级成 resolved；`reject` 保存退回理由和证据，节点为 `finished/open`，将具体修改反馈给原 agent，再依计划重试或重规划。两种决策都须非空 summary/evidence，绑定当前待复核 attempt；旧 attempt 或冲突的重复复核被拒绝。review 另存，不能覆盖执行配置、submission 或派发回执。

### 可选逐项验收契约

需要逐项证明的重要任务可在任务节点声明 `acceptance={version:1,criteria:[{id,text,method,required}]}`。初始化/分解时随节点传入；也可在尚未开始时用 `revise_task_plan` 记录修订。启动后契约进入 attempt 快照，不能原位换标准。

契约任务完成时，执行者通过 `update_task.acceptance_checks` 提交自检，主会话通过 `review_task.acceptance_checks` 独立记录复核。`resolved` 需要全部 required criterion 各有一条带证据的 `pass`；open/reject 要记录发现的问题。执行者自检不能代替主会话复核。没有契约的旧任务沿用原行为，在详情中明确显示“未使用逐项核验”，不从旧状态推断 pass。

**退回过期产物或接受 open 结果。**有契约时，`summary` 和顶层 `evidence` 之外，还需在 `acceptance_checks` 中至少记录一项发现。若源码变化使旧评审无法用于当前候选，从待复核 attempt 的契约取真实 criterion ID，提交 `{"criterion_id":"<契约中的 ID>","verdict":"not_run","note":"候选已更新，旧评审未核验新版本"}`。实际检查未通过时用 `fail` 并记录原因或证据。收到 `review_finding_required` 时按错误补齐该字段再重试。

需要跨任务独立核验时，先在提交中给产物写固定版本：`artifacts:[{id,uri,version}]`；验证提交携带 `verified_artifacts`，主会话的独立核验项引用 `checked_artifacts`，均绑定来源 task/attempt、产物 ID 与版本。只写可变路径不足以证明核验对象一致。服务端只检查账本引用，不读取或校验文件字节；版本真实性仍须由实际核验者确认。界面、HTML 与 XLSX 会保留契约、双方 checks 和产物版本引用。

**在启动目标 attempt 前规划独立核验。**若验收方法选 `independent_review`，先在同一任务分组中建立独立 verifier 叶节点，并让它依赖目标任务；验收分组必须包含目标和 verifier。目标任务与 verifier 必须分别绑定不同的实际 agent ID；目标提交固定版本的 `artifacts`，verifier 对该版本提交 `verified_artifacts`。两项结果都被接受为 `resolved` 后，主会话才在上层验收中逐项引用完全相同的产物版本。不要让目标任务的契约要求一个依赖该目标的 verifier，否则形成验收循环；把独立验收契约放在同时包含目标和 verifier 的上层结果节点。若只是由主会话检查一份评审报告，用 `inspection` 等适合的验收方法，不要把报告节点自身设为需要另一个 verifier 的 `independent_review`。契约在 attempt 启动时冻结，事后补 verifier 可能需要重规划并保留原尝试。

若启动返回 `independent_review_requires_group`，此调用没有创建 attempt。先修订计划，让核验契约位于同时包含目标和 verifier 的分组。主会话直接检查一份报告时，可以把尚未冻结的契约改为 `inspection`，再按修订后的计划派发。

若旧版本已经启动带有必需 `independent_review` 的叶任务，先用一次 `get_progress(view="tasks")` 确认当前 attempt 的冻结契约。叶节点不能同时包含目标和 verifier，换 ID 或追加同级核验任务都无法让该叶的验收通过。已有待验收提交时，用 `review_task(decision="reject")` 记录契约错配，对应 criterion 标记 `not_run` 并说明原因，保留原报告和证据。用 `rebuild_task_network` 保留旧计划后重建受影响的验收结构。若剩下的工作只是主会话查阅报告，使用 `inspection` 并引用原证据；若确实需要跨任务独立核验，在包含目标和 verifier 的分组上设置契约。

继续执行时按需读取 context、tree/tasks 或 ready 列表核对实际状态：ready 的 subagent 叶任务由主会话派发，ready 的 main_session 叶任务按自执行原因处理；pending dispatch 先绑定真实 ID，pending review 先复核，running 则等待或记录有意义进展。ready 列表为空且分组已可验收时提交综合验收，不反复尝试启动空列表；完成状态仍须核对根结果及剩余 open 项。

`pending` 是新节点初始状态，`update_task` 不接受 pending。叶任务必须先启动再完成。只有 `finished` 须传 `outcome=resolved|open`，也只有该状态可提交 `acceptance_checks`、`artifacts` 和 `verified_artifacts`。提交 `running`、`failed` 或 `cancelled` 时省略这四个字段；失败或取消仍用 `summary` 和 `evidence` 记录原因及证据。主会话自执行叶任务可直接结束，但要保留自执行原因。结果复核后再次 `get_ready_tasks`，将未解决前驱的证据传给能够处理它的后继。

有子任务的分组或 root 不直接 running，只能由主会话综合验收。所有直接子任务必须已解决且无待复核项，才可提交 `finished/resolved`；需要非空 summary/evidence 和 `assignment.execution_mode=main_session`，模型未知可省略。服务端另建 `kind=synthesis` attempt；叶节点结束百分比 100% 不代表整体完成。以 `atomic_reason` 初始化的无子节点原子 root 是叶任务：先 running，再按实际结果 finished，不走分组综合验收。

存在 open/failed/cancelled 子结果时，先处理、重试或重规划；也可在子任务均结束后如实以 `finished/open`、`failed` 或 `cancelled` 收尾，并说明证据和剩余工作，不能把未解决子结果静默视为 resolved。取消/失败保存 `kind=group_termination`，不会停止外部执行者，须先协调停止。没有运行 attempt 的分组不传旧的 attempt ID。旧的已完成/已归档记录保持可读，不虚构历史验收。

全部子项已结束（包含 cancelled/open/failed）且无待复核提交时，父级显示 `waiting_synthesis`，由主会话明确判断继续还是验收收尾。若需要保留原始终态，先读取该父节点的 `closure_review_binding`（根可用 overview，其他父用 tasks），再调用 `review_task_closure`，传 `review_id`、`source_epoch`、`source_fingerprint`、`decision=continue|accept`、非空 summary/evidence 和主会话 reviewer。continue 留在当前并明确 retry/重规划；accept 追加独立验收，允许根收尾归档，但不改原 outcome、不把取消算成功、不放行 resolved 依赖。旧 binding 会被拒绝；同 ID 同内容可重试。已收尾实例仅显式 retry 可重开，旧归档保持冻结。子智能体不得验收自己的父/root；负责验收的主会话可依据独立审查结果做决定。

工作移交到另一实例时，不用改写来源 cancelled/open。使用 `handoff_request` 将来源全部未解决叶明确绑定到承接叶的 instance/epoch/task/有效 attempt，并记录摘要和证据；承接可在当前或历史计划，未验收时来源继续可见，后续写入在承接真实 resolved 且通过验收后自动冻结闭环并归档。也支持对已经验收的历史承接补录。具体参数、身份与恢复规则见[请求分流与压缩恢复](request-routing.md)。
