#!/usr/bin/env python3
"""Reproduce responsibility-contract gaps using exclusively temporary data.

Role labels in these cases are tool-input claims, not authenticated agent IDs.
The script never contacts the installed daemon or changes real session data.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "plugins/task-progress/server.py"
MAIN = {"execution_mode": "main_session"}
SUB = {"execution_mode": "subagent", "agent_name": "declared-worker-a",
       "model": "audit-fixture", "reasoning_effort": "high"}


def main():
    results = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "source": str(SOURCE), "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
               "isolation": "Path.home patched during module import; SESSION_DIR explicitly set to TemporaryDirectory",
               "identity_limit": "Direct API calls carry no authenticated caller. execution_mode/agent_name are self-reported assignment fields.",
               "cases": []}
    with tempfile.TemporaryDirectory(prefix="responsibility-backend-audit-") as temporary:
        directory = Path(temporary)
        spec = importlib.util.spec_from_file_location("responsibility_audit_server", SOURCE)
        core = importlib.util.module_from_spec(spec)
        with patch.object(Path, "home", return_value=directory / "isolated-home"):
            spec.loader.exec_module(core)
        core.SESSION_DIR = directory / "sessions"
        core.SESSION_DIR.mkdir()

        def location(function):
            return {"path": str(SOURCE), "line": inspect.getsourcelines(function)[1]}

        results["source_locations"] = {name: location(getattr(core, name)) for name in (
            "_normalize_assignment", "_require_execution_metadata", "_stamp_assignment", "_begin_attempt",
            "_dependency_satisfied", "update_task", "get_progress", "handle")}

        def progress(sid):
            return core.get_progress(sid)["structuredContent"]["progress"]

        def node(sid, task_id):
            return next(item for item in progress(sid)["nodes"] if item["id"] == task_id)

        def create(title, tasks):
            return core.init_task_network(title, tasks=tasks)["structuredContent"]["session_id"]

        def update(sid, task_id, status, **kwargs):
            return core.update_task(sid, task_id, status,
                                    task_instance_id=progress(sid)["task_instance_id"], **kwargs)

        def start(sid, task_id, assignment):
            update(sid, task_id, "running", assignment=assignment)
            return node(sid, task_id)["attempt_id"]

        def finish(sid, task_id, **kwargs):
            update(sid, task_id, "finished", outcome="resolved",
                   attempt_id=node(sid, task_id)["attempt_id"], **kwargs)

        def rejected(call):
            try:
                call()
            except core.ProgressError as error:
                return {"rejected": True, "error": str(error)}
            return {"rejected": False}

        def record(name, value):
            result = {"case": name, **value}
            results["cases"].append(result)
            print(json.dumps(result, ensure_ascii=False))

        # Planned responsibility is overwritten at the first attempt, in both directions.
        for planned, actual, name in ((SUB, MAIN, "planned_subagent_started_as_main"),
                                      (MAIN, SUB, "planned_main_started_as_subagent")):
            sid = create(name, [{"id": "work", "title": "Work", "assignment": planned},
                                {"id": "other", "title": "Other", "assignment": MAIN}])
            before = deepcopy(node(sid, "work"))
            start(sid, "work", actual)
            after = node(sid, "work")
            assert after["assignment"]["execution_mode"] == actual["execution_mode"]
            record(name, {"accepted": True, "planned_assignment_before": before["assignment"],
                          "assignment_after": after["assignment"],
                          "attempt_assignment": after["attempts"][-1]["assignment"],
                          "planned_assignment_retained_separately": "planned_assignment" in after,
                          "code": location(core._begin_attempt)})

        # Snapshot consistency exists, but identity and per-node scopes do not.
        sid = create("Attempt boundary", [{"id": "owned", "title": "Worker task", "assignment": SUB},
                                          {"id": "sibling", "title": "Sibling", "assignment": MAIN}])
        aid = start(sid, "owned", SUB)
        changed = rejected(lambda: update(sid, "owned", "finished", outcome="resolved", attempt_id=aid,
                                          assignment={**SUB, "agent_name": "declared-worker-b"}))
        missing = rejected(lambda: update(sid, "owned", "finished", outcome="resolved"))
        stale = rejected(lambda: update(sid, "owned", "finished", outcome="resolved", attempt_id="stale-id"))
        # Any caller that can read this business session receives the current attempt ID.
        read_attempt = node(sid, "owned")["attempt_id"]
        update(sid, "owned", "finished", outcome="resolved", attempt_id=read_attempt,
               summary="Caller with known identifiers submits result; assignment omitted")
        start(sid, "sibling", SUB)
        assert changed["rejected"] and missing["rejected"] and stale["rejected"]
        record("attempt_snapshot_protected_but_no_caller_scope", {
            "changed_assignment": changed, "missing_attempt": missing, "stale_attempt": stale,
            "omitted_assignment_finish_accepted": node(sid, "owned")["outcome"] == "resolved",
            "sibling_start_with_subagent_claim_accepted": node(sid, "sibling")["execution_status"] == "running",
            "update_signature": str(inspect.signature(core.update_task)),
            "code": location(core.update_task)})

        # The documented update-before-spawn sequence cannot attach the agent
        # identifier obtained later without violating the immutable snapshot.
        unbound = {"execution_mode": "subagent", "model": "audit-fixture", "reasoning_effort": "high"}
        sid = create("Dispatch identifier arrives after attempt start", [
            {"id": "work", "title": "Awaiting real dispatch identifier", "assignment": unbound},
            {"id": "other", "title": "Other", "assignment": MAIN}])
        aid = start(sid, "work", unbound)
        simulated_dispatch_return = "fixture-agent-id-returned-after-start"
        binding = rejected(lambda: update(sid, "work", "running", attempt_id=aid,
            assignment={**unbound, "agent_name": simulated_dispatch_return,
                        "tool_identifier": "collaboration.spawn_agent",
                        "dispatch_text": "Fixture self-contained dispatch prompt"}))
        actual = node(sid, "work")
        assert binding["rejected"] and "agent_name" not in actual["assignment"]
        record("subagent_attempt_starts_unbound_and_rejects_later_dispatch_binding", {
            "start_without_agent_name_tool_identifier_dispatch_text_accepted": True,
            "post_spawn_agent_id_is_fixture_only": simulated_dispatch_return,
            "same_attempt_dispatch_binding": binding,
            "persisted_assignment": actual["assignment"], "code": location(core.update_task)})

        # Self-reported subagent result is already resolved and releases consumers.
        sid = create("Worker result without principal acceptance", [
            {"id": "producer", "title": "Worker result", "assignment": SUB},
            {"id": "consumer", "title": "Depends on worker result", "depends_on": ["producer"], "assignment": MAIN}])
        start(sid, "producer", SUB)
        finish(sid, "producer", summary="Worker declares done", evidence=["fixture://worker-output"])
        state = progress(sid)
        ready = core.get_ready_tasks(sid)["structuredContent"]["ready_tasks"]
        producer = node(sid, "producer")
        assert "consumer" in [item["id"] for item in ready]
        record("worker_result_releases_dependency_without_main_acceptance", {
            "producer_outcome": producer["outcome"], "attempts": producer["attempts"],
            "ready_tasks": [item["id"] for item in ready], "overall_status": state["status"],
            "review_or_acceptance_fields": sorted(key for key in producer if "accept" in key or "review" in key),
            "code": location(core._dependency_satisfied)})

        # Group and root synthesis accept a subagent assignment, independently
        # of the textual skill rule reserving acceptance for the main session.
        sid = create("Subagent-claimed parent synthesis", [
            {"id": "group", "title": "Main-owned group", "assignment": MAIN},
            {"id": "other", "title": "Other leaf", "assignment": MAIN}])
        core.decompose_task(sid, "group", [
            {"id": "child", "title": "Delegated child", "assignment": SUB}],
            task_instance_id=progress(sid)["task_instance_id"])
        premature = rejected(lambda: update(sid, "group", "finished", outcome="resolved", assignment=SUB))
        for task_id in ("child", "other"):
            start(sid, task_id, SUB)
            finish(sid, task_id, summary="Fixture output")
        update(sid, "group", "finished", outcome="resolved", assignment=SUB,
               summary="Self-reported subagent synthesizes group")
        update(sid, "root", "finished", outcome="resolved", assignment=SUB,
               summary="Self-reported subagent synthesizes root")
        group = node(sid, "group")
        root = node(sid, "root")
        assert premature["rejected"] and group["outcome"] == "resolved" and root["outcome"] == "resolved"
        record("subagent_assignment_can_synthesize_parent_and_root", {
            "children_not_terminal_guard": premature,
            "group_assignment": group["assignment"], "group_attempt_kind": group["attempts"][-1]["kind"],
            "root_assignment": root["assignment"], "root_attempt_kind": root["attempts"][-1]["kind"],
            "overall_status": progress(sid)["status"], "code": location(core.update_task)})

        # No terminal-child quality or minimum evidence gate exists at acceptance.
        sid = create("Resolved root with unresolved children", [
            {"id": "open", "title": "Open leaf", "assignment": SUB},
            {"id": "failed", "title": "Failed leaf", "assignment": SUB}])
        for task_id, status, outcome in (("open", "finished", "open"), ("failed", "failed", None)):
            aid = start(sid, task_id, SUB)
            update(sid, task_id, status, outcome=outcome, attempt_id=aid, summary="Incomplete fixture")
        update(sid, "root", "finished", outcome="resolved", assignment=SUB)
        state = progress(sid)
        root = node(sid, "root")
        assert state["status"] == "completed"
        record("root_resolved_accepts_open_and_failed_children_without_evidence", {
            "overall_status": state["status"], "root_outcome": root["outcome"],
            "root_summary": root["summary"], "root_evidence": root["evidence"],
            "child_results": [{"id": item["id"], "status": item["execution_status"], "outcome": item["outcome"]}
                              for item in state["nodes"] if item["id"] != "root"],
            "code": location(core.update_task)})
        results["real_session_data_touched"] = False
    output = Path(__file__).with_name("backend-results.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"results_path": str(output), "cases": len(results["cases"]),
                      "real_session_data_touched": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
