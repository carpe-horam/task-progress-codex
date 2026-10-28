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
- Record `dispatch={status: pending|bound, attempt_id, agent_name, bound_at, ...}` on the node and attempt, independently of immutable execution metadata. A known actual agent ID (for follow-up) may bind immediately.
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

## Validation and file ownership

Backend owner: `plugins/task-progress/server.py`, Python backend tests except the policy-specific test. UI owner: `plugins/task-progress/app.html`, JavaScript browser tests/helpers. Workflow owner: skill, hooks guidance, READMEs, CHANGELOG, and policy-specific hook/skill tests. Root owns this contract and integration reports/commit. All work shares the existing worktree; no commits or installation by subagents.

Acceptance covers planned-role mismatch rejection; explicit reassignment; genuine dispatch ID binding without snapshot mutation; before/after-review dependency gates; rejection/retry/stale-review behavior; group role/evidence/unresolved-child gates; legacy reads; and visible pending/accepted/rejected review data in both the app and export.
