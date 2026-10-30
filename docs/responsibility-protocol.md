# Task responsibility protocol (0.2.0 follow-up)

This document defines the shared backend/UI/skill contract for the responsibility fixes. These are workflow constraints, not authenticated Codex identities. Caller metadata always remains unverified by the host. Version remains 0.2.0.

## Responsibility and planning

- Preserve `planned_assignment` separately from the current attempt's `assignment`. A new attempt must use its planned `execution_mode`; it cannot silently overwrite the plan.
- A main-session leaf execution needs a nonempty `assignment.self_execution_reason`, unless the root is already explicitly atomic with an `atomic_reason`. Coordination, integration decisions, acceptance, and short atomic operations are legitimate reasons. Complex analysis, implementation, multi-file changes, specialist validation, and review default to subagents even when sequential.
- `assignment.complexity` may record `simple` or `complex`. Complex work assigned to the main session requires a specific exception reason; classification is reported by the planner, not automatically inferred or authenticated.
- Add `reassign_task(session_id, task_id, assignment, reason, reviewer, task_instance_id)` for explicit reassignment of a pending leaf. `reviewer.execution_mode` must be `main_session`, `reason` nonempty, and history retained. Never transfer a running or ended attempt through this operation. A retry uses the preserved plan; reassignment after a completed attempt requires explicit re-planning.
- Group synthesis requires `assignment.execution_mode=main_session`, a nonempty summary, and evidence when resolving. A group/root cannot resolve while any direct child is unresolved or awaiting review. Failed/cancelled children can be summarized as an open or terminated group, not silently accepted as resolved.

## Dispatch binding

- Starting a subagent attempt requires model/reasoning plus nonempty `tool_identifier` and `dispatch_text`; its actual `agent_name` can be unknown until the spawn returns.
- Record `dispatch={status: pending|bound, attempt_id, agent_name, bound_at, ...}` on the node and attempt, independently of immutable execution metadata. New spawns stay pending even if an assignment contains a planned alias. Only an explicit follow-up tool (`collaboration.followup_task`, `collaboration.send_message`, or `mcp__codex_app__send_message_to_thread`) with a known actual agent ID may bind immediately. This checks the declared operation; it does not authenticate the call.
- Add `bind_task_agent(session_id, task_id, attempt_id, agent_name, task_instance_id)` to bind the returned agent ID to an active subagent attempt once. Identical repeat binding is idempotent; different IDs and stale attempts are rejected. This binding is caller-reported, not host verified.
- A new subagent attempt cannot report finished before binding; failed/cancelled dispatch cleanup remains possible. Old in-flight attempts without this new dispatch contract remain recoverable, explicitly marked legacy/unbound, and still require review for finished results.

## Result submission and review

- Keep `update_task` as the execution update API. For a subagent leaf, `finished` with an outcome submits a result rather than accepting it: persist `submission={attempt_id,outcome,summary,evidence,submitted_at}`, set `execution_status=finished`, `outcome=null`, and `review_status=pending`.
- Awaiting review is not a satisfied task dependency, including explicit `finished` dependency policies. The execution-ended count may include it, but resolved counts and overall delivery must not. Its display status is `awaiting_review`.
- Add `review_task(session_id, task_id, attempt_id, decision, summary, evidence, reviewer, task_instance_id)`, where `decision=accept|reject`, `reviewer.execution_mode=main_session`, and summary/evidence are required. It only acts on a current pending submission. `accept` adopts the submitted outcome (never upgrades an open result); `reject` leaves the node `finished/open` for retry/re-planning.
- Store `review={attempt_id,decision,summary,evidence,reviewer,reviewed_at}`, with `review_status=accepted|rejected`. Preserve the original submission and record both in the attempt. Repeated conflicting review or review of a stale attempt is rejected. Retries/dependency invalidation clear the current review and retain its historical evidence.
- Do not let generic `update_task` overwrite a pending submission, bypass review, or silently retry unreviewed work. Main-session leaves can still complete directly, with their explicit self-execution reason recorded.
- Existing completed/archived results remain readable without inventing an acceptance record. Linear legacy workflows remain compatible and are identified as legacy; the new delegation workflow uses HTN.

## Next actions and display

- `next_actions` must reflect actual state: dispatch ready subagent leaves, perform ready main-session leaves, bind pending dispatch IDs, review submitted results, synthesize eligible groups, wait for execution, or report completion. Never suggest starting an empty ready list while waiting for synthesis.
- Ready-task text includes its planned executor. UI and offline export preserve planned executor, actual bound agent ID, main self-execution reason, submitted outcome/evidence, review decision/reviewer/evidence, and pending-review state. Review information must not overwrite execution information.
- Unknown or legacy role/acceptance information stays explicitly unrecorded; no simulated actor authentication, retroactive acceptance, or hidden role inference.

## Continuation and historical reads

