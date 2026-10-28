#!/usr/bin/env python3
"""Emit concise, non-blocking Task Progress lifecycle guidance for Codex."""
import json
import sys


RESPONSIBILITY = (
    " Complex analysis, implementation, multi-file changes, specialist validation and review default "
    "to subagents even when sequential; assign a dependency chain to one agent or serialize file owners. "
    "The main session coordinates, decides and accepts results; record a specific self_execution_reason "
    "for a main-session leaf (except an explicitly atomic root). Start the attempt, spawn, then bind the "
    "returned agent ID; a subagent submits only its assigned node, and the main session reviews it separately."
)


def context_for(event: dict) -> str:
    name = event.get("hook_event_name")
    source = event.get("source")
    if name == "SessionStart":
        if source in ("resume", "compact"):
            return (
                "Task Progress: this is a resumed Codex session. Before continuing, read the current "
                "task network with get_progress and reuse its task_instance_id; do not initialize a "
                "duplicate task. If there is no active task and the new request is substantive, plan first. "
                "For multi-stage work, submit a coarse task tree before editing, running checks, or delegation. "
                "Resuming does not transfer parent ownership; keep any explicitly assigned node scope."
                + RESPONSIBILITY
            )
        if source == "fork":
            return (
                "Task Progress: this session was forked. Do not assume the parent's task ownership or "
                "create a second main-session root. Follow the explicit assignment; otherwise make a "
                "coarse plan before substantive work. Do not accept your own submission or synthesize the parent."
                + RESPONSIBILITY
            )
        return (
            "Task Progress: before substantive work, classify scope. Simple questions need no task tree; "
            "a truly atomic task needs an explicit atomic_reason; multi-stage work must start with a coarse "
            "HTN plan. Read the track-progress skill and use init_task_network with initial tasks where possible."
            + RESPONSIBILITY
        )
    if name == "UserPromptSubmit":
        return (
            "Task Progress reminder: for a new substantive goal, first make a coarse plan. Reuse the "
            "existing network for continuation, add newly requested scope to it, and use a concise atomic "
            "reason for genuinely single-step work. Do not start editing, verification, or agent dispatch "
            "before the plan is recorded. Simple conversational answers do not need a task tree."
            + RESPONSIBILITY
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
