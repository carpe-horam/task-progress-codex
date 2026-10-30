#!/usr/bin/env python3
"""Emit concise, non-blocking Task Progress lifecycle guidance for Codex."""
import json
import sys


RESPONSIBILITY = (
    " Split a leaf only into at least two children with distinct outcomes. If only one child is useful, "
    "keep the original task and refine its description or dependencies; do not add a one-child wrapper "
    "or invent filler steps. Existing groups may still receive one genuinely new task at a time. "
    "Complex work defaults to subagents even when sequential; serialize shared file owners. "
    "The main session plans and accepts; record self_execution_reason for its leaf work. "
    "Start the attempt, spawn, then bind the returned ID. A subagent submits only its assigned node; "
    "the main session reviews it separately using queried submission/evidence and review_task."
)

REQUEST_ROUTING = (
    " Keep corrections in the current instance; answer brief questions then return. "
    "Use route_request for independent queue/parallel work; registration is not dispatch. "
    "Save goal, constraints, acceptance and resume location with update_task_context. "
    "Resume via get_progress overview, then context for task_context and session_work, and needed tree/tasks. "
    "Before concluding read all request pages; one request completing does not finish the session. "
    "Return to unfinished foreground work or explicitly activate FIFO after closure. "
    "Honor explicit user stops. Follow only your assigned scope as a subagent."
)

SELECTIVE_QUERY = (
    " Writes return identity/status receipts only; save top-level IDs/attempt_id, not a progress tree. "
    "Query on demand: overview first, tree by depth, tasks by ID, records/context/history by page; "
    "full only when needed. Pin expected_state_revision across pages; restart if changed. "
    "Do not query full after every write or copy task trees/history into messages. "
    "Main session: render_progress once after the first coarse plan; render again only on explicit reopen. Never re-render for resume, updates or polling. Subagents do not render. App reads independently."
)

STAGE_REVIEW = (
    " After each major stage, inventory all unresolved leaves using review_stage_inventory: continue, obsolete "
    "or covered, with reasons and evidence. Preserve outcomes. For additions supply addition_reason and "
    "precedes for pending successors; coordinate started work and replan rather than rewrite history."
)


def context_for(event: dict) -> str:
    name = event.get("hook_event_name")
    source = event.get("source")
    if name == "SessionStart":
        if source in ("resume", "compact"):
            return (
                "Task Progress: this is a resumed Codex session. Before continuing, read the current "
                "overview with get_progress and reuse its task_instance_id; do not initialize a "
                "duplicate task. If there is no active task and the new request is substantive, plan first. "
                "For multi-stage work, submit a coarse task tree before editing, running checks, or delegation. "
                "Resuming does not transfer parent ownership; keep any explicitly assigned node scope."
                + RESPONSIBILITY + REQUEST_ROUTING + SELECTIVE_QUERY + STAGE_REVIEW
            )
        if source == "fork":
            return (
                "Task Progress: this session was forked. Do not assume the parent's task ownership or "
                "create a second main-session root. Follow the explicit assignment; otherwise make a "
                "coarse plan before substantive work. Do not accept your own submission or synthesize the parent."
                + RESPONSIBILITY + REQUEST_ROUTING + SELECTIVE_QUERY + STAGE_REVIEW
            )
        return (
            "Task Progress: before substantive work, classify scope. Simple questions need no task tree; "
            "a truly atomic task needs an explicit atomic_reason; multi-stage work must start with a coarse "
            "HTN plan. Read the track-progress skill and use init_task_network with initial tasks where possible."
            + RESPONSIBILITY + REQUEST_ROUTING + SELECTIVE_QUERY + STAGE_REVIEW
        )
    if name == "UserPromptSubmit":
        return (
            "Task Progress reminder: for a new substantive goal, first make a coarse plan. Reuse the "
            "existing network for continuation, add newly requested scope to it, and use a concise atomic "
            "reason for genuinely single-step work. Do not start editing, verification, or agent dispatch "
            "before the plan is recorded. Simple conversational answers do not need a task tree."
            + RESPONSIBILITY + REQUEST_ROUTING + SELECTIVE_QUERY + STAGE_REVIEW
        )
    return ""


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        context = context_for(event)
        if not context:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event.get("hook_event_name"),
                "additionalContext": context,
            }
        }, ensure_ascii=False))
    except (json.JSONDecodeError, OSError):
        # A reminder hook must fail open; server-side planning checks remain
        # responsible for rejecting an unplanned root task.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
