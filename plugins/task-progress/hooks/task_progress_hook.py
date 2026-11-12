#!/usr/bin/env python3
"""Emit concise, non-blocking Task Progress lifecycle guidance for Codex."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


RESPONSIBILITY = (
    " Split a leaf only into at least two distinct outcomes; if one result is useful, refine the leaf. "
    "Add a task to an existing group only for genuinely new work. Complex work defaults to subagents "
    "even when sequential; serialize shared-file owners. The main session plans and reviews; record "
    "self_execution_reason for its leaf. Run one main-session leaf per instance; finish or pause it "
    "before switching. Start the attempt, spawn, then bind. Subagents stay in scope and submit results; "
    "main session reviews it separately using queried submission/evidence and review_task."
)

REQUEST_ROUTING = (
    " Keep corrections in the current instance; answer brief questions, then return. "
    "Use route_request for independent work; registration is not dispatch. For an existing plan, query "
    "overview/tree, then select_work_context with current session_id, task_instance_id, epoch, task_id, "
    "and known attempt_id. Selection observes context; it grants no role or permission. "
    "Save goal, constraints, acceptance, and resume location with update_task_context. Resume with "
    "get_progress overview, context for task_context and session_work, then needed tree/tasks. "
    "Read all request pages before concluding; one request completing does not finish the session. "
    "Return to unfinished foreground work or activate FIFO. Honor explicit user stops. "
    "Follow only your assigned scope as a subagent."
)

SELECTIVE_QUERY = (
    " Writes return identity/status receipts only; save IDs/attempt_id, not trees. Query on demand: "
    "overview first, tree by depth, tasks by ID, records/context/history by page; full only when needed. "
    "Pin expected_state_revision across pages; restart if changed. Do not query full after every write "
    "or copy trees/history into messages. Main session: render_progress once after the first coarse plan; "
    "rerender only on explicit reopen. Subagents do not render; app reads independently."
)

STAGE_REVIEW = (
    " At stage closure, resume and reporting inspect unresolved counts, stage_inventory_due and next_actions. "
    "Use review_stage_inventory: continue/obsolete/covered with evidence; continue requires retry or replan. "
    "Refresh summaries/evidence and outcomes. Add work with addition_reason/precedes; preserve started history."
)


def _canonical_tool_name(event: dict) -> str:
    name = event.get("tool_name")
    return name.strip().lower() if isinstance(name, str) else ""


PROGRESS_TOOL_PREFIXES = ("mcp__task_progress__", "task_progress.")
DISPATCH_TOOLS = {"spawn_agent", "collaboration.spawn_agent"}
FOLLOWUP_TOOLS = {"followup_task", "collaboration.followup_task"}
WAIT_TOOLS = {
    "wait_agent", "collaboration.wait_agent", "wait_threads",
    "mcp__codex_app__wait_threads",
}
EDIT_TOOLS = {"apply_patch", "edit"}
QUERY_TOOLS = {
    "get_progress", "mcp__task_progress__get_progress", "list_mcp_resources",
    "list_mcp_resource_templates", "read_mcp_resource",
}


def classify_tool_event(event: dict) -> str:
    """Classify exact, known tool identifiers without reading shell text or transcripts."""
    name = _canonical_tool_name(event)
    if not name:
        return "unknown"
    if name.startswith(PROGRESS_TOOL_PREFIXES):
        return "progress"
    if name in DISPATCH_TOOLS:
        return "dispatch"
    if name in FOLLOWUP_TOOLS:
        return "followup"
    if name == "write_stdin":
        return "execution_poll"
    if name in WAIT_TOOLS:
        return "wait"
    if name in EDIT_TOOLS:
        return "edit"
    if name in QUERY_TOOLS:
        return "query"

    # The action field is a documented enum on this exact Dagu tool. It is
    # structured input, so it does not require guessing from command contents.
    if name == "mcp__dagu__dagu_execute":
        tool_input = event.get("tool_input")
        action = tool_input.get("action") if isinstance(tool_input, dict) else None
        if action in {"start", "enqueue", "retry"}:
            return "execution_start"
        if action in {"stop", "approve", "reject", "push_back"}:
            return "execution_control"
        return "unknown"
    if name == "mcp__dagu__dagu_read":
        return "query"
    if name in {"bash", "exec_command", "mcp__codex_app__start_task_execution"}:
        # Bash/exec_command are deliberately not inspected for read/write intent.
        return "execution_start"
    return "unknown"


def _cache_root() -> Path:
    plugin_data = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    fallback = Path(tempfile.gettempdir()) / (
        "task-progress-hooks-" + str(getattr(os, "getuid", lambda: "user")())
    )
    return (Path(plugin_data) if plugin_data else fallback) / "hook-reminders"


def _claim_marker(namespace: str, identity: list, ttl_seconds: int) -> bool | None:
    """Claim a short-lived marker atomically; None means cache unavailable."""
    values = [namespace, *identity]
    # URL-safe hashing avoids embedding host identifiers or tool arguments in paths.
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    try:
        cache = _cache_root()
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Bound cleanup work while eventually removing expired event markers.
        cutoff = time.time() - 7 * 86400
        for index, stale in enumerate(cache.iterdir()):
            if index >= 128:
                break
            try:
                if stale.is_file() and stale.stat().st_mtime < cutoff:
                    stale.unlink()
            except OSError:
                pass
        marker = cache / (namespace + "-" + digest)
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            return True
        except FileExistsError:
            try:
                if time.time() - marker.stat().st_mtime < ttl_seconds:
                    return False
                marker.unlink()
            except FileNotFoundError:
                pass
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            return True
    except OSError:
        return None


def event_replay_seen(event: dict) -> bool | None:
    """Return whether this exact hook event was seen; no tool_use_id means unknown."""
    session_id, turn_id = event.get("session_id"), event.get("turn_id")
    event_name, tool_use_id = event.get("hook_event_name"), event.get("tool_use_id")
    agent_id = event.get("agent_id")
    identity_values = (session_id, event_name)
    if not all(isinstance(value, str) and value for value in identity_values):
        return None
    if event_name == "SubagentStart":
        if not isinstance(agent_id, str) or not agent_id:
            return None
        claimed = _claim_marker("event", [session_id, agent_id, event_name], 7 * 86400)
        return None if claimed is None else not claimed
    if not all(isinstance(value, str) and value for value in (turn_id, tool_use_id)):
        return None
    claimed = _claim_marker("event", [session_id, turn_id, agent_id, tool_use_id, event_name], 7 * 86400)
    return None if claimed is None else not claimed


def reminder_budget(event: dict, reminder_kind: str, cooldown_seconds: int = 600,
                    fingerprint: str = "") -> bool:
    """Enforce per-turn and cross-turn semantic budgets; unavailable identity/cache fails silent."""
    session_id, turn_id = event.get("session_id"), event.get("turn_id")
    if not all(isinstance(value, str) and value for value in (session_id, turn_id)):
        return False
    agent_id = event.get("agent_id")
    per_turn = _claim_marker("reminder-turn", [session_id, turn_id, agent_id, reminder_kind, fingerprint], 7 * 86400)
    if per_turn is not True:
        return False
    if reminder_kind == "work-before":
        # One generic work reminder per turn, even if a more precise state
        # reminder already used the shared work-guidance slot.
        shared = _claim_marker("reminder-turn", [session_id, turn_id, agent_id, "work-guidance"], 7 * 86400)
        if shared is not True:
            return False
    elif reminder_kind not in {"dispatch-before", "followup-before", "dispatch-bind"}:
        # Specific issues retain their own budgets, but also suppress the
        # generic work reminder later in this same turn.
        _claim_marker("reminder-turn", [session_id, turn_id, agent_id, "work-guidance"], 7 * 86400)
    if cooldown_seconds <= 0:
        return True
    cooldown = _claim_marker("reminder-cooldown", [session_id, agent_id, reminder_kind, fingerprint], cooldown_seconds)
    return cooldown is True


def _issue_fingerprint(binding: dict, snapshot: dict | None = None, target: dict | None = None) -> str:
    selected = target or binding.get("active_work") or binding.get("foreground") or {}
    snapshot = snapshot or {}
    foreground = binding.get("foreground") or {}
    issue = {
        "session_id": selected.get("session_id") or snapshot.get("session_id") or foreground.get("session_id"),
        "task_instance_id": selected.get("task_instance_id") or snapshot.get("task_instance_id") or foreground.get("task_instance_id"),
        "epoch": selected.get("epoch", snapshot.get("epoch", foreground.get("epoch"))),
        "task_id": selected.get("task_id"),
        "attempt_id": selected.get("attempt_id"),
        "reason_code": snapshot.get("reason_code"),
    }
    return json.dumps(issue, sort_keys=True, separators=(",", ":"))


def successful_progress_receipt(event: dict) -> dict | None:
    """Extract only a versioned success receipt from a Task Progress PostToolUse event."""
    response = event.get("tool_response")
    if not isinstance(response, dict) or response.get("isError") is True:
        return None
    receipt = response.get("structuredContent")
    if not isinstance(receipt, dict) or receipt.get("receipt") is not True:
        return None
    version = receipt.get("receipt_version")
    operation = receipt.get("operation")
    if not isinstance(version, int) or version < 1 or not isinstance(operation, str) or not operation:
        return None
    actual_tool = _canonical_tool_name(event).split("__")[-1].split(".")[-1]
    if actual_tool != operation:
        return None
    effect = receipt.get("context_effect")
    return {
        "receipt_version": version,
        "operation": operation,
        "context_effect": effect if isinstance(effect, dict) else None,
    }


def _review_followup_context(event: dict) -> str:
    """Surface current review/synthesis work to the parent actor at safe checkpoints."""
    try:
        from hook_state import read_context, read_snapshot
        binding = read_context(event)
        if (binding.get("status") != "bound"
                or binding.get("association_source") == "host_observed_successful_dispatch"):
            return ""
        foreground = binding.get("foreground") or {}
        actor_scope = binding.get("actor_scope") or {}
        if (actor_scope.get("kind") == "dispatched_child"
                and all(actor_scope.get(key) == foreground.get(key)
                        for key in ("session_id", "task_instance_id", "epoch", "task_id"))):
            return ""
        pending_submission = binding.get("pending_submission") or {}
        if (pending_submission
                and all(pending_submission.get(key) == foreground.get(key)
                        for key in ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id"))):
            return ""
        session_id, instance_id, epoch = (foreground.get("session_id"),
                                          foreground.get("task_instance_id"),
                                          foreground.get("epoch"))
        if not (isinstance(session_id, str) and isinstance(instance_id, str)
                and isinstance(epoch, int) and not isinstance(epoch, bool)):
            return ""
        snapshot = read_snapshot(session_id, instance_id, epoch)
        if snapshot.get("status") != "current":
            return ""
        queue = snapshot.get("review_queue") or {}
        pending = queue.get("pending_review_count", 0)
        synthesis = queue.get("synthesis_ready_count", 0)
        if not (isinstance(pending, int) and pending > 0) and not (isinstance(synthesis, int) and synthesis > 0):
            return ""
        candidates = queue.get("pending_reviews") if pending else queue.get("synthesis_ready")
        candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        fingerprint = _issue_fingerprint(binding, snapshot, candidate)
        reminder_kind = "submission-review-pending" if pending else "synthesis-ready"
        if not reminder_budget(event, reminder_kind, fingerprint=fingerprint):
            return ""
        items = []
        if pending:
            items.append(f"{pending} 个子任务结果等待主会话 review_task")
        if synthesis:
            items.append(f"{synthesis} 个汇总节点可综合验收")
        return ("Task Progress: " + "；".join(items) + "。若你负责父级验收，查当前 submission/evidence 后调用 review_task 或完成综合验收。")
    except Exception:
        return ""


def successful_dispatch_agent_id(event: dict) -> str | None:
    """Read a returned agent identifier only from a structured successful result."""
    response = event.get("tool_response")
    if not isinstance(response, dict) or response.get("isError") is True:
        return None
    result = response.get("structuredContent")
    if not isinstance(result, dict):
        return None
    # Only the host's explicit agent_id is usable for a host-scope mapping.
    # agent_name is caller-facing text and may not equal the host identity.
    agent_id = result.get("agent_id")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def has_structured_terminal_result(event: dict) -> bool:
    """Recognize terminal execution metadata without interpreting output text."""
    response = event.get("tool_response")
    if not isinstance(response, dict) or response.get("isError") is True:
        return False
    result = response.get("structuredContent")
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    if isinstance(status, str) and status.lower() in {"succeeded", "success", "failed", "completed", "error"}:
        return True
    return isinstance(result.get("exit_code"), int)


def _resume_overview_query(event: dict) -> str:
    """Offer a read-only query when this actor's observed binding is still current."""
    try:
        from hook_state import read_context, read_snapshot
        binding = read_context(event)
        actor_scope = binding.get("actor_scope") or {}
        if (binding.get("status") != "bound"
                or binding.get("association_source") != "host_observed_successful_task_progress_receipt"
                or actor_scope.get("kind") == "dispatched_child"):
            return ""
        target = binding["foreground"]
        session_id = target["session_id"]
        instance_id = target["task_instance_id"]
        snapshot = read_snapshot(session_id, instance_id, target["epoch"], target.get("task_id"))
        if snapshot.get("status") != "current":
            return ""
        return json.dumps({"session_id": session_id, "task_instance_id": instance_id, "view": "overview"})
    except Exception:
        return ""