- Another phase of the same goal uses the same task instance, adding nodes or rebuilding its plan. Initialization cannot replace any running attempt or pending review. Replacing another unfinished task requires a concrete `new_task_reason`, retained on its archive; this records a deliberate change of goal, not permission to bypass review or re-planning.
- Default reads and mutation responses omit historical bodies and set `history_included=false`. `history_summary` counts archived tasks, plans, and legacy tasks; absent bodies do not mean zero history. `get_progress(include_history=true)` loads history for the panel and exports. Cached history is tied to the current instance and revision; a failed or stale load must not produce a misleading complete export.
- SQLite v3 records are an independent read-only historical source. They never replace JSON state, release dependencies, synthesize accepted reviews, or become writable current tasks. A SQLite-only session is shown as a legacy archive. Dual-store differences remain visible rather than merging by title or overwriting based on timestamps.
- Compatibility reads use read-only SQLite and selected display fields, with explicit truncation limits. Missing, unsupported, or corrupt legacy data must not prevent normal JSON operations. Session clearing leaves a compatibility tombstone so subsequent reads or initialization cannot resurrect old SQLite records; the adapter does not delete the source database.
- A pending-review aggregate may coexist with running work. Display both counts, and label all-submitted results as awaiting review until review is complete. A 100% execution-ended count is never a delivery acceptance claim.

## Session requests and recoverable context (2026-10-28)

The main session owns intent classification, planning, edits, re-planning and acceptance. Task Progress persists those decisions and execution facts, checks its own transitions, and guides the next action. It cannot force unrelated host tools to follow the plan or wake an idle chat.

- Classify current-goal corrections, brief questions, independent requests and explicit stops/replacements separately. Corrections stay in the current instance; a brief answer returns to the original work. Register independent work through `route_request` in `queue` or `parallel` mode, with the actual request, rationale, constraints and acceptance criteria. Registration does not dispatch an agent.
- A session has one foreground task. Parallel requests own separate task instances and attempts; their writes cannot replace the foreground. Parallel leaf execution is delegated, with the existing binding/submission/review contract. Queued work is not executing and does not enter the foreground progress denominator.
- The foreground and request registry are persisted in one locked JSON document. Explicit FIFO activation archives and replaces the foreground in one atomic write, only after it is completed or truthfully closed. Init cannot bypass outstanding registered work. Request IDs deduplicate initial registration; stale instance and attempt IDs cannot write to another task.
- `update_request` edits only a queued request, preserving the original and revisions. `cancel_request` cancels only an unstarted request; stopping an actual executor requires coordination and a truthful task result.
- `update_task_context` records goal, constraints, acceptance criteria, resume location and a change reason. Actual request text may be recorded once; unknown historical text stays unknown. Rebuild preserves context, revisions and historical evidence; updating context does not revalidate old results against changed criteria.
- Reads return `session_work` and `task_context`. Before finishing, check outstanding foreground, queued and parallel work. `can_finish_session` means no queued/active/pending-review work, not that every result succeeded. Open, failed and cancelled results must still be reported. Explicit user stops remain authoritative.
- The UI's primary network percentage counts resolved leaves accepted under the current protocol, including explicit main-session completion. Pending review, failures and cancellations are separate. Missing historical acceptance stays unknown; root synthesis is independent of the leaf percentage.
- Foreground responses expose `parallel_tasks[]` as current network projections from the same captured document and state revision. They omit historical and recursive session bodies. The UI renders these as sibling roots after the foreground, preserving instance-specific keys, children and acceptance states; queued requests are not roots before activation. The headline progress still refers to the foreground. Cancelled requests' unexecuted nodes remain pending in storage and are displayed as unexecuted, never ready.

See [request routing guidance](../plugins/task-progress/skills/track-progress/references/request-routing.md). Current structural edits use decomposition, reassignment and explicit rebuilding; arbitrary edits to an in-flight or accepted node are not introduced here.

## Validation ownership

Backend owner: `plugins/task-progress/server.py`, Python backend tests except the policy-specific test. UI owner: `plugins/task-progress/app.html`, JavaScript browser tests/helpers. Workflow owner: skill, hooks guidance, READMEs, CHANGELOG, and policy-specific hook/skill tests. Root owns this contract and integration reports/commit. All work shares the existing worktree; no commits or installation by subagents.

Acceptance covers planned-role mismatch rejection; explicit reassignment; genuine dispatch ID binding without snapshot mutation; before/after-review dependency gates; rejection/retry/stale-review behavior; group role/evidence/unresolved-child gates; legacy reads; and visible pending/accepted/rejected review data in both the app and export.

## 阶段盘点与追加任务

主要阶段（根任务的直接子阶段）结束后，主会话应在推进下一阶段前读取 `get_progress` 的 `stage_inventory_due`，分页查看任务树并核对未解决任务的证据。对每个未解决叶任务判断继续、过期或已被后续结果覆盖，使用 `review_stage_inventory` 将 `continue`、`obsolete` 或 `covered` 及具体原因、证据或下一步绑定到该阶段的 attempt。提交使用读取时的 `state_revision`；状态变化时重新读取。盘点记录不会自动取消任务、修改依赖或把 open 改为 resolved；需要继续或关闭时按已有重试、重规划、移交和收尾规则操作。盘点提醒可恢复，但不阻止并行任务或强制停止后续执行，最终仍依赖主会话执行盘点。

使用 `decompose_task` 追加问题时，在新增节点填写 `addition_reason`，并通过 `precedes` 指定依赖这个新增节点的已有后继；新增节点自己的前置仍用 `depends_on`。服务端原子校验并更新后继依赖，界面按依赖顺序显示“追加”及原因。后继或其子任务已经开始时拒绝静默修改，须先协调并显式重规划。旧调用不提供原因时保留兼容，标记为“未记录（旧调用）”，不推测历史原因。
