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

继续执行时按需读取 context、tree/tasks 或 ready 列表核对实际状态：ready 的 subagent 叶任务由主会话派发，ready 的 main_session 叶任务按自执行原因处理；pending dispatch 先绑定真实 ID，pending review 先复核，running 则等待或记录有意义进展。ready 列表为空且分组已可验收时提交综合验收，不反复尝试启动空列表；完成状态仍须核对根结果及剩余 open 项。

`pending` 是新节点初始状态，`update_task` 不接受 pending。叶任务必须先启动再完成；只有 `finished` 须传 `outcome=resolved|open`。主会话自执行叶任务可直接结束，但要保留自执行原因。失败/取消也记录 summary/evidence。结果复核后再次 `get_ready_tasks`，将未解决前驱的证据传给能够处理它的后继。

有子任务的分组或 root 不直接 running，只能由主会话综合验收。所有直接子任务必须已解决且无待复核项，才可提交 `finished/resolved`；需要非空 summary/evidence 和 `assignment.execution_mode=main_session`，模型未知可省略。服务端另建 `kind=synthesis` attempt；叶节点结束百分比 100% 不代表整体完成。以 `atomic_reason` 初始化的无子节点原子 root 是叶任务：先 running，再按实际结果 finished，不走分组综合验收。

存在 open/failed/cancelled 子结果时，先处理、重试或重规划；也可在子任务均结束后如实以 `finished/open`、`failed` 或 `cancelled` 收尾，并说明证据和剩余工作，不能把未解决子结果静默视为 resolved。取消/失败保存 `kind=group_termination`，不会停止外部执行者，须先协调停止。没有运行 attempt 的分组不传旧的 attempt ID。旧的已完成/已归档记录保持可读，不虚构历史验收。

全部子项已结束（包含 cancelled/open/failed）且无待复核提交时，父级显示 `waiting_synthesis`，由主会话明确判断继续还是验收收尾。若需要保留原始终态，先读取该父节点的 `closure_review_binding`（根可用 overview，其他父用 tasks），再调用 `review_task_closure`，传 `review_id`、`source_epoch`、`source_fingerprint`、`decision=continue|accept`、非空 summary/evidence 和主会话 reviewer。continue 留在当前并明确 retry/重规划；accept 追加独立验收，允许根收尾归档，但不改原 outcome、不把取消算成功、不放行 resolved 依赖。旧 binding 会被拒绝；同 ID 同内容可重试。已收尾实例仅显式 retry 可重开，旧归档保持冻结。子智能体不得验收自己的父/root；负责验收的主会话可依据独立审查结果做决定。

工作移交到另一实例时，不用改写来源 cancelled/open。使用 `handoff_request` 将来源全部未解决叶明确绑定到承接叶的 instance/epoch/task/有效 attempt，并记录摘要和证据；承接可在当前或历史计划，未验收时来源继续可见，后续写入在承接真实 resolved 且通过验收后自动冻结闭环并归档。也支持对已经验收的历史承接补录。具体参数、身份与恢复规则见[请求分流与压缩恢复](request-routing.md)。