def context_for(event: dict) -> str:
    name = event.get("hook_event_name")
    source = event.get("source")
    if name == "SessionStart":
        if source == "clear":
            try:
                from hook_state import revoke_work_selection
                revoke_work_selection(event)
            except Exception:
                pass
            return (
                "Task Progress: the host session context was cleared. This does not clear stored task data. "
                "Read the current overview/context and explicitly confirm which instance and assignment to resume "
                "before substantive work; do not infer the old active attempt from the cleared context."
            )
        if source in ("resume", "compact"):
            overview_query = _resume_overview_query(event)
            identity_guidance = (
                " Read the current overview with get_progress using " + overview_query + "."
                if overview_query else
                " Recover the exact plugin IDs from the last successful receipt before calling get_progress. "
                "If the IDs cannot be recovered, leave that lookup pending."
            )
            return (
                "Task Progress: this is a resumed Codex session." + identity_guidance
                + " Reuse its task_instance_id; do not initialize a "
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
        review_context = _review_followup_context(event)
        return (
            "Task Progress: classify this request before acting. Continue the current task when it belongs "
            "there, add genuinely new scope to that plan, or route an independent request. For substantive "
            "multi-step work, record a coarse plan before editing or dispatch; simple questions and explicit "
            "stops need no new task. Reuse the current task_instance_id."
            + RESPONSIBILITY + REQUEST_ROUTING + SELECTIVE_QUERY + STAGE_REVIEW
            + (" " + review_context if review_context else "")
        )
    if name == "SubagentStart":
        replayed = event_replay_seen(event)
        if replayed is True:
            return ""
        try:
            from hook_state import read_context, read_snapshot
            binding = read_context(event)
            target = binding.get("active_work") or binding.get("foreground") or {}
            snapshot = (
                read_snapshot(target.get("session_id"), target.get("task_instance_id"),
                              target.get("epoch"), target.get("task_id"), target.get("attempt_id"))
                if binding.get("status") == "bound" and target.get("session_id")
                and target.get("task_instance_id") and isinstance(target.get("epoch"), int)
                else {"status": "unknown"}
            )
        except Exception:
            binding, snapshot, target = {}, {"status": "unknown"}, {}
        has_plugin_assignment = (
            binding.get("status") == "bound" and snapshot.get("status") == "current"
            and (snapshot.get("target") or {}).get("execution_status") == "running"
            and isinstance(target.get("task_instance_id"), str)
            and isinstance(target.get("task_id"), str)
            and isinstance(target.get("attempt_id"), str)
        )
        identity_guidance = (
            f"Use task_instance_id={target['task_instance_id']}, task_id={target['task_id']}, "
            f"attempt_id={target['attempt_id']} from the observed dispatch association."
            if has_plugin_assignment else
            "Use only the explicit assignment context; if it lacks plugin task IDs, ask the parent session to clarify instead of guessing."
        )
        return (
            "Task Progress subagent: read the assigned task and dependency evidence. " + identity_guidance +
            " Work only within that node's scope, update meaningful status changes, and submit outcome, "
            "summary and evidence when finished. The parent session separately reviews the submission. "
            "Do not create a root, render progress, accept your own result or synthesize the parent."
        )
    if name in ("PreToolUse", "PostToolUse"):
        scenario = classify_tool_event(event)

        # Task Progress writes are authoritative only through their structured
        # receipts. They never trigger ordinary reminders on their own event.
        if scenario == "progress":
            if name == "PostToolUse":
                replayed = event_replay_seen(event)
                receipt = successful_progress_receipt(event) if replayed is False else None
                if receipt:
                    try:
                        from hook_state import observe_receipt
                        observe_receipt(event, receipt)
                    except Exception:
                        pass
            return ""

        # Waiting/polling and known reads are quiet in both phases. In
        # particular, a wait response is not a new dispatch or completion.
        if scenario == "wait" and name == "PostToolUse":
            replayed = event_replay_seen(event)
            if replayed is False:
                review_context = _review_followup_context(event)
                if review_context:
                    return review_context
        if scenario in {"wait", "execution_poll", "execution_control", "query"}:
            return ""

        replayed = event_replay_seen(event)
        if replayed is not False:
            # No stable event identity or unavailable replay cache: do not turn
            # a frequent hook into a reminder on every invocation.
            return ""

        if name == "PreToolUse":
            if scenario == "dispatch":
                if not reminder_budget(event, "dispatch-before"):
                    return ""
                return (
                    "Task Progress before dispatch: confirm the intended node is ready and its attempt is "
                    "running before spawning. After successful spawn, bind the returned agent ID to that "
                    "dispatch. Registration alone is not dispatch."
                )
            if scenario == "followup":
                if not reminder_budget(event, "followup-before"):
                    return ""
                return (
                    "Task Progress before follow-up: keep the existing agent binding and assigned scope. "
                    "If this adds a distinct outcome, record that scope in the current plan before dispatch; "
                    "do not treat follow-up as a new agent or a completed task."
                )
            if scenario in {"edit", "execution_start", "unknown"}:
                try:
                    from hook_state import read_context, read_snapshot
                    binding = read_context(event)
                    if binding.get("status") == "needs_reselection" and reminder_budget(
                            event, "host-clear-reselection", fingerprint=_issue_fingerprint(binding)):
                        return "Task Progress: the host context was cleared. Read overview/context and explicitly confirm the current instance and assignment before substantive work; stored task data remains intact."
                    if binding.get("status") == "stale" and reminder_budget(
                            event, "stale-work-context", fingerprint=_issue_fingerprint(binding)):
                        return "Task Progress binding is stale. Read get_progress overview/context and explicitly reselect the current instance/epoch/attempt; do not reuse old IDs."
                    pending = binding.get("pending_submission") or {}
                    pending_snapshot = (
                        read_snapshot(pending.get("session_id"), pending.get("task_instance_id"),
                                      pending.get("epoch"), pending.get("task_id"), pending.get("attempt_id"))
                        if binding.get("status") == "bound" and pending.get("session_id")
                        and pending.get("task_instance_id") and isinstance(pending.get("epoch"), int)
                        else {"status": "unknown"}
                    )
                    if (pending_snapshot.get("status") == "current"
                            and (pending_snapshot.get("target") or {}).get("review_status") == "pending"
                            and reminder_budget(event, "submission-review-pending",
                                                fingerprint=_issue_fingerprint(binding, pending_snapshot, pending))):
                        return "Task Progress: this tracked result is pending review. If you are acting as the parent, query the submission evidence and call review_task; otherwise keep to the assigned node and wait for the parent review."
                    target = binding.get("active_work") or binding.get("foreground") or {}
                    snapshot = (
                        read_snapshot(target.get("session_id"), target.get("task_instance_id"),
                                      target.get("epoch"), target.get("task_id"), target.get("attempt_id"))
                        if binding.get("status") == "bound" and target.get("session_id")
                        and target.get("task_instance_id") and isinstance(target.get("epoch"), int)
                        else {"status": "unknown"}
                    )
                    if snapshot.get("status") == "stale":
                        if reminder_budget(event, "stale-work-context",
                                           fingerprint=_issue_fingerprint(binding, snapshot, target)):
                            return ("Task Progress context is stale (" + str(snapshot.get("reason_code", "unknown")) +"). "
                                    "Read get_progress overview/context and explicitly reselect the current instance/epoch/attempt; do not reuse the stale binding.")
                    if snapshot.get("status") == "current":
                        state = snapshot.get("target") or {}
                        if state.get("stage_inventory_due") and reminder_budget(
                                event, "stage-inventory-due", fingerprint=_issue_fingerprint(binding, snapshot, state)):
                            return "Task Progress: this tracked stage needs inventory review before moving on. Inspect current unresolved tasks and record continue/obsolete/covered with evidence."
                        if state.get("split_review_due") and reminder_budget(
                                event, "split-review-due", fingerprint=_issue_fingerprint(binding, snapshot, state)):
                            return "Task Progress: the tracked attempt needs a split/recovery review. Inspect its recent attempts, then retry or replan with evidence; do not mark an active external run complete."
                        if state.get("execution_status") == "running":
                            return ""
                        if snapshot.get("planning_status") == "required" and reminder_budget(
                                event, "planning-required", fingerprint=_issue_fingerprint(binding, snapshot, target)):
                            return "Task Progress has a tracked session but no plan yet. For substantive multi-step work, create the coarse task tree before editing or dispatch; simple answers need no task."
                        if state.get("synthesis_ready") and reminder_budget(
                                event, "synthesis-ready", fingerprint=_issue_fingerprint(binding, snapshot, state)):
                            return ("Task Progress: all tracked child results are ready for synthesis. If you are the main session that owns this parent, "
                                    "refresh the relevant summaries/evidence and record the parent result with an independent summary and evidence; "
                                    "otherwise stay within your assigned node and report completion to the parent session.")
                        if binding.get("active_work") is None and reminder_budget(
                                event, "select-ready-work", fingerprint=_issue_fingerprint(binding, snapshot, target)):
                            return "Task Progress has a tracked plan but no active attempt in this actor scope. Read the current status and ready tasks; start the correct ready leaf before substantive work, or address its dependency/review blocker first."
                except Exception:
                    # Unavailable state is unknown; preserve the generic guided reminder.
                    pass
                if not reminder_budget(event, "work-before"):
                    return ""
                return (
                    "Task Progress before work: if this operation advances a substantive goal, confirm the "
                    "current plan and the correct ready leaf/active attempt. Keep one main-session leaf "
                    "running per instance; finish or honestly pause it before switching. For an existing plan, "
                    "query its current overview/tree and select the exact target with select_work_context so later "
                    "reminders can reflect its state. Unknown tools and "
                    "shell text are not classified by inspecting their contents."
                )
            return ""

        if scenario == "dispatch":
            host_agent_id = successful_dispatch_agent_id(event)
            if host_agent_id is None:
                return ""
            association = False
            try:
                from hook_state import observe_dispatch
                association = observe_dispatch(event, host_agent_id)
            except Exception:
                pass
            if not reminder_budget(event, "dispatch-bind"):
                return ""
            if association:
                return (
                    "Task Progress after successful dispatch: bind the returned agent ID to the uniquely "
                    "prepared task/attempt. This is an observed association, not role authentication; the "
                    "parent separately reviews the submitted result before dependents proceed."
                )
            return (
                "Task Progress observed a successful dispatch ID but could not unambiguously associate it "
                "with one prepared task/attempt. Re-read the active dispatch and bind the ID explicitly; do "
                "not guess which task it belongs to."
            )
        if scenario == "execution_start" and has_structured_terminal_result(event):
            if not reminder_budget(event, "execution-terminal"):
                return ""
            return (
                "Task Progress: this tool returned structured terminal execution metadata. Verify that the "
                "actual execution receipt is recorded, then assess the task outcome separately; process "
                "success alone does not resolve the task."
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
