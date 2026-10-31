#!/usr/bin/env python3
"""Task Progress MCP server with session-scoped storage and an MCP App view."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
import uuid
import weakref
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX fallback
    msvcrt = None


CACHE_DIR = Path.home() / ".cache" / "task-progress"
SESSION_DIR = CACHE_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

RESOURCE_URI = "ui://task-progress/progress-inspector/v4"
LEGACY_RESOURCE_URIS = ("ui://task-progress/progress-inspector/v2", "ui://task-progress/progress-inspector/v3")
RESOURCE_MIME = "text/html;profile=mcp-app"
APP_HTML_PATH = Path(__file__).with_name("app.html")
LEGACY_DB_PATH = CACHE_DIR / "progress-v3.sqlite3"
PLUGIN_MANIFEST_PATH = Path(__file__).with_name(".codex-plugin") / "plugin.json"

VALID_STATUSES = ("pending", "in_progress", "completed")
EXECUTION_STATUSES = ("pending", "running", "finished", "failed", "cancelled")
OUTCOMES = ("resolved", "open")
DEPENDENCY_POLICIES = ("resolved", "finished")
PROFILES = ("cheap", "standard", "heavy")
EXECUTION_MODES = ("main_session", "subagent")
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
MAX_EXPORT_FILE_BYTES = 20 * 1024 * 1024
LONG_RUNNING_REVIEW_SECONDS = 10 * 60
TERMINAL_EXECUTION_STATUSES = {"finished", "failed", "cancelled"}
HANDSHAKE_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
}
STATUS_ICONS = {
    "completed": "✅",
    "in_progress": "🔄",
    "pending": "⬜",
}
# A shared daemon may see thousands of sessions. Keep locks alive only while
# acquired/waiting; callers retain a strong reference across their whole use.
_SESSION_LOCKS = weakref.WeakValueDictionary()
_SESSION_LOCKS_GUARD = threading.Lock()


class ProgressError(ValueError):
    """An expected progress operation error that should be shown to the caller."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _valid_boundary(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except (ValueError, TypeError, OverflowError):
        return False


def _ensure_time_bounds(item: dict[str, Any], boundary: str, reason: str) -> None:
    """Close missing boundaries without claiming that an unstarted task executed."""
    sources = item.get("time_boundary_sources") or {}
    for field in ("finished_at", "started_at"):
        original = item.get(field)
        if not _valid_boundary(original):
            if original:
                sources["original_" + field] = original
            item[field] = boundary if field == "finished_at" else item["finished_at"]
            sources[field] = reason if field == "finished_at" else "unknown_start_fallback"
    if sources:
        item["time_boundary_sources"] = sources


def _close_snapshot_times(data: dict[str, Any], boundary: str, reason: str) -> None:
    items = data.get("nodes", {}).values() if isinstance(data.get("nodes"), dict) else data.get("steps", [])
    for item in items:
        _ensure_time_bounds(item, boundary, reason)
        for attempt in [*item.get("attempts", []), *item.get("invalidated_results", [])]:
            _ensure_time_bounds(attempt, boundary, reason)


def _elapsed_seconds(started_at: Any, *, now: datetime | None = None) -> int | None:
    if not isinstance(started_at, str) or not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.astimezone()
        current = now or datetime.now().astimezone()
        return max(0, int((current - started).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _needs_split_review(summary: Any) -> bool:
    return not (isinstance(summary, str) and re.search(
        r"拆分复核[：:]\s*(?:已拆分|无需拆分|不拆分|已评估)", summary
    ))


def _normalize_session_id(value: Any, *, generate: bool = False) -> str:
    if value is None and generate:
        return str(uuid.uuid4())
    if not isinstance(value, str):
        raise ProgressError("缺少有效的 session_id；请使用 init_progress 返回的 ID。")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ProgressError("session_id 必须是 UUID；请使用 init_progress 返回的 ID。") from exc


def _session_file(session_id: Any) -> Path:
    return SESSION_DIR / f"{_normalize_session_id(session_id)}.json"


def _ui_view_file(session_id: Any) -> Path:
    return SESSION_DIR / f"{_normalize_session_id(session_id)}.view.json"


def _order_file(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.order.json"


def _read_order(session_id: str) -> dict[str, Any]:
    path = _order_file(session_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state_revision": 0, "view_sequence": 0}


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _advance_order(session_id: str, field: str) -> int:
    # Caller holds the session lock. Counters survive clear/reinitialization so
    # late responses cannot resurrect a cleared task or hide a newer task.
    order = _read_order(session_id)
    order[field] += 1
    _atomic_json(_order_file(session_id), order)
    return order[field]


def _normalize_view_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ProgressError("view_id 必须是 1–256 字符的非空字符串。")
    return value


@contextmanager
def _session_lock(session_id: str):
    sid = _normalize_session_id(session_id)
    with _SESSION_LOCKS_GUARD:
        local_lock = _SESSION_LOCKS.setdefault(sid, threading.RLock())
    with local_lock:
        lock_path = SESSION_DIR / f".{sid}.lock"
        with lock_path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows fallback
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows fallback
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _read(session_id: Any) -> dict[str, Any] | None:
    path = _session_file(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_history(session_id: str, include_details: bool = False) -> dict[str, Any] | None:
    # Legacy records are a separate read-only source, never current writable
    # state. A clear tombstone suppresses them even after a later JSON init.
    if _read_order(session_id).get("legacy_history_cleared"):
        return None
    try:
        if not LEGACY_DB_PATH.exists():
            return None
        spec = importlib.util.spec_from_file_location("task_progress_legacy_history", Path(__file__).with_name("legacy_history.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.read_legacy_history(LEGACY_DB_PATH, session_id, include_details=include_details)
    except Exception:
        # A broken archive must not block ordinary JSON progress or leak raw DB
        # errors/paths. The UI can still show a bounded warning.
        return {"schema": 3, "source": "sqlite_v3", "read_only": True,
                "status": "unavailable", "task_count": 0, "records": [],
                "warning": "旧版历史暂不可读取；当前 JSON 任务不受影响。"}


def _write(session_id: str, data: dict[str, Any], *, archive_before: dict[str, Any] | None = None) -> None:
    path = _session_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    data["state_revision"] = _advance_order(session_id, "state_revision")
    # The foreground and every independent request share one atomic document.
    # A parallel write only replaces its own instance, never the foreground.
    document = data
    if data.get("parallel_request_id"):
        document = _read(session_id)
        if document is None:
            raise ProgressError("会话已清理；迟到的并行结果不能重建任务。")
        state = document.get("session_work_state", {})
        iid = _task_instance_id(data)
        if iid not in state.get("instances", {}):
            raise ProgressError("并行任务实例已失效；迟到结果已拒绝。")
        state["instances"][iid] = data
        document["updated_at"] = data["updated_at"]
        document["state_revision"] = data["state_revision"]
    if document.get("session_work_state"):
        _reconcile_handoffs(document)
        # Instances remain addressable for explicit retry. Archives are frozen
        # snapshots, deduplicated by the completed root acceptance, not the iid.
        if archive_before and not any(item.get("archive_key") == archive_before["archive_key"]
                                      for item in document.get("task_history", [])):
            document.setdefault("task_history", []).append(archive_before)
        document["task_history"] = _parallel_task_history(document)
        document["session_work"] = _session_work(document)
    _atomic_json(path, document)


def _result_fingerprint(data: dict[str, Any], task_id: str | None = None) -> str:
    """Bind audit decisions to results, never to mutable display/review metadata."""
    nodes = data.get("nodes", {})
    ids = [task_id or data.get("root_id")]
    for nid in ids:
        ids.extend(child for child in nodes.get(nid, {}).get("children", []) if child not in ids)
    fields = ("id", "children", "attempt_id", "execution_status", "outcome", "review_status", "summary", "evidence")
    value = [data.get("epoch", 0), data.get("task_context", {}),
             [{key: nodes[nid].get(key) for key in fields} for nid in ids if nid in nodes]]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _closure_review(data: dict[str, Any], task_id: str | None = None) -> dict[str, Any] | None:
    nid = task_id or data.get("root_id")
    fingerprint = _result_fingerprint(data, nid)
    return next((r for r in reversed(data.get("closure_reviews", []))
                 if r["task_id"] == nid and r["source_fingerprint"] == fingerprint), None)


def _handoff_closure(data: dict[str, Any]) -> dict[str, Any] | None:
    fingerprint = _result_fingerprint(data)
    return next((r for r in reversed(data.get("handoff_closures", [])) if r["source_fingerprint"] == fingerprint), None)


def _accepted_closure(data: dict[str, Any], task_id: str | None = None) -> bool:
    review = _closure_review(data, task_id)
    return bool(review and review["decision"] == "accept")


def _node_settled(data: dict[str, Any], nid: str) -> bool:
    return _node_is_terminal(data["nodes"][nid]) or _accepted_closure(data, nid)


def _children_settled(data: dict[str, Any], nid: str) -> bool:
    children = data.get("nodes", {}).get(nid, {}).get("children", [])
    return bool(children) and all(_node_settled(data, child) for child in children)


def _closure_review_due(data: dict[str, Any]) -> bool:
    nodes = data.get("nodes", {})
    root = nodes.get(data.get("root_id"), {})
    unresolved_children = any(nodes[child].get("execution_status") != "finished"
                              or nodes[child].get("outcome") != "resolved" for child in root.get("children", []))
    unresolved_parent = root.get("execution_status") in TERMINAL_EXECUTION_STATUSES and root.get("outcome") != "resolved"
    return (root.get("outcome") != "resolved" and _children_settled(data, data.get("root_id"))
            and (unresolved_children or unresolved_parent)
            and not _accepted_closure(data) and not _handoff_closure(data))


def _task_closed(data: dict[str, Any]) -> bool:
    if data.get("schema_version") != 2:
        return _stats(data)[3] == "completed"
    nodes = data.get("nodes", {})
    root = nodes.get(data.get("root_id"), {})
    return ((root.get("execution_status") in TERMINAL_EXECUTION_STATUSES or _accepted_closure(data))
            and not any(n.get("execution_status") == "running" or n.get("review_status") == "pending"
                        for n in nodes.values())
            and (root.get("outcome") == "resolved" or bool(root.get("summary", "").strip()) or _accepted_closure(data)))


def _inherit_work_state(previous: dict[str, Any] | None, data: dict[str, Any]) -> None:
    if not previous or not previous.get("session_work_state"):
        return
    state = deepcopy(previous["session_work_state"])
    settled = _session_work(previous)["requests"]
    for item in state["requests"]:
        snapshot = next((r for r in settled if r["request_id"] == item["request_id"]), None)
        if snapshot:
            item.update(status=snapshot["status"], summary=snapshot["summary"], context_brief=snapshot["context_brief"])
    data["session_work_state"] = state
    _ensure_work_state(data)


def _request_status(data: dict[str, Any]) -> str:
    if _handoff_closure(data):
        return "handed_off"
    if _accepted_closure(data):
        return "closed"
    if data.get("request_cancelled"):
        return "cancelled"
    if any(n.get("review_status") == "pending" for n in data.get("nodes", {}).values()):
        return "pending_review"
    if not _task_closed(data):
        return "active"
    if data.get("schema_version") != 2:
        return "completed"
    root = data["nodes"][data["root_id"]]
    return ("completed" if root.get("outcome") == "resolved" else
            "open" if root.get("execution_status") == "finished" else root["execution_status"])


def _ensure_work_state(data: dict[str, Any]) -> dict[str, Any]:
    state = data.setdefault("session_work_state", {"requests": [], "instances": {}, "managed": False})
    data.setdefault("task_context", {"goal": data.get("task_title", ""), "constraints": [],
        "acceptance_criteria": [], "resume_note": ""})
    iid = _task_instance_id(data)
    if not any(r.get("task_instance_id") == iid for r in state["requests"]):
        state["requests"].append({
            "request_id": f"foreground:{iid}", "task_instance_id": iid,
            "task_title": data.get("task_title", ""), "goal": data.get("task_title", ""),
            "request_text": data.get("original_request_text"),
            "context_source": "explicit_request_text" if data.get("original_request_text") is not None else "request_text_unrecorded",
            "mode": "foreground", "reason": "恢复已有主任务，不补造历史请求原文。",
            "acceptance_criteria": [], "constraints": [], "context_brief": {},
            "return_to": None, "created_at": data.get("created_at"),
        })
    return state


def _session_work(document: dict[str, Any]) -> dict[str, Any]:
    state = document.get("session_work_state", {})
    iid = _task_instance_id(document)
    requests = []
    for original in state.get("requests", []):
        item = deepcopy(original)
        item["origin_return_to"] = deepcopy(original.get("origin_return_to", original.get("return_to")))
        instance = document if item.get("task_instance_id") == iid else state.get("instances", {}).get(item.get("task_instance_id"))
        if instance is not None:
            item["status"] = _request_status(instance)
            root = instance.get("nodes", {}).get(instance.get("root_id"), {})
            item["summary"] = root.get("summary", "")
            item["task_title"] = instance.get("task_title", item["task_title"])
            item["context_brief"] = deepcopy(instance.get("task_context", item.get("context_brief", {})))
            item["closure_review_due"] = _closure_review_due(instance)
            for key in ("goal", "constraints", "acceptance_criteria"):
                if key in instance.get("task_context", {}):
                    item[key] = deepcopy(instance["task_context"][key])
        else:
            item.setdefault("status", "queued")
            item.setdefault("summary", "")
        # Internal deduplication material and unbounded edit logs are not UI data.
        for key in ("fingerprint_hash", "revisions"):
            item.pop(key, None)
        requests.append(item)
    foreground = next((r for r in requests if r.get("task_instance_id") == iid), None)
    active = [r for r in requests if r["status"] in ("queued", "active", "pending_review") or r.get("closure_review_due")]
    anchor = {"task_instance_id": iid, "task_id": document.get("root_id"),
              "resume_note": document.get("task_context", {}).get("resume_note", "")}
    return_to = anchor if foreground and foreground["status"] in ("active", "pending_review") else None
    for item in requests:
        # The original insertion relationship is history. The next action must
        # return to the live foreground, never to an already archived task.
        item["return_to"] = deepcopy(return_to) if item.get("task_instance_id") != iid else None
    actions = []
    if return_to:
        actions.append(f"返回主任务 {document.get('task_title', '')}（{iid}）；插入请求结束不代表原任务完成。")
    for item in active:
        if item.get("closure_review_due"):
            actions.append(f"请求 {item['request_id']} 的子任务已结束；主会话 review_task_closure 决定继续/重试或验收收尾，不将取消结果算作成功。")
        elif item["status"] == "queued":
            actions.append(f"请求 {item['request_id']} 仅排队；主任务如实收尾后显式 activate_request，按 FIFO 激活。")
        elif item["status"] == "pending_review":
            actions.append(f"主会话独立验收请求 {item['request_id']} 的待复核结果。")
        elif item.get("mode") == "parallel":
            actions.append(f"按实例 {item['task_instance_id']} 继续规划/派发并行请求 {item['request_id']}；Task Progress 不自动启动执行。")
    return {"foreground": foreground, "requests": requests, "can_finish_session": not active,
            "return_to": return_to, "next_actions": actions,
            "verification": "caller_declared_not_host_authenticated"}


def _task_from_document(document: dict[str, Any] | None, task_instance_id: str | None, *, writable: bool = True):
    if document is None:
        if task_instance_id is not None:
            raise ProgressError("任务实例不存在或已清理；迟到请求已拒绝。")
        return None
    if task_instance_id is None or task_instance_id == _task_instance_id(document):
        data = document
    else:
        data = document.get("session_work_state", {}).get("instances", {}).get(task_instance_id)
        if data is None:
            raise ProgressError("task_instance_id 不匹配；旧任务或其他实例的请求已拒绝。")
    if writable and data.get("request_cancelled"):
        raise ProgressError("请求已取消；迟到更新不能恢复执行。")
    if writable and _parallel_is_archivable(data):
        raise ProgressError("并行任务已完成并归档；仅显式 running/retry=true 可重新执行，迟到更新已拒绝。")
    return data


def _select_task(sid: str, task_instance_id: str | None, *, writable: bool = True):
    return _task_from_document(_read(sid), task_instance_id, writable=writable)


def _capture_response_snapshot(sid: str) -> dict[str, Any]:
    """Caller holds the session lock: allocate ordering for this exact state."""
    document = _read(sid)
    return {"document": document, "view_sequence": _advance_order(sid, "view_sequence"),
            "state_revision": _read_order(sid)["state_revision"]}


def _foreground_request(sid: str, task_instance_id: str):
    data = _read(sid)
    if data is None or not task_instance_id or task_instance_id != _task_instance_id(data):
        raise ProgressError("必须提供当前 foreground 的 task_instance_id；迟到的请求管理操作已拒绝。")
    return data, _ensure_work_state(data)


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ProgressError(f"{name} 必须是非空字符串（最多12000字符）。")
    return value.strip()


def _context_list(value: Any, name: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 64 or (required and not value):
        raise ProgressError(f"{name} 必须是字符串列表（最多64项）。")
    return [_nonempty(item, name) for item in value]


def route_request(session_id: str, task_instance_id: str, request_id: str, request_text: str,
                  task_title: str, mode: str, reason: str, acceptance_criteria: list[str],
                  constraints: list[str] | None = None, context_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    rid = _nonempty(request_id, "request_id")
    if len(rid) > 256 or mode not in ("queue", "parallel"):
        raise ProgressError("request_id 最多256字符；mode 必须为 queue 或 parallel。")
    _nonempty(request_text, "request_text")
    original_text = request_text
    title, why = _nonempty(task_title, "task_title"), _nonempty(reason, "reason")
    criteria = _context_list(acceptance_criteria, "acceptance_criteria", required=True)
    limits = _context_list(constraints if constraints is not None else [], "constraints")
    if context_brief is not None and (not isinstance(context_brief, dict) or len(json.dumps(context_brief, ensure_ascii=False)) > 16000):
        raise ProgressError("context_brief 必须为有限的显式上下文对象（最多16000字符）。")
    content = {"request_text": original_text, "task_title": title, "mode": mode, "reason": why,
               "acceptance_criteria": criteria, "constraints": limits, "context_brief": context_brief or {}}
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with _session_lock(sid):
        data, state = _foreground_request(sid, task_instance_id)
        existing = next((r for r in state["requests"] if r["request_id"] == rid), None)
        tombstone = _read_order(sid).get("request_tombstones", {}).get(rid)
        if tombstone:
            raise ProgressError("request_id 已清理；旧登记不能重建工作。")
        if existing:
            if existing.get("fingerprint_hash") != fingerprint:
                raise ProgressError("request_id 已用于不同请求内容；请为新请求使用新的 ID。")
        else:
            state["managed"] = True
            item = {**deepcopy(content), "goal": title, "request_id": rid, "task_instance_id": None,
                    "fingerprint_hash": fingerprint, "status": "queued", "created_at": _now(),
                    "return_to": {"task_instance_id": task_instance_id, "task_id": data.get("root_id")}}
            if mode == "parallel":
                network = _new_network(sid, title)
                network["parallel_request_id"] = rid
                network["original_request_text"] = original_text
                network["task_context"] = {"goal": title, "constraints": limits,
                    "acceptance_criteria": criteria, "resume_note": "", "provided_context": deepcopy(context_brief or {})}
                item.update(task_instance_id=_task_instance_id(network), status="active")
                state["instances"][_task_instance_id(network)] = network
            state["requests"].append(item)
            _write(sid, data)
    return get_progress_view(sid)


def activate_request(session_id: str, task_instance_id: str, request_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    # A lost successful response may be retried with the old foreground ID.
    # Return the already activated instance without consuming another queue item.
    with _session_lock(sid):
        current = _read(sid)
        item = next((r for r in (current or {}).get("session_work_state", {}).get("requests", [])
                     if r["request_id"] == request_id), None)
        replay = bool(current and item and item.get("activated_from") == task_instance_id
                      and item.get("task_instance_id") == _task_instance_id(current))
    if replay:
        return get_progress_view(sid)
    with _session_lock(sid):
        data, state = _foreground_request(sid, task_instance_id)
        queued = [r for r in state["requests"] if r.get("status") == "queued"]
        if not queued or queued[0]["request_id"] != request_id:
            raise ProgressError("只能显式激活 FIFO 队首的 queued 请求。")
        if not _task_closed(data) or _closure_review_due(data):
            raise ProgressError("原主任务尚未完成或如实收尾；排队请求不能抢占当前工作。")
        # Snapshot the settled foreground entry before replacing its network.
        summary = _session_work(data)
        settled = next(r for r in summary["requests"] if r.get("task_instance_id") == task_instance_id)
        old = next(r for r in state["requests"] if r.get("task_instance_id") == task_instance_id)
        old.update(status=settled["status"], summary=settled["summary"], context_brief=settled["context_brief"])
        item = queued[0]
        replacement = _new_network(sid, item["task_title"])
        replacement["original_request_text"] = item["request_text"]
        replacement["task_history"] = _archive_current_task(data, f"显式激活排队请求 {request_id}")
        replacement["task_context"] = {"goal": item["goal"], "constraints": item["constraints"],
            "acceptance_criteria": item["acceptance_criteria"], "resume_note": "", "provided_context": item["context_brief"]}
        item.update(task_instance_id=_task_instance_id(replacement), status="active", activated_at=_now(),
                    activated_from=task_instance_id)
        replacement["session_work_state"] = state
        _write(sid, replacement)
    return get_progress_view(sid)


def update_request(session_id: str, task_instance_id: str, request_id: str, reason: str,
                   task_title: str | None = None, constraints: list[str] | None = None,
                   acceptance_criteria: list[str] | None = None, context_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    why = _nonempty(reason, "reason")
    with _session_lock(sid):
        data, state = _foreground_request(sid, task_instance_id)
        item = next((r for r in state["requests"] if r["request_id"] == request_id), None)
        if item is None or item.get("status") != "queued":
            raise ProgressError("update_request 仅调整尚未启动的 queued 请求；当前实例请 update_task_context/rebuild。")
        changes = {}
        if task_title is not None:
            changes.update(task_title=_nonempty(task_title, "task_title"), goal=_nonempty(task_title, "task_title"))
        if constraints is not None:
            changes["constraints"] = _context_list(constraints, "constraints")
        if acceptance_criteria is not None:
            changes["acceptance_criteria"] = _context_list(acceptance_criteria, "acceptance_criteria", required=True)
        if context_brief is not None:
            if not isinstance(context_brief, dict) or len(json.dumps(context_brief, ensure_ascii=False)) > 16000:
                raise ProgressError("context_brief 必须是有限的显式上下文对象。")
            changes["context_brief"] = deepcopy(context_brief)
        item.setdefault("revisions", []).append({"reason": why, "changed_at": _now(),
            "previous": {key: deepcopy(item.get(key)) for key in changes}})
        item.update(changes)
        _write(sid, data)
    return get_progress_view(sid)


def cancel_request(session_id: str, task_instance_id: str, request_id: str, reason: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    why = _nonempty(reason, "reason")
    with _session_lock(sid):
        data, state = _foreground_request(sid, task_instance_id)
        item = next((r for r in state["requests"] if r["request_id"] == request_id), None)
        network = state["instances"].get(item.get("task_instance_id")) if item else None
        if item is None or (item.get("status") != "queued" and network is None):
            raise ProgressError("仅可取消未启动的排队/并行请求。")
        if network and (network.get("request_cancelled") or any(n.get("attempt_id") or n.get("attempts")
                      for n in network.get("nodes", {}).values())):
            raise ProgressError("请求已启动或已取消；本工具不会假装停止外部执行者，请协调执行并如实收尾。")
        item.update(status="cancelled", summary=why, cancelled_at=_now(), cancellation_reason=why)
        if network:
            network["request_cancelled"] = True
            network["nodes"][network["root_id"]].update(execution_status="cancelled", summary=why)
            _close_snapshot_times(network, item["cancelled_at"], "request_cancel_fallback")
        _write(sid, data)
    return get_progress_view(sid)


def _handoff_target(document: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """Resolve the effective node in a live or frozen plan; never fish old attempts."""
    iid, epoch = mapping["target_task_instance_id"], mapping["target_epoch"]
    instance = document if iid == _task_instance_id(document) else document.get("session_work_state", {}).get("instances", {}).get(iid)
    if instance is None:
        instance = next((t for t in reversed(document.get("task_history", [])) if t.get("task_instance_id") == iid), None)
    if not instance or instance.get("schema_version") != 2 or instance.get("session_id") != document["session_id"]:
        raise ProgressError("承接实例不存在于本会话，不能跨会话移交。")
    plan = instance if instance.get("epoch", 0) == epoch else next(
        (p for p in instance.get("history", []) if p.get("epoch") == epoch), None)
    nodes = plan.get("nodes", {}) if plan else {}
    indexed = nodes if isinstance(nodes, dict) else {n["id"]: n for n in nodes}
    node = indexed.get(mapping["target_task_id"])
    if not node or node.get("children") or node.get("attempt_id") != mapping["target_attempt_id"]:
        raise ProgressError("承接叶/epoch/当前有效 attempt 不匹配；不能使用旧尝试或分组结果。")
    if node.get("invalidated_by") or node.get("execution_status") not in ("running", "finished"):
        raise ProgressError("承接尝试已失效、失败或取消；须显式绑定新的有效尝试。")
    if node.get("review_status") == "rejected" or (node.get("execution_status") == "finished" and node.get("outcome") == "open"):
        raise ProgressError("承接结果被退回或仍未解决，不能作为移交验收依据。")
    return node


def _handoff_target_accepted(node: dict[str, Any]) -> bool:
    if node.get("execution_status") != "finished" or node.get("outcome") != "resolved" or not node.get("summary") or not node.get("evidence"):
        return False
    mode = node.get("assignment", {}).get("execution_mode")
    if mode == "main_session":
        return node.get("review_status") not in ("pending", "rejected")
    submission, review = node.get("submission") or {}, node.get("review") or {}
    return (mode == "subagent" and node.get("review_status") == "accepted"
            and submission.get("attempt_id") == node.get("attempt_id") and submission.get("outcome") == "resolved"
            and review.get("attempt_id") == node.get("attempt_id") and review.get("decision") == "accept"
            and bool(review.get("summary")) and bool(review.get("evidence"))
            and review.get("reviewer", {}).get("execution_mode") == "main_session")


def _handoff_source_ready(source: dict[str, Any]) -> bool:
    return (_task_closed(source) and all(_node_is_terminal(n) for n in source.get("nodes", {}).values()))


def _reconcile_handoffs(document: dict[str, Any]) -> None:
    """Mutation-only reconciliation. Closure proofs and earlier archives stay frozen."""
    for source in document.get("session_work_state", {}).get("instances", {}).values():
        if _handoff_closure(source) or not _handoff_source_ready(source):
            continue
        fingerprint = _result_fingerprint(source)
        for handoff in source.get("handoffs", []):
            if handoff["source_fingerprint"] != fingerprint:
                continue
            proofs = []
            try:
                for mapping in handoff["mappings"]:
                    node = _handoff_target(document, mapping)
                    if not _handoff_target_accepted(node):
                        break
                    proofs.append({**deepcopy(mapping), "accepted_result": deepcopy(node)})
                else:
                    closed_at = _now()
                    _close_snapshot_times(source, closed_at, "handoff_fallback")
                    source.setdefault("handoff_closures", []).append({
                        "handoff_id": handoff["handoff_id"], "source_fingerprint": fingerprint,
                        "source_epoch": source.get("epoch", 0), "closed_at": closed_at,
                        "summary": handoff["summary"], "evidence": deepcopy(handoff["evidence"]),
                        "reviewer": deepcopy(handoff["reviewer"]), "accepted_targets": proofs})
                    break
            except ProgressError:
                # Rejected/retried targets cannot close the source. A fresh explicit
                # registration is required; no fallback to a formerly good attempt.
                continue


def handoff_request(session_id: str, task_instance_id: str, request_id: str, handoff_id: str,
                    source_task_instance_id: str, source_epoch: int, source_fingerprint: str, mappings: list[dict[str, Any]],
                    summary: str, evidence: list[str], reviewer: dict[str, Any]) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    hid = _nonempty(handoff_id, "handoff_id")
    _query_integer(source_epoch, "source_epoch", 0)
    _require_result_evidence(summary, evidence)
    actor = _reviewer(reviewer, scope="handoff_review")
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= 200:
        raise ProgressError("mappings 须逐项覆盖全部未解决来源叶（1..200项）。")
    fields = {"source_task_id", "source_attempt_id", "target_task_instance_id", "target_epoch", "target_task_id", "target_attempt_id"}
    normalized = []
    for m in mappings:
        if not isinstance(m, dict) or set(m) != fields:
            raise ProgressError("每项mapping必须精确提供来源task/attempt及承接instance/epoch/task/attempt。")
        for key in fields - {"source_attempt_id", "target_epoch"}:
            _nonempty(m[key], key)
        if m["source_attempt_id"] is not None:
            _nonempty(m["source_attempt_id"], "source_attempt_id")
        _query_integer(m["target_epoch"], "target_epoch", 0)
        normalized.append(deepcopy(m))
    normalized.sort(key=lambda m: m["source_task_id"])
    content = dict(request_id=request_id, source_task_instance_id=source_task_instance_id, source_epoch=source_epoch,
                   source_fingerprint=source_fingerprint,
                   mappings=normalized, summary=summary.strip(), evidence=list(evidence), reviewer=actor)
    with _session_lock(sid):
        document, state = _foreground_request(sid, task_instance_id)
        request = next((r for r in state["requests"] if r["request_id"] == request_id and r.get("mode") == "parallel"), None)
        source = state["instances"].get(source_task_instance_id)
        if not request or request.get("task_instance_id") != source_task_instance_id or not source or source.get("request_cancelled"):
            raise ProgressError("必须绑定本会话已启动的来源parallel请求及其实际实例。")
        previous = next((h for h in source.get("handoffs", []) if h["handoff_id"] == hid), None)
        if previous:
            if any((_normalize_assignment(previous.get(key)) != _normalize_assignment(value) if key == "reviewer"
                    else previous.get(key) != value) for key, value in content.items()):
                raise ProgressError("handoff_id 已用于不同移交内容。")
            # Lost receipts are safe to replay, including after closure/retry.
            return _tool_result("返回原移交登记回执。", _progress_payload(document), sid,
                                response_snapshot=_capture_response_snapshot(sid))
        if (source.get("epoch", 0) != source_epoch or source_fingerprint != _result_fingerprint(source)
                or _parallel_is_archivable(source) or not _handoff_source_ready(source)):
            raise ProgressError("来源轮次不匹配、已归档或尚未如实收尾；运行/待复核/未结束来源不能移交。")
        unresolved = {nid: n for nid, n in source["nodes"].items()
                      if not n.get("children") and not (n.get("execution_status") == "finished" and n.get("outcome") == "resolved")}
        if not unresolved or len(normalized) != len(unresolved) or {m["source_task_id"] for m in normalized} != set(unresolved):
            raise ProgressError("移交必须完整覆盖且仅覆盖全部未解决叶；不能遗漏或重复。")
        edges: dict[str, set[str]] = {}
        for instance in state["instances"].values():
            for h in instance.get("handoffs", []):
                edges.setdefault(_task_instance_id(instance), set()).update(m["target_task_instance_id"] for m in h["mappings"])
        for mapping in normalized:
            if unresolved[mapping["source_task_id"]].get("attempt_id") != mapping["source_attempt_id"]:
                raise ProgressError("来源attempt已改变，不能移交旧结果。")
            target_id = mapping["target_task_instance_id"]
            seen, pending = set(), [target_id]
            while pending:
                iid = pending.pop()
                if iid == source_task_instance_id:
                    raise ProgressError("移交不能自指或形成跨实例环；闭环只采用真实已验收叶结果。")
                if iid not in seen:
                    seen.add(iid)
                    pending.extend(edges.get(iid, ()))
            _handoff_target(document, mapping)
        source.setdefault("handoffs", []).append({**content, "handoff_id": hid,
            "source_fingerprint": _result_fingerprint(source), "registered_at": _now()})
        _write(sid, document)
    return get_progress_view(sid)


def review_task_closure(session_id: str, task_instance_id: str, task_id: str, review_id: str,
                        source_epoch: int, source_fingerprint: str, decision: str, summary: str,
                        evidence: list[str], reviewer: dict[str, Any]) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    rid = _nonempty(review_id, "review_id")
    _query_integer(source_epoch, "source_epoch", 0)
    if decision not in ("continue", "accept"):
        raise ProgressError("收尾验收decision须为continue或accept；accept不改变原outcome。")
    _require_result_evidence(summary, evidence)
    actor = _reviewer(reviewer, scope="closure_review")
    content = dict(task_id=task_id, source_epoch=source_epoch, source_fingerprint=source_fingerprint,
                   decision=decision, summary=summary.strip(), evidence=list(evidence), reviewer=actor)
    with _session_lock(sid):
        document = _read(sid)
        data = _task_from_document(document, task_instance_id, writable=False)
        if not data or task_instance_id != _task_instance_id(data):
            raise ProgressError("收尾验收须明确指定本会话当前或并行实例。")
        previous = next((r for r in data.get("closure_reviews", []) if r["review_id"] == rid), None)
        if previous:
            if any((_normalize_assignment(previous.get(key)) != _normalize_assignment(value) if key == "reviewer"
                    else previous.get(key) != value) for key, value in content.items()):
                raise ProgressError("review_id已用于不同收尾验收。")
            return _tool_result("返回原收尾验收回执。", _progress_payload(data), sid,
                                response_snapshot=_capture_response_snapshot(sid))
        if _parallel_is_archivable(data) or data.get("request_cancelled"):
            raise ProgressError("实例已收尾归档；只有显式重试可重开。")
        node = data.get("nodes", {}).get(task_id)
        if source_epoch != data.get("epoch", 0) or source_fingerprint != _result_fingerprint(data, task_id):
            raise ProgressError("来源epoch/fingerprint已改变；重新读取结果后再验收，不能接受迟到决定。")
        if not node or not _children_settled(data, task_id):
            raise ProgressError("父级收尾验收要求所有直接子项已结束且无pending submission review。")
        if node.get("execution_status") == "finished" and node.get("outcome") == "resolved":
            raise ProgressError("父级已经resolved；不能追加矛盾的继续/收尾决定。")
        # Check the complete subtree, not just terminal direct groups.
        pending = [task_id]
        for nid in pending:
            current = data["nodes"][nid]
            if current.get("execution_status") == "running" or current.get("review_status") == "pending":
                raise ProgressError("仍有运行/待复核结果，不能进行收尾验收。")
            if not current.get("children") and not _node_is_terminal(current):
                raise ProgressError("子树仍有未执行叶，不能用父级收尾验收跳过。")
            pending.extend(current.get("children", []))
        reviewed_at = _now()
        if decision == "accept":
            for nid in pending:
                _ensure_time_bounds(data["nodes"][nid], reviewed_at, "closure_fallback")
        data.setdefault("closure_reviews", []).append({**content, "review_id": rid, "epoch": data.get("epoch", 0),
            "reviewed_at": reviewed_at})
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
    return get_progress_view(sid, task_instance_id=task_instance_id)


def update_task_context(session_id: str, task_instance_id: str, goal: str, constraints: list[str],
                        acceptance_criteria: list[str], resume_note: str, reason: str,
                        request_text: str | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    context = {"goal": _nonempty(goal, "goal"), "constraints": _context_list(constraints, "constraints"),
               "acceptance_criteria": _context_list(acceptance_criteria, "acceptance_criteria", required=True),
               "resume_note": _nonempty(resume_note, "resume_note")}
    why = _nonempty(reason, "reason")
    if request_text is not None:
        _nonempty(request_text, "request_text")
    if not task_instance_id:
        raise ProgressError("更新上下文必须指定 task_instance_id。")
    with _session_lock(sid):
        data = _select_task(sid, task_instance_id)
        if request_text is not None:
            if data.get("original_request_text") not in (None, request_text):
                raise ProgressError("已记录的请求原文不可覆盖；目标调整请保留原文并更新上下文。")
            data["original_request_text"] = request_text
        if not data.get("parallel_request_id"):
            state = _ensure_work_state(data)
            state["managed"] = True
            if request_text is not None:
                item = next(r for r in state["requests"] if r.get("task_instance_id") == task_instance_id)
                item.update(request_text=request_text, context_source="explicit_request_text")
        data.setdefault("context_revisions", []).append({"previous": deepcopy(data.get("task_context")),
            "reason": why, "changed_at": _now()})
        data["task_context"] = {**data.get("task_context", {}), **context}
        _write(sid, data)
    return get_progress_view(sid, task_instance_id=task_instance_id)


def _stats(data: dict[str, Any]) -> tuple[int, int, int, str]:
    total = len(data["steps"])
    completed = sum(1 for step in data["steps"] if step["status"] == "completed")
    percent = int(completed / total * 100) if total else 0
    if total > 0 and completed == total:
        status = "completed"
    elif completed > 0 or any(step["status"] == "in_progress" for step in data["steps"]):
        status = "in_progress"
    else:
        status = "pending"
    return total, completed, percent, status


def _task_instance_id(data: dict[str, Any]) -> str:
    # Legacy sessions receive a stable identity without rewriting their cache on read.
    return data.get("task_instance_id") or str(uuid.uuid5(
        uuid.UUID(data["session_id"]), f"legacy-task:{data.get('created_at', '')}"
    ))


def _validate_task_instance(data: dict[str, Any], task_instance_id: str | None) -> None:
    order = _read_order(data["session_id"])
    if (task_instance_id is None and not data.get("task_history")
            and not data.get("session_work_state", {}).get("managed")
            and not order.get("require_task_instance_id", False)):
        return
    if task_instance_id != _task_instance_id(data):
        raise ProgressError(
            "task_instance_id 缺失或不匹配；旧任务的迟到更新已拒绝。"
            "请先 get_progress 读取当前任务的 task_instance_id。"
        )


def _assert_can_replace_task(data: dict[str, Any] | None, new_task_reason: str | None) -> None:
    if new_task_reason is not None and (not isinstance(new_task_reason, str) or not new_task_reason.strip()):
        raise ProgressError("new_task_reason 必须说明用户的新目标及旧任务剩余工作的处理方式。")
    if data is None:
        return
    if data.get("session_work_state", {}).get("managed"):
        if not _task_closed(data):
            raise ProgressError("受控会话仍有未完成主任务；请继续、重规划或如实收尾，不能通过初始化绕过。")
        if not _session_work(data)["can_finish_session"]:
            raise ProgressError("会话仍有排队/并行/待验收请求；使用显式请求管理入口，不能通过初始化绕过。")
    _assert_no_pending_reviews(data, "替换任务")
    if data.get("schema_version") == 2:
        running = [node["id"] for node in data.get("nodes", {}).values() if node.get("execution_status") == "running"]
        complete = data.get("nodes", {}).get(data.get("root_id"), {}).get("outcome") == "resolved" or _accepted_closure(data)
    else:
        running = [str(index) for index, step in enumerate(data.get("steps", [])) if step.get("status") == "in_progress"]
        complete = _stats(data)[3] == "completed"
    if running:
        raise ProgressError("当前任务仍有运行中的执行尝试（" + ", ".join(running) + "）；请先协调执行者并结束当前 attempt，再开始新目标。new_task_reason 不能绕过此保护。")
    if not complete and not new_task_reason:
        raise ProgressError("当前任务尚未完成；继续目标请 get_progress 后 decompose_task，重规划请 rebuild_task_network。只有用户明确开启新目标时，才传 new_task_reason 说明新目标及旧工作处理方式。")


def _task_archive_snapshot(data: dict[str, Any], reason: str | None = None, *, archived_at: str | None = None) -> dict[str, Any]:
    # Exclude task history before building the snapshot, keeping archives flat.
    current = {key: deepcopy(value) for key, value in data.items()
               if key not in ("task_history", "session_work_state", "session_work")}
    archived_at = archived_at if _valid_boundary(archived_at) else _now()
    _close_snapshot_times(current, archived_at, "archive_fallback")
    snapshot = _progress_payload(current)
    for key in ("task_context", "context_revisions"):
        if key in current:
            snapshot[key] = deepcopy(current[key])
    snapshot.pop("task_history", None)
    snapshot["archived_at"] = archived_at
    if reason:
        snapshot["archive_reason"] = reason.strip()
    return deepcopy(snapshot)


def _parallel_is_archivable(data: dict[str, Any]) -> bool:
    if not data.get("parallel_request_id") or data.get("schema_version") != 2 or not _task_closed(data):
        return False
    # A terminal/open/failed/cancelled request is still unresolved. Even old
    # completed instances must have resolved every node before leaving current.
    return bool(_handoff_closure(data)) or _accepted_closure(data) or bool(data.get("nodes")) and all(
        node.get("execution_status") == "finished" and node.get("outcome") == "resolved"
        and node.get("review_status") != "pending" for node in data["nodes"].values())


def _parallel_archive_key(data: dict[str, Any]) -> str:
    closure = _handoff_closure(data)
    if closure:
        return json.dumps([_task_instance_id(data), data.get("epoch", 0), "handoff", closure["handoff_id"]], ensure_ascii=False)
    review = _closure_review(data)
    if review and review["decision"] == "accept":
        return json.dumps([_task_instance_id(data), data.get("epoch", 0), "closure_review", review["review_id"]], ensure_ascii=False)
    root = data["nodes"][data["root_id"]]
    return json.dumps([_task_instance_id(data), data.get("epoch", 0),
        root.get("attempt_id") or root.get("finished_at") or data.get("revision", 0)], ensure_ascii=False)


def _parallel_archive_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    root = data["nodes"][data["root_id"]]
    closure = _handoff_closure(data)
    review = _closure_review(data)
    archived_at = (closure.get("closed_at") if closure else None) or (review.get("reviewed_at") if review and review["decision"] == "accept" else None) or root.get("finished_at") or data.get("updated_at") or data.get("created_at") or _now()
    snapshot = _task_archive_snapshot(data, "并行任务已完成综合验收。", archived_at=archived_at)
    snapshot.update(archive_kind="parallel", request_id=data["parallel_request_id"],
        archive_key=_parallel_archive_key(data))
    if closure:
        snapshot.update(archive_reason="移交目标已验收；保留来源未解决/取消结果。",
                        handoff_closure=deepcopy(closure))
    if review and review["decision"] == "accept":
        snapshot.update(archive_reason="父级已明确验收收尾；原始结果保持不变。",
                        closure_review=deepcopy(review))
    return snapshot


def _parallel_task_history(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Project old completed instances on read; persist only on an actual write."""
    history = list(document.get("task_history", []))
    keys = {item.get("archive_key") for item in history if item.get("archive_key")}
    state = document.get("session_work_state", {})
    for request in state.get("requests", []):
        instance = state.get("instances", {}).get(request.get("task_instance_id"))
        if request.get("mode") != "parallel" or not instance or not _parallel_is_archivable(instance):
            continue
        key = _parallel_archive_key(instance)
        if key not in keys:
            snapshot = _parallel_archive_snapshot(instance)
            history.append(snapshot)
            keys.add(key)
    return history


def _archive_current_task(data: dict[str, Any] | None, reason: str | None = None) -> list[dict[str, Any]]:
    if data is None:
        return []
    return [*_parallel_task_history(data), _task_archive_snapshot(data, reason)]


def _progress_payload(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") == 2 and isinstance(data.get("nodes"), dict):
        return _network_progress_payload(data)
    total, completed, percent, status = _stats(data)
    steps = [
        {
            "index": index,
            "description": step["description"],
            "status": step["status"],
            "started_at": step.get("started_at"),
            "finished_at": step.get("finished_at"),
            **({"time_boundary_sources": deepcopy(step["time_boundary_sources"])} if step.get("time_boundary_sources") else {}),
            "attempt": step.get("attempt", 0),
            "attempt_id": step.get("attempt_id"),
            "attempts": deepcopy(step.get("attempts", [])),
            "legacy_attempt": step.get("legacy_attempt", False),
            **({"assignment": step["assignment"]} if step.get("assignment") else {}),
            **({"note": step["note"]} if step.get("note") else {}),
        }
        for index, step in enumerate(data["steps"])
    ]
    split_review_due = []
    for step in steps:
        if step["status"] != "in_progress" or not _needs_split_review(step.get("note")):
            continue
        elapsed = _elapsed_seconds(step.get("started_at"))
        if elapsed is not None and elapsed > LONG_RUNNING_REVIEW_SECONDS:
            split_review_due.append({
                "id": str(step["index"]),
                "title": step["description"],
                "started_at": step.get("started_at"),
                "elapsed_seconds": elapsed,
                "elapsed_minutes": elapsed // 60,
                "execution_mode": step.get("assignment", {}).get("execution_mode"),
            })
    return {
        "schema_version": 1,
        "state_revision": data.get("state_revision", 0),
        "session_id": data["session_id"],
        "task_instance_id": _task_instance_id(data),
        "task_history": data.get("task_history", []),
        "epoch": 0,
        "task_title": data.get("task_title") or "未命名任务",
        "status": status,
        "completed": completed,
        "total": total,
        "percent": percent,
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "steps": steps,
        "split_review_due": split_review_due,
    }


def _new_node(node_id: str, title: str, parent_id: str | None) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": title,
        "parent_id": parent_id,
        "children": [],
        "depends_on": [],
        "dependency_policy": "resolved",
        "execution_status": "pending",
        "outcome": None,
        "summary": "",
        "evidence": [],
        "assignment": {},
        "planned_assignment": {},
        "responsibility_version": 1,
        "reassignment_history": [],
        "dispatch": None,
        "submission": None,
        "review": None,
        "review_status": None,
        "profile": None,
        "attempt": 0,
        "attempt_id": None,
        "attempts": [],
        "started_at": None,
        "finished_at": None,
    }


def _normalize_main_session(value: Any) -> dict[str, Any] | None:
    """Record caller-reported session settings without implying host verification."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProgressError("main_session 必须是对象。")
    model = value.get("model")
    effort = value.get("reasoning_effort")
    if model is not None and not isinstance(model, str):
        raise ProgressError("main_session.model 必须是字符串或省略。")
    if effort == "":
        effort = None
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ProgressError(f"main_session.reasoning_effort 必须是 {REASONING_EFFORTS} 之一。")
    model = model.strip() if isinstance(model, str) and model.strip() else None
    effort = effort or None
    context = {}
    for key in ("codex_thread_id", "codex_turn_id"):
        identifier = value.get(key)
        if identifier is not None:
            if not isinstance(identifier, str):
                raise ProgressError(f"main_session.{key} 必须是字符串。")
            if identifier.strip():
                context[key] = identifier.strip()
    return {
        "model": model,
        "reasoning_effort": effort,
        "metadata_provenance": _metadata_provenance(
            {"model": model, "reasoning_effort": effort, **context}, scope="task_initialization"
        ),
        **context,
    }


def _new_network(
    session_id: str,
    task_title: str,
    *,
    epoch: int = 0,
    main_session: dict[str, str] | None = None,
    reserved_ids: set[str] | None = None,
) -> dict[str, Any]:
    now = _now()
    root_id = "root" if epoch == 0 else f"root-e{epoch}"
    base_id = root_id
    suffix = 0
    while root_id in (reserved_ids or set()):
        suffix += 1
        root_id = f"{base_id}-{suffix}"
    network = {
        "schema_version": 2,
        "session_id": session_id,
        "task_instance_id": str(uuid.uuid4()),
        "task_history": [],
        "task_title": task_title,
        "planning_status": "required",
        "planning_note": "",
        "main_session": main_session,
        "epoch": epoch,
        "revision": 0,
        "root_id": root_id,
        "nodes": {root_id: _new_node(root_id, task_title, None)},
        "history": [],
        "created_at": now,
        "updated_at": now,
    }
    network["nodes"][root_id]["planned_assignment"] = {"execution_mode": "main_session"}
    return network


def _validate_node_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ProgressError("task_id 必须是 1–64 位字母、数字、连字符或下划线。")
    return value


def _metadata_provenance(value: dict[str, Any], *, scope: str) -> dict[str, Any]:
    return {
        "source": "mcp_tool_input" if value.get("model") or value.get("reasoning_effort") else "unknown",
        "verification": "not_verified_by_codex_host",
        "model_source": "mcp_tool_input" if value.get("model") else "unknown",
        "reasoning_effort_source": "mcp_tool_input" if value.get("reasoning_effort") else "unknown",
        "captured_at": _now(),
        "scope": scope,
    }


def _normalize_assignment(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProgressError("assignment 必须是对象。")
    assignment: dict[str, Any] = {}
    string_fields = ("tool_identifier", "tool_name", "dispatch_text", "agent_name", "model", "batch", "codex_thread_id", "codex_turn_id", "self_execution_reason")
    for key in string_fields:
        field_value = value.get(key)
        if field_value is not None:
            if not isinstance(field_value, str):
                raise ProgressError(f"assignment.{key} 必须是字符串。")
            assignment[key] = field_value
    execution_mode = value.get("execution_mode")
    if execution_mode is not None:
        if execution_mode not in EXECUTION_MODES:
            raise ProgressError(f"execution_mode 必须是 {EXECUTION_MODES} 之一。")
        assignment["execution_mode"] = execution_mode
    complexity = value.get("complexity")
    if complexity is not None:
        if complexity not in ("simple", "complex"):
            raise ProgressError("assignment.complexity 必须是 simple 或 complex。")
        assignment["complexity"] = complexity
    profile = value.get("profile")
    if profile is not None:
        if profile not in PROFILES:
            raise ProgressError(f"profile 必须是 {PROFILES} 之一。")
        assignment["profile"] = profile
    effort = value.get("reasoning_effort")
    if effort is not None:
        if effort not in REASONING_EFFORTS:
            raise ProgressError(f"reasoning_effort 必须是 {REASONING_EFFORTS} 之一。")
        assignment["reasoning_effort"] = effort
    fork_turns = value.get("fork_turns")
    has_dispatch = execution_mode == "subagent" or any(assignment.get(key) for key in ("tool_identifier", "dispatch_text", "agent_name"))
    if fork_turns is not None and fork_turns != "none":
        raise ProgressError('子智能体配置必须使用 fork_turns="none"。')
    if fork_turns == "none" or has_dispatch:
        assignment["fork_turns"] = "none"
    return assignment


def _require_execution_metadata(assignment: dict[str, Any] | None, *, dispatch_required: bool = False) -> None:
    if assignment is None or not assignment.get("execution_mode"):
        raise ProgressError("启动任务必须提供 assignment.execution_mode。")
    if assignment.get("execution_mode") == "main_session":
        # A missing host-readable setting is better represented as unknown than
        # guessed from config or model self-report; it must not block the work.
        return
    missing = [key for key in ("model", "reasoning_effort") if not assignment.get(key)]
    if missing:
        raise ProgressError(f"启动子智能体任务时 assignment 必须包含派发配置：{', '.join(missing)}。")
    if dispatch_required:
        missing = [key for key in ("tool_identifier", "dispatch_text")
                   if not isinstance(assignment.get(key), str) or not assignment[key].strip()]
        if missing:
            raise ProgressError("启动子智能体任务必须记录派发工具和自包含文本：" + ", ".join(missing) + "。")


def _require_self_execution_reason(assignment: dict[str, Any], *, atomic_reason: str = "") -> None:
    if assignment.get("execution_mode") != "main_session":
        return
    reason = assignment.get("self_execution_reason", "").strip() or atomic_reason.strip()
    if not reason:
        raise ProgressError("主会话执行叶任务必须记录 assignment.self_execution_reason；复杂工作默认委派子智能体。")
    if assignment.get("complexity") == "complex" and reason.rstrip("。.!！ ") in {
        "有依赖", "需要集成", "需要上下文", "集成", "上下文", "复杂任务", "main_session",
    }:
        raise ProgressError("复杂任务由主会话执行须提供具体例外原因，不能仅填写有依赖、需要集成或需要上下文。")


def _reviewer(value: Any, *, scope: str) -> dict[str, Any]:
    reviewer = _normalize_assignment(value)
    if reviewer.get("execution_mode") != "main_session":
        raise ProgressError("此动作必须由 reviewer.execution_mode=main_session 声明主会话责任；该声明未经宿主核验。")
    return _stamp_assignment(reviewer, scope=scope)


def _require_result_evidence(summary: Any, evidence: Any) -> None:
    if not isinstance(summary, str) or not summary.strip():
        raise ProgressError("验收必须记录非空 summary。")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item.strip() for item in evidence):
        raise ProgressError("验收必须记录至少一条非空 evidence。")


def _assert_no_pending_reviews(data: dict[str, Any] | None, action: str) -> None:
    if data and any(node.get("review_status") == "pending" for node in data.get("nodes", {}).values()):
        raise ProgressError(f"存在待验收 submission，不能{action}；请先由主会话 review_task 接受或退回，原始上报会保留。")


def _stamp_assignment(assignment: dict[str, Any], *, scope: str) -> dict[str, Any]:
    stamped = dict(assignment)
    # Do not accept caller-supplied provenance: current MCP arguments cannot
    # authenticate Codex host metadata.
    stamped["metadata_provenance"] = _metadata_provenance(stamped, scope=scope)
    return stamped


def _historical_ids(data: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for epoch in data.get("history", []):
        nodes = epoch.get("nodes", {}) if isinstance(epoch, dict) else {}
        if isinstance(nodes, dict):
            found.update(nodes)
    return found


def _validate_network(data: dict[str, Any], *, check_execution_cycles: bool = True) -> None:
    nodes = data.get("nodes")
    root_id = data.get("root_id")
    if not isinstance(nodes, dict) or not nodes or not isinstance(root_id, str) or root_id not in nodes:
        raise ProgressError("任务网络必须包含有效的根节点。")
    if not all(isinstance(node_id, str) and isinstance(node, dict) for node_id, node in nodes.items()):
        raise ProgressError("任务节点索引必须映射到对象节点。")
    for node_id in nodes:
        _validate_node_id(node_id)
    if nodes[root_id].get("parent_id") is not None:
        raise ProgressError("根任务不能有父节点。")
    children = {node_id: [] for node_id in nodes}
    # Starting a group releases its children; completing it waits for them.
    # One vertex per task cannot represent both constraints without either
    # accepting ancestor deadlocks or rejecting valid sibling dependencies.
    edges = {(node_id, phase): set() for node_id in nodes for phase in ("start", "finish")}
    indegree = {event: 0 for event in edges}
    for node_id, node in nodes.items():
        if node.get("id") != node_id:
            raise ProgressError("任务节点 ID 与网络索引不一致。")
        edges[(node_id, "start")].add((node_id, "finish"))
        parent_id = node.get("parent_id")
        if node_id != root_id:
            if not isinstance(parent_id, str) or parent_id not in nodes:
                raise ProgressError(f"任务 {node_id} 的父节点不存在。")
            children[parent_id].append(node_id)
            edges[(parent_id, "start")].add((node_id, "start"))
            edges[(node_id, "finish")].add((parent_id, "finish"))
        dependencies = node.get("depends_on", [])
        if (
            not isinstance(dependencies, list)
            or not all(isinstance(dependency_id, str) for dependency_id in dependencies)
            or len(dependencies) != len(set(dependencies))
        ):
            raise ProgressError(f"任务 {node_id} 的 depends_on 必须是无重复 ID 列表。")
        for dependency_id in dependencies:
            if dependency_id not in nodes:
                raise ProgressError(f"任务 {node_id} 依赖的 {dependency_id} 不存在。")
            if check_execution_cycles:
                edges[(dependency_id, "finish")].add((node_id, "start"))
        if node.get("dependency_policy", "resolved") not in DEPENDENCY_POLICIES:
            raise ProgressError(f"任务 {node_id} 的 dependency_policy 无效。")
        if node.get("execution_status") not in EXECUTION_STATUSES:
            raise ProgressError(f"任务 {node_id} 的 execution_status 无效。")
        outcome = node.get("outcome")
        if outcome is not None and outcome not in OUTCOMES:
            raise ProgressError(f"任务 {node_id} 的 outcome 无效。")
        if outcome is not None and node.get("execution_status") != "finished":
            raise ProgressError(f"任务 {node_id} 只有 finished 状态才能设置 outcome。")
    for source, targets in edges.items():
        for target in targets:
            indegree[target] += 1
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        source = ready.pop()
        visited += 1
        for target in edges[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(edges):
        raise ProgressError("父子层级与 depends_on 合并后出现循环依赖。")
    for node_id, node in nodes.items():
        node["children"] = children[node_id]


def _add_tasks(data: dict[str, Any], parent_id: str, tasks: Any) -> None:
    if not isinstance(tasks, list) or not tasks:
        raise ProgressError("tasks 必须是非空任务对象列表。")
    _validate_node_id(parent_id)
    nodes = data["nodes"]
    parent = nodes.get(parent_id)
    if parent is None:
        raise ProgressError(f"父任务 {parent_id} 不存在。")
    if parent.get("execution_status") in TERMINAL_EXECUTION_STATUSES:
        raise ProgressError("已结束的任务不能继续分解。")
    is_addition = bool(parent.get("children"))
    ids = set(nodes) | _historical_ids(data)
    new_nodes: list[tuple[str, dict[str, Any]]] = []
    for item in tasks:
        if not isinstance(item, dict):
            raise ProgressError("tasks 中的每个任务都必须是对象。")
        node_id = _validate_node_id(item.get("id"))
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ProgressError(f"任务 {node_id} 必须有非空 title。")
        if node_id in ids:
            raise ProgressError(f"task_id {node_id} 已存在；节点 ID 在当前任务的所有计划轮次中必须唯一。")
        ids.add(node_id)
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
            raise ProgressError(f"任务 {node_id} 的 depends_on 必须是字符串列表。")
        policy = item.get("dependency_policy", "resolved")
        if policy not in DEPENDENCY_POLICIES:
            raise ProgressError(f"任务 {node_id} 的 dependency_policy 必须是 {DEPENDENCY_POLICIES} 之一。")
        profile = item.get("profile")
        if profile is not None and profile not in PROFILES:
            raise ProgressError(f"任务 {node_id} 的 profile 必须是 {PROFILES} 之一。")
        node = _new_node(node_id, title.strip(), parent_id)
        reason = item.get("addition_reason")
        precedes = item.get("precedes", [])
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ProgressError(f"任务 {node_id} 的 addition_reason 必须非空。")
        if not isinstance(precedes, list) or not all(isinstance(value, str) for value in precedes) or len(precedes) != len(set(precedes)):
            raise ProgressError(f"任务 {node_id} 的 precedes 必须是无重复 ID 列表。")
        if precedes and not reason:
            raise ProgressError("插入既有任务的前置条件时必须提供 addition_reason。")
        if is_addition or reason is not None or precedes:
            node["addition_reason"] = reason.strip() if reason else "未记录（旧调用）"
            node["is_added"] = True
        node["depends_on"] = list(dependencies)
        node["dependency_policy"] = policy
        node["profile"] = profile
        assignment = _normalize_assignment(item.get("assignment"))
        if not assignment.get("execution_mode"):
            raise ProgressError(f"任务 {node_id} 必须指定 assignment.execution_mode。")
        # A plan does not claim that an execution attempt has already started.
        node["assignment"] = {}
        node["planned_assignment"] = deepcopy(assignment)
        if assignment.get("complexity") == "complex":
            _require_self_execution_reason(assignment)
        new_nodes.append((node_id, node))
    for node_id, node in new_nodes:
        nodes[node_id] = node
    for item, (node_id, node) in zip(tasks, new_nodes):
        for successor_id in item.get("precedes", []):
            successor = nodes.get(successor_id)
            if successor is None or successor_id in {key for key, _ in new_nodes}:
                raise ProgressError(f"后继 {successor_id} 必须是已有任务。")
            # Changing an already started branch would falsify its execution history.
            stack = [successor_id]
            while stack:
                current = nodes[stack.pop()]
                if current.get("execution_status") != "pending" or current.get("attempt") or current.get("review_status") == "pending":
                    raise ProgressError(f"后继 {successor_id} 或其子任务已开始；请先协调并重规划，不能修改既有执行历史。")
                stack.extend(current.get("children", []))
            successor.setdefault("depends_on", []).append(node_id)
        # Persist a useful stable order for same-parent consumers as well as
        # the dependency edge used by scheduling and the UI's topological sort.
        sibling_targets = [v for v in item.get("precedes", []) if nodes[v].get("parent_id") == parent_id]
        if sibling_targets:
            order = list(nodes)
            order.remove(node_id)
            order.insert(min(order.index(v) for v in sibling_targets), node_id)
            data["nodes"] = nodes = {key: nodes[key] for key in order}
    _validate_network(data)


def _node_is_terminal(node: dict[str, Any]) -> bool:
    if node.get("review_status") == "pending":
        return False
    status = node.get("execution_status")
    return status in TERMINAL_EXECUTION_STATUSES and (
        status != "finished" or node.get("outcome") in OUTCOMES
    )


def _dependency_satisfied(data: dict[str, Any], node: dict[str, Any], dependency_id: str) -> bool:
    dependency = data["nodes"][dependency_id]
    if dependency.get("review_status") == "pending":
        return False
    policy = node.get("dependency_policy", "resolved")
    if policy == "finished":
        return _node_is_terminal(dependency)
    return dependency.get("execution_status") == "finished" and dependency.get("outcome") == "resolved"


def _dependency_constraints(data: dict[str, Any], node: dict[str, Any]):
    """Yield dependencies with the policy of the node that declared them."""
    owner = node
    while owner is not None:
        for dependency_id in owner.get("depends_on", []):
            yield owner, dependency_id
        parent_id = owner.get("parent_id")
        owner = data["nodes"].get(parent_id) if parent_id is not None else None


def _dependencies_satisfied(data: dict[str, Any], node: dict[str, Any]) -> bool:
    return all(_dependency_satisfied(data, owner, dep_id)
               for owner, dep_id in _dependency_constraints(data, node))


def _node_is_ready(data: dict[str, Any], node: dict[str, Any]) -> bool:
    root_id = data.get("root_id")
    planning_status = data.get("planning_status")
    # Older persisted graphs predate planning_status. Preserve in-flight and
    # completed root attempts, but treat an untouched empty root as unplanned.
    if planning_status is None:
        if data.get("nodes", {}).get(root_id, {}).get("children"):
            planning_status = "planned"
        elif node.get("execution_status") in ("running", *TERMINAL_EXECUTION_STATUSES):
            planning_status = "atomic"
        else:
            planning_status = "required"
    if node.get("id") == root_id and planning_status == "required":
        return False
    return (
        not node.get("children")
        and node.get("execution_status") == "pending"
        and _dependencies_satisfied(data, node)
    )


def _display_node_status(data: dict[str, Any], node: dict[str, Any]) -> str:
    if node.get("id") == data.get("root_id") and _handoff_closure(data):
        return "handed_off"
    if _accepted_closure(data, node.get("id")):
        return "closed"
    if node.get("review_status") == "pending":
        return "awaiting_review"
    if node.get("outcome") != "resolved" and _children_settled(data, node.get("id")):
        review = _closure_review(data, node.get("id"))
        return "open" if review and review["decision"] == "continue" else "waiting_synthesis"
    if node.get("execution_status") == "finished" and node.get("outcome"):
        return node["outcome"]
    status = node.get("execution_status")
    if status == "running":
        return "executing"
    if status in ("failed", "cancelled"):
        return status
    if node.get("children"):
        child_statuses = [_display_node_status(data, data["nodes"][child_id]) for child_id in node["children"]]
        if "awaiting_review" in child_statuses:
            return "awaiting_review"
        if any(value == "executing" for value in child_statuses):
            return "executing"
        if all(value in ("resolved", "open", "failed", "cancelled") for value in child_statuses):
            return "waiting_synthesis"
        if any(value in ("ready", "decomposing") for value in child_statuses):
            return "decomposing"
        return "blocked"
    return "ready" if _node_is_ready(data, node) else "blocked"


def _network_progress_payload(data: dict[str, Any]) -> dict[str, Any]:
    # Old versions may have persisted a dependency deadlock. Keep it readable
    # and available for re-planning; only new graph mutations must pass the gate.
    _validate_network(data, check_execution_cycles=False)
    nodes = data["nodes"]
    leaves = [node for node in nodes.values() if not node.get("children")]
    ready_nodes = [node for node in leaves if _node_is_ready(data, node)]
    terminal_leaves = [node for node in leaves if _node_is_terminal(node) or node.get("review_status") == "pending"]
    pending_reviews = [node for node in leaves if node.get("review_status") == "pending"]
    resolved_leaves = [node for node in leaves if node.get("outcome") == "resolved"]
    open_leaves = [node for node in leaves if node.get("outcome") == "open"]
    failed_leaves = [node for node in leaves if node.get("execution_status") == "failed"]
    running_leaves = [node for node in leaves if node.get("execution_status") == "running"]
    split_review_due = []
    for node in running_leaves:
        if not _needs_split_review(node.get("summary")):
            continue
        elapsed = _elapsed_seconds(node.get("started_at"))
        if elapsed is not None and elapsed > LONG_RUNNING_REVIEW_SECONDS:
            split_review_due.append({
                "id": node["id"],
                "title": node["title"],
                "started_at": node.get("started_at"),
                "elapsed_seconds": elapsed,
                "elapsed_minutes": elapsed // 60,
                "execution_mode": node.get("assignment", {}).get("execution_mode"),
            })
    blocked_leaves = [node for node in leaves if node.get("execution_status") == "pending" and not _node_is_ready(data, node)]
    total = len(leaves)
    completed = len(terminal_leaves)
    percent = int(completed / total * 100) if total else 0
    root = nodes[data["root_id"]]
    root_status = _display_node_status(data, root)
    if root_status in ("closed", "handed_off", "waiting_synthesis", "open"):
        overall_status = root_status
    elif root.get("outcome") == "resolved":
        overall_status = "completed"
    elif root.get("outcome") == "open":
        overall_status = "open"
    elif root.get("execution_status") == "failed":
        overall_status = "failed"
    elif root.get("execution_status") == "cancelled":
        overall_status = "cancelled"
    elif pending_reviews:
        overall_status = "awaiting_review"
    elif running_leaves or ready_nodes:
        overall_status = "in_progress"
    elif total and completed == total:
        overall_status = "waiting_synthesis"
    else:
        overall_status = "pending"
    enriched_nodes = []
    for node_id, node in nodes.items():
        constraints = list(_dependency_constraints(data, node))
        unmet = list(dict.fromkeys(dep_id for owner, dep_id in constraints
                                  if not _dependency_satisfied(data, owner, dep_id)))
        dependencies = []
        for owner, dep_id in constraints:
            dependency = nodes[dep_id]
            dependencies.append({
                "id": dep_id,
                "title": dependency["title"],
                "execution_status": dependency["execution_status"],
                "outcome": dependency.get("outcome"),
                "summary": dependency.get("summary", ""),
                "evidence": dependency.get("evidence", []),
                "dependency_policy": owner.get("dependency_policy", "resolved"),
                "inherited_from": owner["id"] if owner["id"] != node_id else None,
            })
        enriched_nodes.append({
            **node,
            "planned_assignment": node.get("planned_assignment"),
            "responsibility_legacy": node.get("responsibility_version") != 1,
            "review_status": node.get("review_status") or ("legacy_unrecorded" if node.get("responsibility_version") != 1 else None),
            "closure_review_binding": {"source_epoch": data.get("epoch", 0), "source_fingerprint": _result_fingerprint(data, node_id)} if node.get("children") else None,
            "dispatch": node.get("dispatch") or ({
                "status": "pending", "attempt_id": node.get("attempt_id"), "agent_name": None,
                "bound_at": None, "legacy": True, "source": "legacy_unrecorded",
                "verification": "not_verified_by_codex_host",
            } if node.get("responsibility_version") != 1 and node.get("execution_status") == "running"
                  and node.get("assignment", {}).get("execution_mode") == "subagent" else None),
            "status": _display_node_status(data, node),
            "ready": _node_is_ready(data, node),
            "blocked_by": unmet,
            "dependencies": dependencies,
        })
    return {
        "schema_version": 2,
        "state_revision": data.get("state_revision", 0),
        "session_id": data["session_id"],
        "task_instance_id": _task_instance_id(data),
        "task_history": data.get("task_history", []),
        "task_title": data["task_title"],
        "main_session": data.get("main_session"),
        "planning_status": data.get("planning_status") or _legacy_planning_status(data),
        "planning_note": data.get("planning_note", ""),
        "status": overall_status,
        "root_status": root_status,
        "completed": completed,
        "total": total,
        "percent": percent,
        "resolved_leaf_count": len(resolved_leaves),
        "open_leaf_count": len(open_leaves),
        "failed_leaf_count": len(failed_leaves),
        "ready_count": len(ready_nodes),
        "executing_count": len(running_leaves),
        "pending_review_count": len(pending_reviews),
        "split_review_due": split_review_due,
        "blocked_count": len(blocked_leaves),
        "epoch": data.get("epoch", 0),
        "revision": data.get("revision", 0),
        "root_id": data["root_id"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "nodes": enriched_nodes,
        "ready_tasks": [node["id"] for node in ready_nodes],
        "next_actions": _planning_next_actions(data),
        "history": data.get("history", []),
        "handoffs": deepcopy(data.get("handoffs", [])),
        "handoff_closures": deepcopy(data.get("handoff_closures", [])),
        "closure_reviews": deepcopy(data.get("closure_reviews", [])),
        "handoff_closure": deepcopy(_handoff_closure(data)),
        "closure_review": deepcopy(_closure_review(data)),
        "closure_review_due": _closure_review_due(data),
        "closure_review_binding": {"source_epoch": data.get("epoch", 0), "source_fingerprint": _result_fingerprint(data)},
        "handoff_summary": {"registered": len(data.get("handoffs", [])), "closed": len(data.get("handoff_closures", [])),
                            "closure_reviews": len(data.get("closure_reviews", []))},
    }


def _legacy_planning_status(data: dict[str, Any]) -> str:
    root = data.get("nodes", {}).get(data.get("root_id"), {})
    if root.get("children"):
        return "planned"
    if root.get("execution_status") in ("running", *TERMINAL_EXECUTION_STATUSES):
        return "atomic"
    return "required"


def _planning_next_actions(data: dict[str, Any]) -> list[str]:
    status = data.get("planning_status") or _legacy_planning_status(data)
    if status == "required":
        return ["decompose_task(root) 或通过 init_task_network 提交初始 tasks；若确为单步任务，重新初始化时填写 atomic_reason"]
    nodes = data["nodes"]
    root = nodes[data["root_id"]]
    review = _closure_review(data)
    if _handoff_closure(data) or _accepted_closure(data):
        return ["已验收收尾；原始未解决/取消结果保留。需要继续时显式retry，不改写旧结果。"]
    if _closure_review_due(data):
        return (["收尾验收决定继续：按验收摘要显式retry或重规划；原始结果保持不变。"]
                if review and review["decision"] == "continue" else
                ["全部子项已结束；主会话review_task_closure决定continue（重试/重规划）或accept（验收收尾），取消不自动算成功。"])
    if _node_is_terminal(root):
        return ["任务已完成：报告验收结果。" if root.get("outcome") == "resolved" else "报告未解决或已终止结果；需要继续时显式重试或重规划。"]
    actions = []
    for node in nodes.values():
        node_id = node["id"]
        if node.get("review_status") == "pending":
            actions.append(f"主会话 review_task({node_id})：使用当前 task_instance_id 与 attempt_id={node.get('attempt_id')}，检查 submission 后填写 decision=accept|reject、summary、evidence 与 reviewer.execution_mode=main_session；不要重复 update_task(finished)。")
        elif node.get("execution_status") == "running":
            if node.get("assignment", {}).get("execution_mode") == "subagent" and not (node.get("dispatch") or {}).get("agent_name"):
                actions.append(f"bind_task_agent({node_id})：绑定实际派发返回的 agent_name；旧执行可补录后提交复核。")
            else:
                actions.append(f"等待任务 {node_id} 的当前执行结果；继续用当前 attempt_id 更新。")
        elif (node.get("children") and node.get("execution_status") == "pending"
              and all(_node_is_terminal(nodes[child]) for child in node["children"])
              and (_dependencies_satisfied(data, node) or all(_node_is_terminal(nodes[dependency_id])
                   for _, dependency_id in _dependency_constraints(data, node)))):
            outcome_hint = ", outcome=resolved" if all(nodes[child].get("execution_status") == "finished"
                and nodes[child].get("outcome") == "resolved" for child in node["children"]) else "，有未解决子结果只能 open/终止"
            actions.append(f"主会话综合验收 {node_id}：update_task(finished{outcome_hint})，填写 summary/evidence。")
        elif _node_is_ready(data, node):
            planned = node.get("planned_assignment") or {}
            role = planned.get("execution_mode")
            if role is None:
                actions.append(f"主会话 reassign_task({node_id})：旧节点缺少独立计划，请确认执行者和恢复原因。")
            elif role == "subagent":
                actions.append(f"主会话派发 {node_id}：update_task(running) 后实际 spawn，再 bind_task_agent。")
            else:
                actions.append(f"主会话执行 {node_id}：update_task(running)，记录具体 self_execution_reason。")
    return actions or ["检查阻塞依赖和未解决结果；显式重试或重规划，不能启动空的 ready 列表。"]


def init_task_network(
    task_title: str,
    session_id: str | None = None,
    main_session: dict[str, Any] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    atomic_reason: str | None = None,
    request_id: str | None = None,
    new_task_reason: str | None = None,
) -> dict[str, Any]:
    requested_sid = _normalize_session_id(session_id) if session_id is not None else None
    if not isinstance(task_title, str) or not task_title.strip():
        raise ProgressError("task_title 必须是非空字符串。")
    if tasks is not None and atomic_reason is not None:
        raise ProgressError("tasks 与 atomic_reason 不能同时提供；请选择计划或明确单步任务。")
    if atomic_reason is not None and (not isinstance(atomic_reason, str) or not atomic_reason.strip()):
        raise ProgressError("atomic_reason 必须说明该任务为何是单步工作。")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()):
        raise ProgressError("request_id 必须是非空字符串。")
    normalized_title = task_title.strip()
    normalized_main = _normalize_main_session(main_session)
    fingerprint_main = (
        {key: normalized_main.get(key) for key in ("model", "reasoning_effort", "codex_thread_id", "codex_turn_id")}
        if normalized_main is not None else None
    )
    request_fingerprint = json.dumps(
        {"task_title": normalized_title, "tasks": tasks, "atomic_reason": atomic_reason,
         "main_session": fingerprint_main, **({"new_task_reason": new_task_reason} if new_task_reason is not None else {})}, sort_keys=True, ensure_ascii=False, default=str,
    )
    # One process/file lock protects allocation as well as the ledger. Session
    # locks alone cannot deduplicate a request that has not yet received a sid.
    ledger_lock = str(uuid.uuid5(uuid.NAMESPACE_URL, "task-progress:init-request-ledger"))
    ledger_path = SESSION_DIR / ".init-request-ledger.json"
    fingerprint_hash = hashlib.sha256(request_fingerprint.encode()).hexdigest()

    def request_key(scope: str) -> str:
        return hashlib.sha256(json.dumps([scope, request_id]).encode()).hexdigest()

    with _session_lock(ledger_lock) if request_id else nullcontext():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if request_id and ledger_path.exists() else {}
        key = request_key(requested_sid or "generated-session")
        binding = ledger.get(key) if request_id else None
        sid = binding["session_id"] if binding else requested_sid or str(uuid.uuid4())
        with _session_lock(sid):
            previous = _read(sid)
            if binding and binding["fingerprint_hash"] != fingerprint_hash:
                raise ProgressError("request_id 已用于不同的初始化内容；请为新请求生成新的 request_id。")
            if binding and previous and _task_instance_id(previous) == binding["task_instance_id"]:
                # Return the current revision of the original instance, including
                # replanning, rather than restoring its initialization snapshot.
                data = previous
            elif binding and not (
                binding.get("status") == "reserved"
                and _read_order(sid)["state_revision"] == binding["previous_revision"]
                and (_task_instance_id(previous) if previous else None) == binding["previous_instance_id"]
            ):
                raise ProgressError("该初始化请求绑定的任务已归档、清除或被替换；旧请求已过期，当前任务不会回滚。")
            elif request_id and not binding and previous and previous.get("init_request_id") == request_id:
                # Adopt a current pre-ledger task without rewriting its state.
                if previous.get("init_request_fingerprint") != request_fingerprint:
                    raise ProgressError("request_id 已用于不同的初始化内容；请为新请求生成新的 request_id。")
                data = previous
            else:
                _assert_can_replace_task(previous, new_task_reason)
                data = _new_network(sid, normalized_title, main_session=normalized_main)
                if binding:
                    data["task_instance_id"] = binding["task_instance_id"]
                if tasks is not None:
                    if not isinstance(tasks, list) or len(tasks) < 2:
                        raise ProgressError("多阶段初始计划必须提供至少两个有独立结果边界的 tasks；单步任务请填写 atomic_reason。")
                    _add_tasks(data, data["root_id"], tasks)
                    data["planning_status"] = "planned"
                elif atomic_reason is not None:
                    data["planning_status"] = "atomic"
                    data["planning_note"] = atomic_reason.strip()
                if request_id:
                    data["init_request_id"] = request_id
                    data["init_request_fingerprint"] = request_fingerprint
                    binding = {
                        "session_id": sid, "task_instance_id": data["task_instance_id"],
                        "fingerprint_hash": fingerprint_hash, "status": "reserved",
                        "previous_revision": _read_order(sid)["state_revision"],
                        "previous_instance_id": _task_instance_id(previous) if previous else None,
                    }
                    ledger[key] = binding
                    ledger[request_key(sid)] = binding
                    # Reserve the identity before writing data. A crash cannot
                    # turn a no-session retry into another independent task.
                    _atomic_json(ledger_path, ledger)
                data["task_history"] = _archive_current_task(previous, new_task_reason)
                _inherit_work_state(previous, data)
                _write(sid, data)
            if request_id:
                # Retain only hashes and identity tombstones, never task content.
                binding = {"session_id": sid, "task_instance_id": _task_instance_id(data),
                           "fingerprint_hash": fingerprint_hash, "status": "committed"}
                ledger[key] = binding
                ledger[request_key(sid)] = binding
                _atomic_json(ledger_path, ledger)
            response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid, response_snapshot=response_snapshot)


def decompose_task(
    session_id: str,
    task_id: str,
    tasks: list[dict[str, Any]],
    task_instance_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data = _select_task(sid, task_instance_id)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        _validate_task_instance(data, task_instance_id)
        parent = data["nodes"].get(task_id)
        if parent is not None:
            if parent["execution_status"] == "running" and attempt_id != parent.get("attempt_id"):
                raise ProgressError("分解运行中的任务必须提供当前 attempt_id；请先协调停止旧执行者。")
            if attempt_id is not None and attempt_id != parent.get("attempt_id"):
                raise ProgressError("attempt_id 不匹配，不能用旧尝试分解当前任务。")
        # Validate on an isolated copy: new edges must never partially mutate a
        # stored plan when validation fails.
        data = deepcopy(data)
        parent = data["nodes"].get(task_id)
        _add_tasks(data, task_id, tasks)
        if task_id == data.get("root_id"):
            data["planning_status"] = "planned"
            data["planning_note"] = ""
        if parent["execution_status"] == "running":
            # The caller has coordinated the executor; keep its original attempt
            # before turning the node into a group awaiting child results.
            parent["execution_status"] = "cancelled"
            parent["finished_at"] = _now()
            parent["summary"] = (parent.get("summary", "") + "\n执行尝试因任务分解结束，剩余工作由子任务继续。").strip()
            _record_attempt_result(parent)
            _reset_execution(parent)
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid, response_snapshot=response_snapshot)


def _supersede_stage_inventory(node: dict[str, Any], reason: str) -> None:
    attempt_id = node.pop("stage_inventory_due_attempt", None)
    if attempt_id:
        node.setdefault("stage_inventories", []).append({"attempt_id": attempt_id, "status": "superseded",
                                                       "reason": reason, "recorded_at": _now()})


def _begin_attempt(node: dict[str, Any], assignment: dict[str, Any] | None, *, kind: str = "execution") -> None:
    _require_execution_metadata(assignment, dispatch_required=kind == "execution")
    assignment = _stamp_assignment(assignment or {}, scope="attempt_start")
    node.pop("invalidated_by", None)
    _supersede_stage_inventory(node, "阶段重新执行，旧结果的待盘点提醒已失效。")
    node.pop("time_boundary_sources", None)
    node["attempt"] = int(node.get("attempt", 0)) + 1
    node["attempt_id"] = str(uuid.uuid4())
    node["assignment"] = dict(assignment)
    node["started_at"] = _now()
    node["finished_at"] = None
    node["outcome"] = None
    node["summary"] = ""
    node["evidence"] = []
    node["responsibility_version"] = 1
    node["submission"] = None
    node["review"] = None
    node["review_status"] = None
    node["dispatch"] = None
    if assignment.get("execution_mode") == "subagent":
        # A planned spawn alias is never a dispatch receipt. Only a known
        # executor addressed by an explicit follow-up tool can bind at start.
        tool = assignment.get("tool_identifier", "")
        is_followup = tool in ("collaboration.followup_task", "collaboration.send_message", "mcp__codex_app__send_message_to_thread")
        agent_name = assignment.get("agent_name", "").strip() if is_followup else ""
        if is_followup and not agent_name:
            raise ProgressError("follow-up 派发必须填写真实工具返回的已有 agent_name；新执行者请使用 spawn 后 bind_task_agent。")
        node["dispatch"] = {
            "status": "bound" if agent_name else "pending", "attempt_id": node["attempt_id"],
            "agent_name": agent_name or None, "bound_at": _now() if agent_name else None,
            "tool_identifier": assignment.get("tool_identifier"), "dispatch_text": assignment.get("dispatch_text"),
            "verification": "not_verified_by_codex_host", "source": "mcp_tool_input",
        }
    node.setdefault("attempts", []).append({
        "attempt": node["attempt"], "attempt_id": node["attempt_id"], "kind": kind,
        "execution_status": "running", "outcome": None, "summary": "", "evidence": [],
        "started_at": node["started_at"], "assignment": dict(node["assignment"]),
        "planned_assignment": deepcopy(node.get("planned_assignment")),
        "responsibility_version": 1, "dispatch": deepcopy(node["dispatch"]),
    })


def _record_attempt_result(node: dict[str, Any]) -> None:
    if node.get("attempts") and node.get("attempt_id"):
        node["attempts"][-1].update({key: deepcopy(node.get(key)) for key in (
            "execution_status", "outcome", "summary", "evidence", "finished_at", "dispatch", "submission", "review", "review_status", "failure_reason"
        )})
        if not _valid_boundary(node["attempts"][-1].get("started_at")):
            node["attempts"][-1]["started_at"] = node.get("started_at")
            node["attempts"][-1]["time_boundary_sources"] = deepcopy(node.get("time_boundary_sources", {}))


def _retry_review(node: dict[str, Any]) -> dict[str, Any] | None:
    streak = []
    for attempt in reversed(node.get("attempts", [])[:-1]):
        if (attempt.get("kind", "execution") != "execution"
                or not (attempt.get("execution_status") == "failed"
                or (attempt.get("execution_status") == "finished" and attempt.get("outcome") == "open"))):
            break
        streak.append(attempt)
    if len(streak) < 2:
        return None
    return {"task_id": node["id"], "consecutive_attempts": len(streak),
            "recent_attempts": [{"attempt_id": item.get("attempt_id"),
                                 "failure_reason": (item.get("failure_reason") or "")[:500],
                                 "summary": (item.get("summary") or "")[:500],
                                 "truncated": len(item.get("failure_reason") or "") > 500 or len(item.get("summary") or "") > 500}
                                for item in reversed(streak[:2])]}


def _reset_execution(node: dict[str, Any]) -> None:
    _supersede_stage_inventory(node, "前驱结果变化，阶段结果及待盘点提醒已失效。")
    node.update(execution_status="pending", outcome=None, summary="", evidence=[],
                assignment={}, attempt_id=None, started_at=None, finished_at=None,
                dispatch=None, submission=None, review=None, review_status=None)
    node.pop("time_boundary_sources", None)


def _reopen_ancestors_for_retry(data: dict[str, Any], node: dict[str, Any]) -> None:
    # Compute the full closure before changing anything: an old consumer result
    # can otherwise release its own consumers while its input is being retried.
    nodes = data["nodes"]
    invalidated = {node["id"]}
    while True:
        previous = set(invalidated)
        for invalid_id in previous:
            parent_id = nodes[invalid_id].get("parent_id")
            while parent_id is not None:
                parent = nodes[parent_id]
                if _node_is_terminal(parent):
                    invalidated.add(parent_id)
                parent_id = parent.get("parent_id")
        for consumer in nodes.values():
            if any(dep_id in invalidated for _, dep_id in _dependency_constraints(data, consumer)):
                invalidated.add(consumer["id"])
        if invalidated == previous:
            break
    for invalid_id in invalidated - {node["id"]}:
        if nodes[invalid_id].get("review_status") == "pending":
            raise ProgressError(f"任务 {invalid_id} 尚有待验收 submission；请先 review_task 再重试前驱。")
        if nodes[invalid_id]["execution_status"] == "running":
            raise ProgressError(f"任务 {invalid_id} 仍在使用该结果；请先结束相关执行尝试再重试。")
    for invalid_id in invalidated - {node["id"]}:
        target = nodes[invalid_id]
        if not _node_is_terminal(target):
            continue
        # Retain original attempts and legacy acceptances for inspection/export.
        invalidated_at = _now()
        target.setdefault("invalidated_results", []).append({
            **{key: deepcopy(target.get(key)) for key in (
                "attempt_id", "execution_status", "outcome", "assignment", "summary", "evidence", "started_at", "finished_at",
                "dispatch", "submission", "review", "review_status"
            )},
            "invalidated_by": node["id"], "invalidated_at": invalidated_at,
        })
        if target.get("attempts") and target.get("attempt_id"):
            target["attempts"][-1].update(invalidated_by=node["id"], invalidated_at=invalidated_at)
        _reset_execution(target)
        target["invalidated_by"] = node["id"]


def update_task(
    session_id: str,
    task_id: str,
    execution_status: str,
    outcome: str | None = None,
    summary: str = "",
    evidence: list[str] | None = None,
    attempt_id: str | None = None,
    retry: bool = False,
    assignment: dict[str, Any] | None = None,
    task_instance_id: str | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    node_id = _validate_node_id(task_id)
    if execution_status not in EXECUTION_STATUSES:
        raise ProgressError(f"execution_status 必须是 {EXECUTION_STATUSES} 之一。")
    if outcome is not None and outcome not in OUTCOMES:
        raise ProgressError(f"outcome 必须是 {OUTCOMES} 之一。")
    if execution_status == "finished" and outcome not in OUTCOMES:
        raise ProgressError("finished 状态必须明确给出 outcome=resolved 或 open。")
    if execution_status != "finished" and outcome is not None:
        raise ProgressError("只有 finished 状态才能设置 outcome。")
    if execution_status == "pending":
        raise ProgressError("pending 是任务初始状态；如需重试，请使用 running 并设置 retry=true。")
    if retry and execution_status != "running":
        raise ProgressError("retry=true 只适用于重新进入 running 状态。")
    if not isinstance(summary, str):
        raise ProgressError("summary 必须是字符串。")
    if failure_reason is not None:
        if not isinstance(failure_reason, str) or not failure_reason.strip():
            raise ProgressError("failure_reason 必须是非空字符串。")
        if not (execution_status == "failed" or (execution_status == "finished" and outcome == "open")):
            raise ProgressError("failure_reason 仅适用于 failed 或 finished/open 的尝试。")
        failure_reason = failure_reason.strip()
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ProgressError("evidence 必须是字符串列表。")
    normalized_assignment = _normalize_assignment(assignment) if assignment is not None else None
    with _session_lock(sid):
        # The sole mutation of a completed parallel instance is an explicit
        # retry. Freeze its acceptance before retry invalidates the ancestors.
        document = _read(sid)
        data = _task_from_document(document, task_instance_id, writable=not retry)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        if data.get("request_cancelled"):
            raise ProgressError("请求已取消；迟到更新不能恢复执行。")
        archive_before = _parallel_archive_snapshot(data) if _parallel_is_archivable(data) else None
        _validate_task_instance(data, task_instance_id)
        node = data["nodes"].get(node_id)
        if node is None:
            raise ProgressError(f"当前 epoch 中不存在任务 {node_id}；旧 epoch 的结果不会覆盖新计划。")
        if failure_reason is not None and node.get("children"):
            raise ProgressError("failure_reason 仅适用于叶任务的执行尝试。")
        previous_status = node["execution_status"]
        if (data.get("parallel_request_id") and execution_status == "running" and not node.get("children")
                and (normalized_assignment or node.get("assignment") or {}).get("execution_mode") != "subagent"):
            raise ProgressError("并行独立请求的叶执行必须为 subagent；主会话只负责规划和独立验收。")
        if node.get("review_status") == "pending":
            raise ProgressError("此 attempt 的结果正在等待主会话验收；请使用 review_task，不可覆盖、重试或绕过 submission。")
        if attempt_id is not None and attempt_id != node.get("attempt_id"):
            raise ProgressError("attempt_id 不匹配；迟到或旧尝试结果已拒绝。")
        if previous_status == "running":
            if attempt_id != node.get("attempt_id"):
                raise ProgressError("更新运行中任务必须提供当前 attempt_id。")
            if normalized_assignment is not None:
                changed = [key for key, value in normalized_assignment.items()
                           if node.get("assignment", {}).get(key) != value]
                if changed:
                    raise ProgressError("当前 attempt 的执行快照不可修改；不一致字段：" + ", ".join(changed) + "。")
        if node.get("children") and execution_status == "running":
            raise ProgressError("分组任务由子任务执行；请完成子任务后再提交父任务综合结果。")
        if previous_status in TERMINAL_EXECUTION_STATUSES and execution_status != "running":
            raise ProgressError("已结束任务不能覆盖结果；如需重试，请使用 running 并设置 retry=true。")
        if execution_status == "running":
            if node_id == data.get("root_id") and (data.get("planning_status") or _legacy_planning_status(data)) == "required":
                raise ProgressError("planning_required: 空的根任务不能开工。请先调用 decompose_task 提交粗粒度计划；如果这是单步任务，请通过 init_task_network 的 atomic_reason 明确记录单步原因。")
            if previous_status in TERMINAL_EXECUTION_STATUSES and not retry:
                raise ProgressError("已结束任务重试时必须设置 retry=true，且会创建新的 attempt_id。")
            if previous_status != "running" and not _dependencies_satisfied(data, node):
                raise ProgressError("该任务尚未 ready；请先完成依赖，或显式设置 dependency_policy=finished。")
            if previous_status != "running":
                _require_execution_metadata(normalized_assignment)
                planned = node.get("planned_assignment") or {}
                if not planned.get("execution_mode"):
                    raise ProgressError("旧节点缺少独立 planned_assignment；pending 叶请先 reassign_task 确认计划，已结束节点请显式重规划。")
                if normalized_assignment.get("execution_mode") != planned["execution_mode"]:
                    raise ProgressError("attempt execution_mode 与 planned_assignment 不一致；pending 叶请先由主会话 reassign_task 并记录原因。")
                effective = {**normalized_assignment,
                             "complexity": planned.get("complexity", normalized_assignment.get("complexity"))}
                atomic_reason = data.get("planning_note", "") if (
                    node_id == data.get("root_id") and data.get("planning_status") == "atomic"
                ) else ""
                _require_self_execution_reason(effective, atomic_reason=atomic_reason)
                if previous_status in TERMINAL_EXECUTION_STATUSES:
                    _reopen_ancestors_for_retry(data, node)
                _begin_attempt(node, normalized_assignment)
        if (
            previous_status == "pending"
            and execution_status in ("finished", "failed")
            and not node.get("children")
            and not _node_is_ready(data, node)
        ):
            raise ProgressError("依赖尚未满足的任务不能直接结束；先完成前驱或改用 dependency_policy=finished。")
        if (previous_status == "pending" and execution_status == "finished"
                and not node.get("children")):
            raise ProgressError("叶任务必须先进入 running 并创建执行尝试，才能标记为 finished（resolved 或 open）；子智能体结果随后须经主会话 review_task。")
        if execution_status in TERMINAL_EXECUTION_STATUSES and node.get("children"):
            if not normalized_assignment or normalized_assignment.get("execution_mode") != "main_session":
                raise ProgressError("父/root 的综合验收必须由 assignment.execution_mode=main_session 声明主会话负责。")
            if not summary.strip():
                raise ProgressError("父/root 的综合验收必须记录非空 summary。")
            if not all(_node_is_terminal(data["nodes"][child_id]) for child_id in node["children"]):
                raise ProgressError("父任务必须等所有直接子任务结束后再综合验收。")
            if execution_status == "finished" and outcome == "resolved":
                _require_result_evidence(summary, evidence)
                if not all(data["nodes"][child_id].get("execution_status") == "finished"
                           and data["nodes"][child_id].get("outcome") == "resolved"
                           for child_id in node["children"]):
                    raise ProgressError("父/root 不能 resolved：所有直接子结果必须已验收且 resolved；未解决结果只能 open/终止或重试。")
            if execution_status == "finished" and not _dependencies_satisfied(data, node):
                if outcome != "open" or not all(
                    _node_is_terminal(data["nodes"][dep_id]) for _, dep_id in _dependency_constraints(data, node)
                ):
                    raise ProgressError("父任务的前置依赖尚未满足，不能提交综合验收。")
                if not summary.strip():
                    raise ProgressError("前置未解决时须记录 summary，说明分组无法完成的原因。")
            if execution_status in ("failed", "cancelled") and not summary.strip():
                raise ProgressError("取消或失败收尾须记录 summary，说明原因。")
            if previous_status != "running":
                kind = "synthesis" if execution_status == "finished" else "group_termination"
                _begin_attempt(node, normalized_assignment, kind=kind)
        node["execution_status"] = execution_status
        node["outcome"] = outcome
        if previous_status != "running" and execution_status == "running":
            node.pop("failure_reason", None)
        if failure_reason is not None:
            node["failure_reason"] = failure_reason
        if summary:
            node["summary"] = summary
        if evidence:
            node["evidence"] = evidence
        if execution_status in TERMINAL_EXECUTION_STATUSES:
            node["finished_at"] = _now()
            _ensure_time_bounds(node, node["finished_at"], "termination_fallback")
        if (execution_status == "finished" and not node.get("children")
                and node.get("assignment", {}).get("execution_mode") == "subagent"):
            dispatch = node.get("dispatch") or {}
            if node.get("responsibility_version") == 1 and dispatch.get("status") != "bound":
                raise ProgressError("子智能体完成上报前必须 bind_task_agent 绑定实际返回的 agent_name；派发失败可 failed/cancelled 收尾。")
            if node.get("responsibility_version") != 1 and not node.get("dispatch"):
                if not node.get("attempt_id"):
                    node["attempt_id"] = str(uuid.uuid4())
                    node["attempt"] = int(node.get("attempt", 0)) + 1
                    node.setdefault("attempts", []).append({
                        "attempt": node["attempt"], "attempt_id": node["attempt_id"],
                        "kind": "execution", "assignment": deepcopy(node["assignment"]),
                        "planned_assignment": None, "legacy": True,
                        "started_at": node.get("started_at"),
                    })
                node["dispatch"] = {"status": "pending", "attempt_id": node.get("attempt_id"),
                    "agent_name": None, "bound_at": None, "legacy": True,
                    "verification": "not_verified_by_codex_host", "source": "legacy_unrecorded"}
            node["submission"] = {"attempt_id": node.get("attempt_id"), "outcome": outcome,
                "summary": summary, "evidence": list(evidence), "submitted_at": _now()}
            node["outcome"] = None
            node["review_status"] = "pending"
            node["review"] = None
        _record_attempt_result(node)
        if (node.get("parent_id") == data.get("root_id") and execution_status in TERMINAL_EXECUTION_STATUSES
                and node.get("review_status") != "pending"):
            node["stage_inventory_due_attempt"] = node.get("attempt_id")
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data, archive_before=archive_before)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    result = _tool_result(_format_detail(progress), progress, sid, response_snapshot=response_snapshot)
    if retry and execution_status == "running":
        result["structuredContent"]["retry_review_due"] = _retry_review(node)
    return result


def _current_task(sid: str, task_id: str, task_instance_id: str | None):
    _validate_node_id(task_id)
    data = _select_task(sid, task_instance_id)
    if data is None or data.get("schema_version") != 2:
        raise ProgressError("该会话没有 HTN 任务网络。")
    _validate_task_instance(data, task_instance_id)
    node = data["nodes"].get(task_id)
    if node is None:
        raise ProgressError("当前任务实例/epoch 中不存在此节点。")
    return data, node


def reassign_task(session_id: str, task_id: str, assignment: dict[str, Any], reason: str,
                  reviewer: dict[str, Any], task_instance_id: str | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    if not isinstance(reason, str) or not reason.strip():
        raise ProgressError("显式改派必须记录非空 reason。")
    planned = _normalize_assignment(assignment)
    if not planned.get("execution_mode"):
        raise ProgressError("改派必须指定 assignment.execution_mode。")
    if planned.get("complexity") == "complex":
        _require_self_execution_reason(planned)
    actor = _reviewer(reviewer, scope="task_reassignment")
    with _session_lock(sid):
        data, node = _current_task(sid, task_id, task_instance_id)
        if node.get("children") or node.get("execution_status") != "pending" or node.get("attempt_id"):
            raise ProgressError("reassign_task 仅适用于 pending 叶节点；不能转移正在执行/已结束的 attempt，请显式重规划。")
        if data.get("parallel_request_id") and planned.get("execution_mode") != "subagent":
            raise ProgressError("并行独立请求的叶任务只能改派给 subagent。")
        planned = _stamp_assignment(planned, scope="planned_reassignment")
        node.setdefault("reassignment_history", []).append({
            "previous_assignment": deepcopy(node.get("planned_assignment")),
            "assignment": deepcopy(planned), "reason": reason.strip(), "reviewer": actor, "reassigned_at": _now(),
        })
        node["planned_assignment"] = planned
        node["assignment"] = {}
        node["responsibility_version"] = 1
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result("已显式更新计划分工；这不代表实际执行或身份认证。", progress, sid, response_snapshot=response_snapshot)


def bind_task_agent(session_id: str, task_id: str, attempt_id: str, agent_name: str,
                    task_instance_id: str | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    if not isinstance(agent_name, str) or not agent_name.strip():
        raise ProgressError("agent_name 必须是实际派发返回的非空标识，不能预填未知值。")
    agent_name = agent_name.strip()
    with _session_lock(sid):
        data, node = _current_task(sid, task_id, task_instance_id)
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id != node.get("attempt_id"):
            raise ProgressError("attempt_id 不匹配，不能绑定旧执行或尚无标识的旧 attempt。")
        if node.get("children") or node.get("execution_status") != "running" or node.get("assignment", {}).get("execution_mode") != "subagent":
            raise ProgressError("只能绑定当前 running 的 subagent 叶 attempt。")
        dispatch = node.get("dispatch") or {"status": "pending", "attempt_id": attempt_id,
                                             "legacy": node.get("responsibility_version") != 1}
        if dispatch.get("status") == "bound":
            if dispatch.get("agent_name") != agent_name:
                raise ProgressError("此 attempt 已绑定不同 agent_name；不能二次转移执行归属。")
        else:
            node["dispatch"] = {**dispatch, "status": "bound", "agent_name": agent_name,
                                "bound_at": _now(), "source": "mcp_tool_input", "verification": "not_verified_by_codex_host"}
            _record_attempt_result(node)
            data["revision"] = int(data.get("revision", 0)) + 1
            _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result("已记录派发返回标识；调用方填报，未经 Codex 宿主核验。", progress, sid, response_snapshot=response_snapshot)


def review_task(session_id: str, task_id: str, attempt_id: str, decision: str, summary: str,
                evidence: list[str], reviewer: dict[str, Any], task_instance_id: str | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    if decision not in ("accept", "reject"):
        raise ProgressError("decision 必须是 accept 或 reject。")
    _require_result_evidence(summary, evidence)
    actor = _reviewer(reviewer, scope="submission_review")
    with _session_lock(sid):
        data, node = _current_task(sid, task_id, task_instance_id)
        submission = node.get("submission") or {}
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id != node.get("attempt_id") or attempt_id != submission.get("attempt_id"):
            raise ProgressError("attempt_id 不匹配，不能验收旧执行结果。")
        if node.get("children") or node.get("assignment", {}).get("execution_mode") != "subagent":
            raise ProgressError("review_task 仅接受 subagent 叶结果；父/root 必须使用主会话 synthesis。")
        if node.get("review_status") != "pending" or node.get("execution_status") != "finished":
            raise ProgressError("当前没有 pending submission，不能重复或冲突验收。")
        node["review"] = {"attempt_id": attempt_id, "decision": decision, "summary": summary.strip(),
                          "evidence": list(evidence), "reviewer": actor, "reviewed_at": _now()}
        node["review_status"] = "accepted" if decision == "accept" else "rejected"
        node["outcome"] = submission["outcome"] if decision == "accept" else "open"
        _record_attempt_result(node)
        if node.get("parent_id") == data.get("root_id"):
            node["stage_inventory_due_attempt"] = attempt_id
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result("主会话验收决定已独立记录；原始执行提交保持不变。", progress, sid, response_snapshot=response_snapshot)


def review_stage_inventory(session_id: str, task_id: str, attempt_id: str,
                           decisions: list[dict[str, Any]], expected_state_revision: int,
                           task_instance_id: str | None = None) -> dict[str, Any]:
    """Record a coordinator's disposition of every currently unresolved leaf."""
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data, stage = _current_task(sid, task_id, task_instance_id)
        if not isinstance(expected_state_revision, int) or isinstance(expected_state_revision, bool) or _read_order(sid)["state_revision"] != expected_state_revision:
            raise ProgressError("状态已变化；请重新读取并盘点当前任务树。")
        if (not attempt_id or stage.get("stage_inventory_due_attempt") != attempt_id
                or stage.get("attempt_id") != attempt_id or not _node_is_terminal(stage)):
            raise ProgressError("阶段盘点绑定的 attempt 已过期或无需盘点。")
        if not isinstance(decisions, list):
            raise ProgressError("decisions 必须为列表。")
        unresolved = {key for key, node in data["nodes"].items()
                      if not node.get("children") and not (node.get("execution_status") == "finished"
                      and node.get("outcome") == "resolved" and node.get("review_status") != "pending")}
        normalized = []
        for item in decisions:
            if (not isinstance(item, dict) or not isinstance(item.get("task_id"), str)
                    or item.get("task_id") not in unresolved or item.get("decision") not in ("continue", "obsolete", "covered")):
                raise ProgressError("每项必须引用未解决叶任务并选择 continue、obsolete 或 covered。")
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ProgressError("每项盘点必须记录具体原因和证据或下一步。")
            normalized.append({"task_id": item["task_id"], "decision": item["decision"], "reason": reason.strip()})
        if len(normalized) != len(unresolved) or {x["task_id"] for x in normalized} != unresolved:
            raise ProgressError("盘点必须恰好覆盖当前全部未解决叶任务；请刷新任务树后重试。")
        stage.setdefault("stage_inventories", []).append({"attempt_id": attempt_id, "decisions": normalized, "reviewed_at": _now()})
        stage.pop("stage_inventory_due_attempt", None)
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result("阶段盘点已记录；任务原状态和执行历史未被更改。", progress, sid, response_snapshot=response_snapshot)


def get_ready_tasks(session_id: str, task_instance_id: str | None = None) -> dict[str, Any]:
    result = get_progress_view(session_id, task_instance_id=task_instance_id)
    structured = result.get("structuredContent", {})
    progress = structured.get("progress")
    if not isinstance(progress, dict) or progress.get("schema_version") != 2:
        raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
    ready = [node for node in progress["nodes"] if node.get("ready")]
    if progress.get("planning_status") == "required":
        lines = ["当前没有 ready 任务：规划尚未完成。", *progress.get("next_actions", [])]
    else:
        lines = [f"当前 ready 叶任务：{len(ready)} 个", *progress.get("next_actions", [])]
    for node in ready:
        role = (node.get("planned_assignment") or {}).get("execution_mode")
        action = "待派发子智能体" if role == "subagent" else "主会话自执行" if role == "main_session" else "未记录执行者，先 reassign_task"
        lines.append(f"- [{node['id']}] {node['title']}（计划执行者：{action}；依赖策略：{node['dependency_policy']}）")
        for dependency in node.get("dependencies", []):
            if dependency.get("outcome") == "open" or dependency.get("execution_status") in ("failed", "cancelled"):
                detail = dependency.get("summary") or "无摘要"
                lines.append(f"  前驱 {dependency['id']} 的未解决结果：{detail}")
                lines.extend(f"  证据：{item}" for item in dependency.get("evidence", []))
    result["content"] = [{"type": "text", "text": "\n".join(lines)}]
    structured["ready_tasks"] = ready
    return result


def rebuild_task_network(
    session_id: str,
    reason: str,
    task_title: str = "",
    task_instance_id: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    if not isinstance(reason, str) or not reason.strip():
        raise ProgressError("重建任务网络时必须记录 reason。")
    with _session_lock(sid):
        data = _select_task(sid, task_instance_id)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        _validate_task_instance(data, task_instance_id)
        _assert_no_pending_reviews(data, "重规划")
        if any(node.get("execution_status") == "running" for node in data["nodes"].values()):
            raise ProgressError("仍有任务处于 running，核实或结束这些尝试后再重建网络。")
        closed_at = _now()
        _close_snapshot_times(data, closed_at, "replan_fallback")
        data["history"].append({
            "epoch": data["epoch"],
            "root_id": data["root_id"],
            "task_title": data["task_title"],
            "reason": reason.strip(),
            "closed_at": closed_at,
            "nodes": data["nodes"],
            "handoffs": deepcopy(data.get("handoffs", [])),
            "handoff_closures": deepcopy(data.get("handoff_closures", [])),
            "closure_reviews": deepcopy(data.get("closure_reviews", [])),
        })
        next_epoch = int(data["epoch"]) + 1
        new_title = task_title.strip() if isinstance(task_title, str) and task_title.strip() else data["task_title"]
        replacement = _new_network(sid, new_title, epoch=next_epoch,
                                   reserved_ids=set(data["nodes"]) | _historical_ids(data))
        data.update({key: replacement[key] for key in (
            "epoch", "root_id", "nodes", "task_title", "planning_status", "planning_note"
        )})
        # The initialization binding remains valid across plan epochs. Durable
        # ledger entries also survive replacing or clearing this task object.
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid, response_snapshot=response_snapshot)


def _format_detail(progress: dict[str, Any] | None) -> str:
    if progress is None:
        return "当前会话没有活跃的进度跟踪。"
    if progress.get("mode") == "legacy_archive":
        if progress.get("legacy_history_warning") or progress.get("legacy_history", {}).get("status") == "unavailable":
            return "旧版历史当前不可读取；任务是否存在及原结果均未知。请检查旧版历史存储后重试，不能据此认定没有任务或任务已完成。"
        return "当前会话只有只读旧版历史；使用 get_progress(include_history=true) 查看原始任务结果。继续工作请显式初始化新目标，旧版结果不会迁移为已验收。"
    if progress.get("schema_version") == 2:
        lines = [
            f"📋 目标：{progress['task_title']}（计划第 {progress['epoch'] + 1} 轮）",
            f"🧭 规划状态：{progress.get('planning_status', 'required')}",
            f"📊 叶任务结束：{progress['completed']}/{progress['total']}（{progress['percent']}%），已解决 {progress['resolved_leaf_count']}，未解决 {progress['open_leaf_count']}，可执行 {progress['ready_count']}，运行中 {progress['executing_count']}，待验收 {progress['pending_review_count']}",
            f"状态：{progress['status']}",
        ]
        if progress.get("planning_status") == "required":
            lines.append("下一步：先提交粗粒度任务拆分；确为单步工作时，初始化时记录 atomic_reason。")
        else:
            lines.extend(f"下一步：{action}" for action in progress.get("next_actions", []))
        if progress.get("planning_note"):
            lines.append(f"单步原因：{progress['planning_note']}")
        for task in progress.get("split_review_due", []):
            lines.append(
                f"⚠️ 拆分复核到期：[{task['id']}] {task['title']} 已运行 {task['elapsed_minutes']} 分钟。"
                "请现在检查是否有可独立执行或验收的阶段并拆分、安排；若拆分会干扰仍在运行的命令/Dagu/CI/子智能体，"
                "保留该执行并说明原因，另拆安全的独立阶段；确无自然边界时，在任务摘要记录"
                "“拆分复核：无需拆分—原因”。"
            )
        main_session = progress.get("main_session")
        if isinstance(main_session, dict):
            provenance = main_session.get("metadata_provenance", {})
            verification = "调用方填报，未经 Codex 宿主核验" if provenance.get("verification") == "not_verified_by_codex_host" else "来源未标注"
            lines.append(
                f"主会话设置：model={main_session.get('model') or '未记录'}；"
                f"reasoning={main_session.get('reasoning_effort') or '未记录'}；{verification}"
            )
        lines.append("当前节点状态、执行提交与验收证据见 structuredContent.progress；历史记录按需读取 include_history=true。")
        return "\n".join(lines)
    lines = [
        f"📋 任务：{progress['task_title']}",
        f"📊 进度：{progress['completed']}/{progress['total']} 步完成（{progress['percent']}%）",
        f"状态：{progress['status']}",
    ]
    for task in progress.get("split_review_due", []):
        lines.append(
            f"⚠️ 拆分复核到期：步骤 [{task['id']}] {task['title']} 已运行 {task['elapsed_minutes']} 分钟。"
            "请现在检查并拆分可独立阶段；若不拆，在步骤备注记录“拆分复核：无需拆分—原因”。"
        )
    lines.append("步骤状态与备注见 structuredContent.progress.steps；历史记录按需读取 include_history=true。")
    return "\n".join(lines)


def _project_execution_records(progress: dict[str, Any], include_records: bool) -> dict[str, Any]:
    """Keep current execution evidence; load append-only execution logs on demand.

    Work on shallow projection copies: arrays in storage/snapshots are immutable
    here and must remain available to explicit include_history reads and exports.
    """
    projected = dict(progress)
    field = "nodes" if progress.get("schema_version") == 2 else "steps"
    if field not in progress:
        return projected
    records = []
    for source in progress[field]:
        record = dict(source)
        attempts = source.get("attempts", [])
        reviews = [attempt for attempt in attempts if attempt.get("review_status") in ("pending", "accepted", "rejected")
                   or attempt.get("submission") or attempt.get("review") or attempt.get("kind") == "synthesis"]
        if (source.get("review_status") in ("pending", "accepted", "rejected") or source.get("submission") or source.get("review")) and not any(
                attempt.get("attempt_id") == source.get("attempt_id") for attempt in reviews):
            reviews.append(source)
        record.update(records_included=include_records, attempt_count=len(attempts), review_record_count=len(reviews),
                      invalidated_result_count=len(source.get("invalidated_results", [])),
                      reassignment_count=len(source.get("reassignment_history", [])),
                      stage_inventory_count=len(source.get("stage_inventories", [])))
        if not include_records:
            for key in ("attempts", "invalidated_results", "reassignment_history", "stage_inventories"):
                record.pop(key, None)
        records.append(record)
    projected[field] = records
    projected["records_included"] = include_records
    return projected


def _parallel_progress_payloads(document: dict[str, Any], state_revision: int) -> list[dict[str, Any]]:
    """Project peer roots from this response's document, without reading storage.

    Request registration order is the display order. Resolved acceptances live
    in task history; unresolved/cancelled requests remain visible. Queued requests are
    not parallel networks. Projection works on copies because network validation
    normalizes children; a read response must not change the captured document.
    """
    state = document.get("session_work_state", {})
    foreground_id = _task_instance_id(document)
    projections = []
    omitted = {"task_history", "history", "session_work_state", "session_work", "parallel_tasks",
               "legacy_history", "legacy_history_warning", "history_summary"}
    for request in state.get("requests", []):
        iid = request.get("task_instance_id")
        instance = state.get("instances", {}).get(iid)
        if (request.get("mode") != "parallel" or iid == foreground_id
                or instance is None or instance.get("schema_version") != 2 or _parallel_is_archivable(instance)):
            continue
        current = {key: deepcopy(value) for key, value in instance.items() if key not in omitted}
        progress = _network_progress_payload(current)
        for key in omitted:
            progress.pop(key, None)
        progress.update(request_id=request["request_id"], request_status=_request_status(instance),
                        state_revision=state_revision, history_included=False)
        if instance.get("task_context"):
            progress["task_context"] = deepcopy(instance["task_context"])
        if instance.get("request_cancelled"):
            progress.update(request_cancelled=True, status="cancelled", ready_tasks=[], ready_count=0,
                            next_actions=["请求已取消；不得继续执行，迟到更新会被拒绝。"])
            for node in progress["nodes"]:
                node["ready"] = False
                if node.get("execution_status") == "pending":
                    node.update(status="blocked", request_cancelled=True)
        projections.append(_project_execution_records(progress, False))
    return projections


def _tool_result(
    text: str, progress: dict[str, Any] | None, session_id: str,
    *, state_revision: int | None = None, include_history: bool = False,
    response_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if response_snapshot is None:
        # Compatibility for callers holding a historical payload: never combine
        # that payload with metadata from a later document.
        with _session_lock(session_id):
            response_snapshot = _capture_response_snapshot(session_id)
        document = response_snapshot["document"]
        selected = (_task_from_document(document, progress.get("task_instance_id"), writable=False)
                    if progress and document and (progress.get("task_instance_id") == _task_instance_id(document)
                       or progress.get("task_instance_id") in document.get("session_work_state", {}).get("instances", {})) else None)
        coherent = bool(selected and progress.get("state_revision", 0) == selected.get("state_revision", 0))
    else:
        document = response_snapshot["document"]
        selected = (_task_from_document(document, progress.get("task_instance_id"), writable=False)
                    if progress and document else None)
        coherent = True
    view_sequence = response_snapshot["view_sequence"]
    session_work = _session_work(document) if coherent and document and document.get("session_work_state") else None
    if coherent:
        state_revision = response_snapshot["state_revision"]
        if progress is not None:
            progress = dict(progress)
            progress["state_revision"] = state_revision
    if progress is not None:
        progress = dict(progress)
        if coherent and document and progress.get("task_instance_id") == _task_instance_id(document):
            progress["task_history"] = _parallel_task_history(document)
        legacy = progress.get("legacy_history") or _legacy_history(session_id, include_history)
        if legacy:
            progress["legacy_history"] = legacy
            if legacy.get("warning"):
                progress["legacy_history_warning"] = legacy["warning"]
        progress["history_summary"] = {
            "task_count": len(progress.get("task_history", [])),
            "plan_count": len(progress.get("history", [])),
            "legacy_task_count": progress.get("legacy_history", {}).get("task_count", 0),
        }
        progress["history_included"] = include_history
        if not include_history:
            for key in ("task_history", "history", "legacy_history"):
                progress.pop(key, None)
    if progress is not None and session_work is not None:
        if include_history:
            raw_requests = {r["request_id"]: r for r in document["session_work_state"]["requests"]}
            for item in session_work["requests"]:
                item["revisions"] = deepcopy(raw_requests[item["request_id"]].get("revisions", []))
        progress["session_work"] = session_work
        if progress.get("task_instance_id") == _task_instance_id(document):
            progress["parallel_tasks"] = _parallel_progress_payloads(document, state_revision)
        if selected and selected.get("task_context"):
            progress["task_context"] = deepcopy(selected["task_context"])
            if include_history:
                progress["context_revisions"] = deepcopy(selected.get("context_revisions", []))
        if selected and selected.get("request_cancelled"):
            progress["ready_tasks"] = []
            for node in progress.get("nodes", []):
                node["ready"] = False
            progress["next_actions"] = ["请求已取消；不得继续执行，迟到更新会被拒绝。"]
        progress["next_actions"] = list(progress.get("next_actions", [])) + session_work["next_actions"]
        if session_work["next_actions"]:
            text += "\n会话下一步：\n" + "\n".join(session_work["next_actions"])
    if progress is not None:
        progress = _project_execution_records(progress, include_history)
    if state_revision is None:
        state_revision = progress.get("state_revision", 0) if progress is not None else 0
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "progress": progress,
            "session_id": session_id,
            "state_revision": state_revision,
            "view_sequence": view_sequence,
            **({"session_work": session_work} if session_work is not None else {}),
        },
    }


def init_progress(
    steps: list[str], task_title: str = "", session_id: str | None = None,
    legacy_linear: bool = False, new_task_reason: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id, generate=True)
    if not isinstance(steps, list) or not steps or not all(
        isinstance(step, str) and step.strip() for step in steps
    ):
        raise ProgressError("steps 必须是非空字符串列表。")
    if len(steps) >= 2 and legacy_linear is not True:
        raise ProgressError(
            "多步骤新任务默认必须使用 HTN：请调用 init_task_network 并分解任务。"
            "仅在用户明确要求线性步骤或恢复旧版线性任务时，才传 legacy_linear=true。"
        )
    with _session_lock(sid):
        previous = _read(sid)
        _assert_can_replace_task(previous, new_task_reason)
        now = _now()
        data = {
            "schema_version": 1,
            "session_id": sid,
            "task_instance_id": str(uuid.uuid4()),
            "task_history": _archive_current_task(previous, new_task_reason),
            "task_title": task_title if isinstance(task_title, str) else "",
            "steps": [
                {
                    "description": step.strip(),
                    "status": "pending",
                    "started_at": None,
                    "finished_at": None,
                }
                for step in steps
            ],
            "created_at": now,
            "updated_at": now,
        }
        _inherit_work_state(previous, data)
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid, response_snapshot=response_snapshot)


def update_step(
    session_id: str,
    step_index: int,
    status: str,
    note: str = "",
    assignment: dict[str, Any] | None = None,
    task_instance_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data = _select_task(sid, task_instance_id)
        if data is None:
            raise ProgressError("该 session_id 没有进度数据；请先调用 init_progress。")
        if "steps" not in data:
            raise ProgressError("当前会话使用 HTN 任务网络；请用 update_task 更新任务节点。")
        _validate_task_instance(data, task_instance_id)
        if not isinstance(step_index, int) or isinstance(step_index, bool):
            raise ProgressError("step_index 必须是整数。")
        if step_index < 0 or step_index >= len(data["steps"]):
            raise ProgressError(f"step_index {step_index} 超出范围（0–{len(data['steps']) - 1}）。")
        if status not in VALID_STATUSES:
            raise ProgressError(f"status 必须是 {VALID_STATUSES} 之一。")
        if note and not isinstance(note, str):
            raise ProgressError("note 必须是字符串。")
        normalized_assignment = _normalize_assignment(assignment) if assignment is not None else None
        step = data["steps"][step_index]
        previous_status = step["status"]
        if attempt_id is not None and (not isinstance(attempt_id, str) or not attempt_id.strip()):
            raise ProgressError("attempt_id 必须是非空字符串。")
        current_attempt = step.get("attempt_id")
        if current_attempt:
            if attempt_id != current_attempt and not (attempt_id is None and step.get("legacy_attempt")):
                raise ProgressError("attempt_id 缺失或不匹配；旧执行尝试不能更新、完成或重置当前步骤。")
        elif attempt_id is not None:
            raise ProgressError("当前步骤尚无该 attempt_id；请先启动并读取新的执行尝试。")
        if status in ("in_progress", "completed") and any(
            previous["status"] != "completed" for previous in data["steps"][:step_index]
        ):
            raise ProgressError("顺序步骤必须先完成前面的步骤。")
        if status == "in_progress" and any(
            index != step_index and other["status"] == "in_progress"
            for index, other in enumerate(data["steps"])
        ):
            raise ProgressError("顺序模式最多只能有一个 in_progress 步骤。")
        if status == "completed" and previous_status == "pending":
            raise ProgressError("顺序步骤必须先设置为 in_progress，再标记为 completed。")
        if status != "completed" and previous_status == "completed" and any(
            following["status"] != "pending" for following in data["steps"][step_index + 1:]
        ):
            raise ProgressError("重开前序步骤前，请先从后往前将后续步骤恢复为 pending。")
        step.setdefault("started_at", None)
        step.setdefault("finished_at", None)
        # Existing running/completed v1 storage has no token. Preserve that
        # attempt, permit legacy calls only for its remaining lifetime, and
        # require tokens for every start after reset/reopen.
        if not current_attempt and previous_status in ("in_progress", "completed"):
            step["attempt"] = int(step.get("attempt", 0)) + 1
            step["attempt_id"] = str(uuid.uuid4())
            step["legacy_attempt"] = True
            step.setdefault("attempts", []).append({
                "attempt": step["attempt"], "attempt_id": step["attempt_id"],
                "kind": "execution", "legacy_attempt": True,
                "status": previous_status,
                "execution_status": "running" if previous_status == "in_progress" else "finished",
                "started_at": step["started_at"], "finished_at": step["finished_at"],
                "assignment": deepcopy(step.get("assignment", {})),
                "summary": step.get("note", ""), "evidence": [],
            })
        if status == "pending":
            if step.get("attempts") and step.get("attempt_id"):
                record = step["attempts"][-1]
                if previous_status == "in_progress":
                    record.update(status="cancelled", execution_status="cancelled", finished_at=_now(),
                                  summary=note or step.get("note", ""))
            step["started_at"] = None
            step["finished_at"] = None
            step.pop("time_boundary_sources", None)
            step.pop("assignment", None)
            step["attempt_id"] = None
            step["legacy_attempt"] = False
        elif status == "in_progress":
            if previous_status != "in_progress" or not step["started_at"]:
                _require_execution_metadata(normalized_assignment)
                step.pop("time_boundary_sources", None)
                step["started_at"] = _now()
                # A new run (including reopening a completed step) gets a fresh
                # snapshot so fields from an earlier dispatch cannot linger.
                step["assignment"] = _stamp_assignment(normalized_assignment or {}, scope="step_start")
                step["attempt"] = int(step.get("attempt", 0)) + 1
                step["attempt_id"] = str(uuid.uuid4())
                step["legacy_attempt"] = False
                step.pop("note", None)
                step.setdefault("attempts", []).append({
                    "attempt": step["attempt"], "attempt_id": step["attempt_id"],
                    "kind": "execution", "status": "in_progress", "execution_status": "running",
                    "started_at": step["started_at"], "finished_at": None,
                    "assignment": deepcopy(step["assignment"]), "summary": "", "evidence": [],
                })
            elif normalized_assignment is not None:
                snapshot = step.get("assignment", {})
                changed = [
                    key for key, value in normalized_assignment.items()
                    if snapshot.get(key) != value
                ]
                if changed:
                    raise ProgressError(
                        "in_progress 步骤的执行快照不可修改；不一致字段："
                        + ", ".join(changed)
                        + "。如需更换执行配置，请先设为 pending 再重新启动。"
                    )
            step["finished_at"] = None
        elif status == "completed":
            if previous_status != "completed":
                step["finished_at"] = _now()
            _ensure_time_bounds(step, step["finished_at"] or _now(), "termination_fallback")
            if normalized_assignment is not None and previous_status in ("in_progress", "completed"):
                snapshot = step.get("assignment", {})
                changed = [
                    key for key, value in normalized_assignment.items()
                    if snapshot.get(key) != value
                ]
                if changed:
                    raise ProgressError(
                        "步骤的执行快照在完成时不可修改；不一致字段："
                        + ", ".join(changed)
                        + "。"
                    )
            step["legacy_attempt"] = False
        step["status"] = status
        if note:
            data["steps"][step_index]["note"] = note
        elif status == "pending":
            data["steps"][step_index].pop("note", None)
        if status != "pending" and step.get("attempts"):
            step["attempts"][-1].update(
                status=status, execution_status="finished" if status == "completed" else "running",
                finished_at=step["finished_at"], summary=step.get("note", ""),
            )
            if status == "completed" and not _valid_boundary(step["attempts"][-1].get("started_at")):
                step["attempts"][-1]["started_at"] = step["started_at"]
                step["attempts"][-1]["time_boundary_sources"] = deepcopy(step.get("time_boundary_sources", {}))
        _write(sid, data)
        response_snapshot = _capture_response_snapshot(sid)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid, response_snapshot=response_snapshot)


def get_progress_view(session_id: str, include_history: bool = False, task_instance_id: str | None = None) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    if not isinstance(include_history, bool):
        raise ProgressError("include_history 必须是布尔值。")
    with _session_lock(sid):
        response_snapshot = _capture_response_snapshot(sid)
        data = _task_from_document(response_snapshot["document"], task_instance_id, writable=False)
        progress = _progress_payload(data) if data is not None else None
        revision = data.get("state_revision", 0) if data is not None else _read_order(sid)["state_revision"]
        if progress is None:
            legacy = _legacy_history(sid, include_history)
            if legacy and (legacy.get("status") == "unavailable" or legacy.get("task_count", 0)):
                progress = {"mode": "legacy_archive", "read_only": True, "schema_version": 3,
                            "session_id": sid, "state_revision": revision,
                            "legacy_history": legacy,
                            "task_title": "旧版任务历史", "status": "legacy_archive"}
    return _tool_result(_format_detail(progress), progress, sid, state_revision=revision, include_history=include_history, response_snapshot=response_snapshot)


def render_progress(session_id: str) -> dict[str, Any]:
    # The host must associate this tool's result with the UI template from the
    # tool descriptor so the rendered view receives structuredContent.
    result = _wire_receipt("render_progress", {}, get_progress_view(session_id))
    source = result["structuredContent"]
    if source.get("task_instance_id"):
        sid = source["session_id"]
        with _session_lock(sid):
            path = _ui_view_file(sid)
            metadata = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            intent = {"id": str(uuid.uuid4()), "view_sequence": source["view_sequence"],
                      "task_instance_id": source["task_instance_id"], "consumed": False}
            # Concurrent render completions must not replace a newer issuance.
            if intent["view_sequence"] > metadata.get("display_intent", {}).get("view_sequence", -1):
                metadata["display_intent"] = intent
                _atomic_json(path, metadata)
            source["display_intent"] = intent["id"]
    return result


def consume_display_intent(session_id: str, view_id: str, display_intent: str) -> dict[str, Any]:
    """One app mount may act on the newest render, once, even after restart."""
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    with _session_lock(sid):
        data = _read(sid)
        path = _ui_view_file(sid)
        metadata = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        intent = metadata.get("display_intent", {})
        status = "superseded"
        if (data and isinstance(display_intent, str) and display_intent == intent.get("id")
                and intent.get("task_instance_id") == _task_instance_id(data)
                and vid not in metadata.get("released_views", {})):
            if intent.get("consumed"):
                status = "consumed"
            else:
                intent["consumed"] = True
                intent["view_id"] = vid
                now = time.time()
                active = any(other != vid and _ui_fullscreen_active(view, now)
                             for other, view in metadata.get("views", {}).items())
                pending = metadata.get("display_pending", {})
                if active or (pending.get("view_id") != vid and pending.get("until", 0) > now):
                    status = "already_open"
                else:
                    status = "granted"
                    # Reserve the request-to-host interval, without treating it
                    # as evidence that a fullscreen tab actually exists.
                    metadata["display_pending"] = {"view_id": vid, "until": now + 20}
                _atomic_json(path, metadata)
    return {"content": [], "structuredContent": {"session_id": sid, "view_id": vid,
            "display_intent": display_intent, "status": status}}



# Public query responses are selective. The app-only reader above retains its
# complete current snapshot so UI/export traffic never enters model tool results.
_QUERY_VIEWS = ("overview", "tree", "tasks", "records", "context", "history", "full")
_RECORD_KINDS = ("attempts", "invalidated_results", "reassignment_history", "stage_inventories")
_HISTORY_KINDS = ("history", "task_history", "context_revisions", "legacy_history", "handoffs", "handoff_closures", "closure_reviews")
_OVERVIEW_FIELDS = ("schema_version", "mode", "read_only", "session_id", "task_instance_id", "state_revision",
                    "epoch", "revision", "root_id", "status", "root_status", "planning_status", "completed", "total",
                    "percent", "resolved_leaf_count", "open_leaf_count", "failed_leaf_count", "ready_count",
                    "executing_count", "pending_review_count", "blocked_count", "history_summary", "history_included",
                    "closure_review_binding", "closure_review_due", "handoff_summary")
_TREE_FIELDS = ("id", "parent_id", "status", "execution_status", "outcome", "review_status", "ready", "attempt_id", "is_added", "addition_reason", "stage_inventory_due_attempt")
_MUTATION_TOOLS = frozenset(("init_progress", "init_task_network", "decompose_task", "update_task", "update_step",
                           "bind_task_agent", "review_task", "reassign_task", "rebuild_task_network", "route_request",
                           "activate_request", "update_request", "cancel_request", "update_task_context", "clear_progress",
                           "handoff_request", "review_task_closure", "review_stage_inventory"))


def _query_integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
        raise ProgressError(f"{name} 必须是 {minimum}..{maximum if maximum is not None else '无限'} 范围的整数。")
    return value


def _page(items: list[Any], offset: int, limit: int) -> dict[str, Any]:
    end = min(offset + limit, len(items))
    return {"offset": offset, "limit": limit, "total": len(items), "has_more": end < len(items),
            "next_offset": end if end < len(items) else None}


def _brief_node(node: dict[str, Any]) -> dict[str, Any]:
    title = node.get("title", "")
    return {**{key: node[key] for key in _TREE_FIELDS if key in node},
            "title": title[:200], "title_truncated": len(title) > 200,
            "child_count": len(node.get("children", [])),
            "dependency_count": len(node.get("dependencies", [])),
            "execution_mode": (node.get("assignment") or node.get("planned_assignment") or {}).get("execution_mode")}


def get_progress(session_id: str, include_history: bool = False, task_instance_id: str | None = None,
                 *, view: str | None = None, task_id: str | None = None, task_ids: list[str] | None = None,
                 depth: int = 1, offset: int = 0, limit: int = 50, record_kind: str = "attempts",
                 history_kind: str = "history", expected_state_revision: int | None = None) -> dict[str, Any]:
    """Read one explicit layer from one captured revision; never persist task changes.

    Full means the selected instance and its archives/records, plus compact peers
    and the session request registry. Peer histories require their own iid read.
    Pagination is stable within a revision; callers can pin subsequent pages.
    """
    if not isinstance(include_history, bool):
        raise ProgressError("include_history 必须是布尔值。")
    if include_history and view not in (None, "full"):
        raise ProgressError("include_history=true 仅兼容 view=full；请勿与分层视图混用。")
    view = "full" if include_history else (view or "overview")
    if view not in _QUERY_VIEWS:
        raise ProgressError("view 必须是 overview/tree/tasks/records/context/history/full。")
    _query_integer(offset, "offset", 0)
    _query_integer(limit, "limit", 1, 200)
    _query_integer(depth, "depth", 0, 8)
    if expected_state_revision is not None:
        _query_integer(expected_state_revision, "expected_state_revision", 0)
    if task_id is not None and (not isinstance(task_id, str) or not task_id):
        raise ProgressError("task_id 必须是非空字符串。")
    if view == "tasks" and (not isinstance(task_ids, list) or not 1 <= len(task_ids) <= 20
                            or any(not isinstance(x, str) or not x for x in task_ids)):
        raise ProgressError("tasks 视图须提供 1..20 个非空 task_ids。")
    if view == "records" and (not task_id or record_kind not in _RECORD_KINDS):
        raise ProgressError("records 视图须提供 task_id 和有效 record_kind。")
    if view == "history" and history_kind not in _HISTORY_KINDS:
        raise ProgressError("history_kind 必须是 history/task_history/context_revisions/legacy_history。")
    # Include append-only arrays only in views that explicitly request them.
    result = get_progress_view(session_id, view in ("full", "records", "history"), task_instance_id)
    source = result["structuredContent"]
    if expected_state_revision is not None and source["state_revision"] != expected_state_revision:
        raise ProgressError("state_revision 已改变；请从第一页重新读取，不能混合不同版本的分页。")
    metadata = {key: source[key] for key in ("session_id", "state_revision", "view_sequence")}
    metadata.update(view=view, scope="instance", consistency="pin expected_state_revision across pages")
    p = source.get("progress")
    if p is None:
        return {"content": [{"type": "text", "text": "当前会话没有进度数据。"}],
                "structuredContent": {**metadata, "progress": None}}
    if view == "full":
        source.update(view="full", scope="instance", peer_records_included=False,
                      full_scope="Selected instance records and archives; peers are current compact snapshots. Read each peer iid for its history.")
        return {"content": [{"type": "text", "text": "已按显式请求返回目标实例完整数据；并行实例历史须分别读取。"}], "structuredContent": source}
    output = {key: p[key] for key in _OVERVIEW_FIELDS if key in p}
    output.update(task_title=p.get("task_title", "")[:200], title_truncated=len(p.get("task_title", "")) > 200,
                  records_included=False, history_included=False)
    metadata.update(task_instance_id=p.get("task_instance_id"), epoch=p.get("epoch"), root_id=p.get("root_id"))
    nodes = p.get("nodes", p.get("steps", []))
    indexed = {n.get("id", str(i)): n for i, n in enumerate(nodes)}
    output["node_count"] = len(nodes)
    due = {item["id"]: item for item in p.get("split_review_due", [])}
    output["split_review_due_count"] = len(due)
    output["split_review_due_ids"] = list(due)[:20]
    output["split_review_due_ids_has_more"] = len(due) > 20
    inventory_due = [{"task_id": n["id"], "attempt_id": n["stage_inventory_due_attempt"]}
                     for n in nodes if n.get("stage_inventory_due_attempt")]
    output["stage_inventory_due"] = inventory_due[:20]
    output["stage_inventory_due_count"] = len(inventory_due)
    output["stage_inventory_due_has_more"] = len(inventory_due) > 20
    if p.get("legacy_history_warning"):
        output["legacy_history_warning"] = p["legacy_history_warning"][:500]
    work = source.get("session_work") or {}
    requests = work.get("requests", [])
    output["session_work_summary"] = {
        "foreground_task_instance_id": (work.get("foreground") or {}).get("task_instance_id"),
        "request_count": len(requests), "queued_count": sum(r.get("status") == "queued" for r in requests),
        "pending_review_count": sum(r.get("status") == "pending_review" for r in requests),
        "can_finish_session": work.get("can_finish_session", p.get("status") in ("completed", "open", "failed", "cancelled"))}
    if view == "tree":
        selected_id = task_id or p.get("root_id")
        if p.get("schema_version") == 2:
            if selected_id not in indexed:
                raise ProgressError("task_id 不存在于目标实例当前 epoch。")
            frontier, traversed = [(selected_id, 0)], []
            for node_id, level in frontier:
                n = indexed[node_id]
                traversed.append({**_brief_node(n), "depth": level, "split_review_due": node_id in due})
                if level < depth:
                    frontier.extend((child, level + 1) for child in n.get("children", []))
            depth_limited = any(n["depth"] == depth and n["child_count"] for n in traversed)
        else:
            if task_id is not None:
                raise ProgressError("线性任务 tree 不接受 task_id；使用 tasks 的步骤索引。")
            traversed = [{**_brief_node(n), "id": str(i), "depth": 0} for i, n in enumerate(nodes)]
            depth_limited = False
        output.update(nodes=traversed[offset:offset + limit], pagination=_page(traversed, offset, limit),
                      depth=depth, depth_limited=bool(depth_limited), task_id=selected_id)
    elif view == "tasks":
        missing = [key for key in task_ids if key not in indexed]
        if missing:
            raise ProgressError("task_ids 含目标实例当前 epoch 不存在的节点。")
        output["nodes"] = [{**deepcopy(indexed[key]), "split_review_due": key in due,
                            **({"split_review": deepcopy(due[key])} if key in due else {})} for key in dict.fromkeys(task_ids)]
    elif view == "records":
        if task_id not in indexed:
            raise ProgressError("task_id 不存在于目标实例当前 epoch。")
        records = indexed[task_id].get(record_kind, [])
        output.update(task_id=task_id, record_kind=record_kind, records=deepcopy(records[offset:offset + limit]),
                      pagination=_page(records, offset, limit), records_included=True)
    elif view == "context":
        output["task_context"] = deepcopy(p.get("task_context", {}))
        output["session_work"] = {key: deepcopy(work[key]) for key in ("foreground", "return_to", "can_finish_session", "verification") if key in work}
        output["session_work"].update(requests=deepcopy(requests[offset:offset + limit]), pagination=_page(requests, offset, limit))
    elif view == "history":
        records = p.get(history_kind, [])
        if history_kind == "legacy_history":
            legacy = records if isinstance(records, dict) else {}
            records = legacy.get("records", [])
            output["legacy_history_status"] = {key: deepcopy(value) for key, value in legacy.items() if key != "records"}
        output.update(history_kind=history_kind, records=deepcopy(records[offset:offset + limit]),
                      pagination=_page(records, offset, limit), history_included=True)
    text = f"已读取 {view}；按需查询 tree/tasks/records/context/history，完整数据显式使用 full。"
    return {"content": [{"type": "text", "text": text}], "structuredContent": {**metadata, "progress": output}}


def _wire_receipt(name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source = result.get("structuredContent", {})
    p = source.get("progress") or {}
    ack = {key: source[key] for key in ("session_id", "state_revision", "view_sequence") if key in source}
    ack.update({key: p[key] for key in ("task_instance_id", "epoch", "revision", "root_id", "planning_status", "status") if key in p})
    ack["acknowledged"] = True
    ack["receipt"] = True
    if name == "render_progress":
        ack.update(bootstrap=True, view_tool="get_progress_view")
    task_id = args.get("task_id")
    nodes = p.get("nodes", [])
    if name == "update_step":
        index = args.get("step_index")
        steps = p.get("steps", [])
        target = steps[index] if isinstance(index, int) and 0 <= index < len(steps) else None
    else:
        target = next((node for node in nodes if node["id"] == task_id), None)
    if target:
        ack["affected"] = {key: target[key] for key in ("id", "attempt_id", "execution_status", "status", "outcome", "review_status") if key in target}
        if target.get("attempt_id"):
            ack["attempt_id"] = target["attempt_id"]
    if name == "update_step":
        ack["step_index"] = args.get("step_index")
    for key in ("handoff_id", "review_id"):
        if args.get(key):
            ack[key] = args[key]
    if args.get("request_id"):
        ack["request_id"] = args["request_id"]
        request = next((item for item in source.get("session_work", {}).get("requests", []) if item["request_id"] == args["request_id"]), None)
        if request:
            ack.update(request_task_instance_id=request.get("task_instance_id"), request_status=request.get("status"))
    ack["split_review_due_count"] = len(p.get("split_review_due", []))
    ack["next_query"] = {"tool": "get_progress", "view": "overview"}
    stage_done = bool(target and target.get("stage_inventory_due_attempt") and target.get("parent_id") == p.get("root_id")
                      and target.get("execution_status") in TERMINAL_EXECUTION_STATUSES
                      and target.get("review_status") != "pending"
                      and (name == "review_task" or (name == "update_task" and args.get("execution_status") in TERMINAL_EXECUTION_STATUSES)))
    if stage_done:
        ack["stage_inventory_due"] = True
    message = "已请求展示进度；实际模式由宿主决定。" if name == "render_progress" else "已记录。"
    if stage_done:
        message += " 主要阶段已结束：继续下一阶段前盘点未闭环任务是否继续、过期或被覆盖，记录原因、证据及后续动作。"
    retry_review = source.get("retry_review_due")
    if name == "update_task" and retry_review:
        ack["retry_review_due"] = retry_review
        message += (f" 连续 {retry_review['consecutive_attempts']} 次未解决：请比较回执中的最近两次原因和摘要，"
                    "判断是否为相同或相关问题；如有需要，评估优化或重新规划任务并记录判断与下一步。"
                    "信息不足时读取 attempts 记录，不要假定原因相同。")
    return {"content": [{"type": "text", "text": message}], "structuredContent": ack}


def _wire_result(name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError"):
        return result
    if name in _MUTATION_TOOLS:
        return _wire_receipt(name, args, result)
    if name == "get_ready_tasks":
        offset = _query_integer(args.get("offset", 0), "offset", 0)
        limit = _query_integer(args.get("limit", 50), "limit", 1, 200)
        source = result["structuredContent"]
        tasks = source.get("ready_tasks", [])
        # This is an explicit dependency read. Current dependency evidence is
        # preserved once, without attaching the whole tree or repeating in text.
        selected = deepcopy(tasks[offset:offset + limit])
        dependency_evidence = {}
        for task in selected:
            for dependency in task.get("dependencies", []):
                dependency_evidence.setdefault(dependency["id"], {key: dependency.get(key) for key in
                    ("id", "title", "execution_status", "outcome", "summary", "evidence")})
                dependency.pop("summary", None)
                dependency.pop("evidence", None)
        return {"content": [{"type": "text", "text": "已读取可执行任务及必要依赖证据。"}],
                "structuredContent": {**{key: source[key] for key in ("session_id", "state_revision", "view_sequence")},
                    "task_instance_id": (source.get("progress") or {}).get("task_instance_id"),
                    "ready_tasks": selected, "dependency_evidence": dependency_evidence,
                    "pagination": _page(tasks, offset, limit)}}
    return result


def _ui_view_result(session_id: str, view_id: str, status: str) -> dict[str, Any]:
    messages = {
        "registered": "已登记当前进度视图。",
        "latest": "当前实例是本会话的活动进度视图。",
        "hidden_by_fullscreen": "本会话已打开完整进度标签页，会话内视图暂时隐藏。",
        "superseded": "当前实例已被本会话的新进度视图替代。",
        "duplicate_fullscreen": "本会话已有完整进度标签页打开。",
        "unregistered": "本会话尚未登记进度视图。",
        "missing_progress": "本会话已无进度数据，视图不再有效。",
    }
    return {
        "content": [{"type": "text", "text": messages[status]}],
        "structuredContent": {
            "session_id": session_id,
            "view_id": view_id,
            "status": status,
            "registered": status in ("registered", "latest", "hidden_by_fullscreen", "superseded"),
            "is_latest": status in ("registered", "latest", "hidden_by_fullscreen"),
        },
    }


def _ui_source_order(source: dict[str, Any]) -> tuple[int, int]:
    return tuple(value if isinstance(value, int) and not isinstance(value, bool) else -1
                 for value in (source.get("state_revision"), source.get("view_sequence")))


def _ui_fullscreen_active(view: dict[str, Any], now: float) -> bool:
    return (view.get("display_mode") == "fullscreen"
            and isinstance(view.get("expires_at"), (int, float))
            and view["expires_at"] > now)


def _prepare_ui_registry(metadata: dict[str, Any]) -> None:
    """Keep issuance order separate from the owner of a currently mounted card."""
    views = metadata.setdefault("views", {})
    metadata.setdefault("released_views", {})
    for view in views.values():
        # Keep expired mounts' original source until teardown: a late heartbeat
        # or mode change must not acquire a new inline issuance identity.
        # Migrate registries written before explicit ownership was introduced.
        view.setdefault("inline_source", {key: view.get(key) for key in (
            "state_revision", "view_sequence", "task_instance_id"
        )})
        view.setdefault("inline_eligible", view.get("display_mode") == "inline")
    inline = {vid: view for vid, view in views.items()
              if view.get("display_mode") == "inline" and view.get("inline_eligible")}
    if metadata.get("latest_inline") is None and inline:
        metadata["latest_inline"] = dict(max(
            inline.values(), key=lambda view: _ui_source_order(view["inline_source"])
        )["inline_source"])
    if metadata.get("inline_owner") not in inline:
        # Only cards that previously qualified for ownership can be restored.
        # A newly mounted historical result cannot lower the issuance watermark.
        metadata["inline_owner"] = max(
            inline, key=lambda vid: _ui_source_order(inline[vid]["inline_source"]), default=None
        )


def register_ui_view(
    session_id: str, view_id: str, view_sequence: int | None = None,
    state_revision: int | None = None, task_instance_id: str | None = None,
    display_mode: str = "inline",
    mode_generation: int | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    with _session_lock(sid):
        data = _read(sid)
        if data is None:
            return _ui_view_result(sid, vid, "missing_progress")
        if display_mode not in ("inline", "fullscreen"):
            raise ProgressError("display_mode 必须是 inline 或 fullscreen。")
        if mode_generation is not None and (
            not isinstance(mode_generation, int) or isinstance(mode_generation, bool) or mode_generation < 0
        ):
            raise ProgressError("mode_generation 必须是非负整数。")
        path = _ui_view_file(sid)
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        now = time.time()
        previous = current.get("views", {}).get(vid)
        mode_state = current.get("mode_generations", {}).get(vid)
        previous_generation = mode_state["generation"] if mode_state else previous.get("mode_generation") if previous else None
        previous_mode = mode_state["display_mode"] if mode_state else previous.get("display_mode") if previous else None
        # Reject stale/legacy requests before preparing ownership or releasing
        # any lease. A late demotion is as unsafe as a late fullscreen renewal.
        if previous_generation is not None and (
            mode_generation is None or mode_generation < previous_generation
            or (mode_generation == previous_generation and previous_mode != display_mode)
        ):
            return _ui_view_result(sid, vid, "superseded")
        if vid in current.get("released_views", {}):
            return _ui_view_result(sid, vid, "superseded")
        _prepare_ui_registry(current)
        if current.get("display_pending", {}).get("view_id") == vid:
            current.pop("display_pending", None)
        views = current["views"]
        previous = views.get(vid)
        returning_inline = bool(previous and previous.get("display_mode") == "fullscreen"
                                and display_mode == "inline")
        if mode_generation is not None:
            # Mode observations and source eligibility are independent. Even a
            # stale task result, missing source, or duplicate fullscreen must
            # fence off requests from an earlier host mode. No lease is granted
            # merely by recording this watermark.
            current.setdefault("mode_generations", {})[vid] = {
                "generation": mode_generation, "display_mode": display_mode,
            }
            if previous:
                previous["mode_generation"] = mode_generation
        if returning_inline:
            # Release the fullscreen lease even if this source is subsequently
            # rejected as stale. Polling in a tab must not promote its card source.
            previous["display_mode"] = "inline"
            previous["expires_at"] = None
        if returning_inline or mode_generation is not None:
            _prepare_ui_registry(current)
            _atomic_json(path, current)
        # Legacy cards cannot claim ownership over an ordered result.
        if view_sequence is None or state_revision is None or task_instance_id is None:
            return _ui_view_result(sid, vid, "unregistered")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
               for value in (view_sequence, state_revision)):
            raise ProgressError("视图顺序必须是服务端工具结果返回的非负整数。")
        order = _read_order(sid)
        if view_sequence > order["view_sequence"] or state_revision > data.get("state_revision", 0):
            raise ProgressError("视图顺序超出服务端已发出的范围。")
        if task_instance_id != _task_instance_id(data):
            return _ui_view_result(sid, vid, "superseded")
        candidate = {"state_revision": state_revision, "view_sequence": view_sequence,
                     "task_instance_id": task_instance_id}
        if display_mode == "fullscreen" and any(
            other_id != vid and _ui_fullscreen_active(view, now)
            for other_id, view in views.items()
        ):
            _atomic_json(path, current)
            return _ui_view_result(sid, vid, "duplicate_fullscreen")
        inline_source = (previous["inline_source"] if previous and (
            display_mode == "fullscreen" or returning_inline
        ) else candidate)
        eligible = bool(previous and previous.get("inline_eligible"))
        status = "registered"
        if display_mode == "inline":
            watermark = current.get("latest_inline") or {}
            source_order = _ui_source_order(inline_source)
            latest_order = _ui_source_order(watermark)
            if source_order > latest_order:
                current["latest_inline"] = dict(inline_source)
                current["inline_owner"] = vid
                eligible = True
            elif source_order == latest_order and (
                current.get("inline_owner") in (None, vid) or previous is None
            ):
                # A new mount of the same result replaces its old mount once;
                # an already superseded mount cannot reclaim ownership by retry.
                current["inline_owner"] = vid
                eligible = True
            elif current.get("inline_owner") != vid:
                status = "superseded"
        views[vid] = {**candidate, "display_mode": display_mode,
                      "mode_generation": mode_generation,
                      "inline_source": dict(inline_source), "inline_eligible": eligible,
                      "expires_at": now + 35 if display_mode == "fullscreen" else None}
        _prepare_ui_registry(current)
        _atomic_json(path, current)
    return _ui_view_result(sid, vid, status)


def check_ui_view(session_id: str, view_id: str, display_mode: str = "inline") -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    if display_mode not in ("inline", "fullscreen"):
        raise ProgressError("display_mode 必须是 inline 或 fullscreen。")
    with _session_lock(sid):
        if not _session_file(sid).exists():
            return _ui_view_result(sid, vid, "missing_progress")
        path = _ui_view_file(sid)
        if not path.exists():
            return _ui_view_result(sid, vid, "unregistered")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        now = time.time()
        _prepare_ui_registry(metadata)
        views = metadata["views"]
        if vid in metadata["released_views"]:
            status = "superseded"
        elif vid not in views:
            status = "unregistered"
        elif display_mode == "fullscreen" and not _ui_fullscreen_active(views[vid], now):
            status = "unregistered"
        elif display_mode == "inline" and metadata.get("inline_owner") != vid:
            status = "superseded"
        else:
            fullscreen_open = any(
                other_id != vid and _ui_fullscreen_active(view, now)
                for other_id, view in views.items()
            )
            status = "hidden_by_fullscreen" if display_mode == "inline" and fullscreen_open else "latest"
        _atomic_json(path, metadata)
    return _ui_view_result(sid, vid, status)


def unregister_ui_view(session_id: str, view_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    with _session_lock(sid):
        path = _ui_view_file(sid)
        metadata = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        # view_id denotes one mount for its entire lifetime. Remember release
        # even if an in-flight initial registration has not arrived yet.
        metadata.setdefault("released_views", {})[vid] = time.time()
        metadata.setdefault("views", {}).pop(vid, None)
        if metadata.get("display_pending", {}).get("view_id") == vid:
            metadata.pop("display_pending", None)
        _prepare_ui_registry(metadata)
        _atomic_json(path, metadata)
    return _ui_view_result(sid, vid, "unregistered")


def clear_progress(session_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        document = _read(sid)
        _assert_no_pending_reviews(document, "清除任务")
        for instance in (document or {}).get("session_work_state", {}).get("instances", {}).values():
            _assert_no_pending_reviews(instance, "清除任务")
        # Persist the binding requirement first: even if deletion fails halfway,
        # a later stale write without an instance ID can never bind to new data.
        order = _read_order(sid)
        order["require_task_instance_id"] = True
        order["legacy_history_cleared"] = True
        for item in (document or {}).get("session_work_state", {}).get("requests", []):
            if not item.get("fingerprint_hash"):
                continue
            order.setdefault("request_tombstones", {})[item["request_id"]] = {"fingerprint_hash": item.get("fingerprint_hash"),
                "task_instance_id": item.get("task_instance_id")}
        _atomic_json(_order_file(sid), order)
        path = _session_file(sid)
        if path.exists():
            path.unlink()
        _ui_view_file(sid).unlink(missing_ok=True)
        revision = _advance_order(sid, "state_revision")
        response_snapshot = _capture_response_snapshot(sid)
    return _tool_result("当前会话的进度数据已清除。", None, sid, state_revision=revision, response_snapshot=response_snapshot)


def save_export_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist the App-generated workbook and report where Codex can access them."""
    if not isinstance(files, list) or len(files) != 2:
        raise ProgressError("导出必须同时包含一个 Excel 文件和一个可视化 HTML 文件。")

    prepared: list[tuple[str, str, bytes]] = []
    expected = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".html": "text/html",
    }
    for item in files:
        if not isinstance(item, dict):
            raise ProgressError("导出文件描述必须是对象。")
        name = item.get("name")
        mime_type = item.get("mime_type")
        encoding = item.get("encoding")
        content = item.get("content")
        if not isinstance(name, str) or not name or len(name) > 180:
            raise ProgressError("导出文件名无效。")
        if name != name.strip() or name in {".", ".."} or any(char in name for char in ("/", "\\")) or any(ord(char) < 32 for char in name):
            raise ProgressError("导出文件名只能是普通文件名，不能包含路径或控制字符。")
        extension = Path(name).suffix.lower()
        if not name.startswith("Task-Progress-") or extension not in expected or mime_type != expected[extension]:
            raise ProgressError("仅支持 Task Progress 的 .xlsx 和 .html 导出文件。")
        if not isinstance(content, str):
            raise ProgressError("导出文件内容无效。")
        if extension == ".xlsx" and encoding == "base64":
            try:
                data = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ProgressError("Excel 文件的 Base64 内容无效。") from exc
        elif extension == ".html" and encoding == "utf-8":
            data = content.encode("utf-8")
        else:
            raise ProgressError("导出文件编码与文件类型不匹配。")
        if not data or len(data) > MAX_EXPORT_FILE_BYTES:
            raise ProgressError("导出文件为空或超过 20 MiB 限制。")
        prepared.append((name, mime_type, data))

    suffixes = {Path(name).suffix.lower() for name, _, _ in prepared}
    stems = {Path(name).stem for name, _, _ in prepared}
    if suffixes != set(expected) or len(stems) != 1:
        raise ProgressError("导出文件必须是同名的一对 .xlsx 与 .html 文件。")

    output_dir = Path.home() / "Downloads" / "Task Progress Exports"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProgressError(f"无法创建导出目录：{exc}") from exc

    base_stem = next(iter(stems))
    written: list[Path] = []
    for index in range(1, 1000):
        tagged_stem = base_stem if index == 1 else f"{base_stem}-{index}"
        destinations = [output_dir / f"{tagged_stem}{Path(name).suffix.lower()}" for name, _, _ in prepared]
        if any(path.exists() for path in destinations):
            continue
        try:
            for destination, (_, _, data) in zip(destinations, prepared):
                with destination.open("xb") as stream:
                    written.append(destination)
                    os.chmod(destination, 0o600)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
        except FileExistsError:
            for path in written:
                path.unlink(missing_ok=True)
            written.clear()
            continue
        except OSError as exc:
            for path in written:
                path.unlink(missing_ok=True)
            raise ProgressError(f"保存导出文件失败：{exc}") from exc
        break
    else:
        raise ProgressError("无法为导出文件分配不冲突的文件名。")

    exported = [
        {"name": path.name, "path": str(path), "mime_type": mime_type, "size_bytes": len(data)}
        for path, (_, mime_type, data) in zip(destinations, prepared)
    ]
    return {
        "content": [{"type": "text", "text": f"已将 Excel 和 HTML 保存到 {output_dir}。"}],
        "structuredContent": {"saved": True, "output_directory": str(output_dir), "files": exported},
    }


def _session_property() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "由 init_progress 或 init_task_network 返回，并在本会话的所有调用中复用的会话 ID。",
    }


def _task_instance_property() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "目标 progress 返回的 task_instance_id，显式路由到主任务或隔离并行实例。请求管理工具须传当前 foreground ID；同一 session_id 有 task_history、受控请求/上下文或曾 clear_progress 后写操作必填。未受控且未清理过的首个任务兼容省略；旧实例/attempt更新会被拒绝。",
    }


def _data_tool_meta() -> dict[str, Any]:
    """Keep ordinary data operations from implicitly opening the UI resource."""
    return {}


def _display_tool_meta() -> dict[str, Any]:
    """Bind the UI resource only to the explicit display tool."""
    return {"ui": {"resourceUri": RESOURCE_URI}}


def _assignment_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "计划保留为 planned_assignment；新执行必须匹配计划角色，改派用 reassign_task。主会话叶执行须 self_execution_reason（原子根有 atomic_reason 可豁免）；复杂工作默认委派。子智能体启动须 model/reasoning_effort/tool_identifier/dispatch_text，spawn 后用 bind_task_agent 绑定标识，finished 仅提交待主会话 review_task 的结果。父/root synthesis 必须主会话。所有角色/模型/回执均为调用方填报，未经 Codex 宿主核验。",
        "properties": {
            "execution_mode": {"type": "string", "enum": list(EXECUTION_MODES), "description": "本次叶任务/attempt 的实际执行者；主会话仍负责根任务统筹与验收，子智能体仅负责派发范围"},
            "self_execution_reason": {"type": "string", "description": "主会话亲自执行叶任务的具体原因；复杂工作须记录可检查的例外理由"},
            "complexity": {"type": "string", "enum": ["simple", "complex"], "description": "规划者填报的复杂度，非自动识别；complex 默认委派，main_session 需具体例外理由"},
            "tool_identifier": {"type": "string", "description": "派发工具标识"},
            "tool_name": {"type": "string", "description": "工具中文名称"},
            "dispatch_text": {"type": "string", "description": "完整、自包含的派发任务文本"},
            "agent_name": {"type": "string", "description": "spawn 配置中的计划别名不构成绑定；spawn 返回后须 bind_task_agent。仅明确 follow-up 工具可填已知实际执行者标识用于立即绑定。"},
            "fork_turns": {"type": "string", "enum": ["none"]},
            "model": {"type": "string", "description": "可选；仅填当前执行者明确提供的模型标识，不从配置默认值推断"},
            "reasoning_effort": {"type": "string", "enum": list(REASONING_EFFORTS), "description": "可选；仅填明确提供的思维强度，模型不同支持值可能不同"},
            "codex_thread_id": {"type": "string", "description": "可选的 Codex thread ID；仅在宿主明确提供时填写。当前插件无法验证其归属"},
            "codex_turn_id": {"type": "string", "description": "可选的 Codex turn ID；仅在宿主明确提供时填写。当前插件无法验证其归属"},
            "profile": {"type": "string", "enum": list(PROFILES)},
            "batch": {"type": "string", "description": "派发批次"},
        },
    }


def _task_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "当前 task_instance_id 生命周期内唯一的节点 ID；重规划时也不可复用"},
            "title": {"type": "string", "description": "任务标题"},
            "addition_reason": {"type": "string", "minLength": 1, "description": "执行中追加任务的具体原因；保留为追加标记与审计信息。"},
            "precedes": {"type": "array", "items": {"type": "string"}, "description": "依赖此新增任务的已有后继 ID；原子加入依赖并排序。后继及其子任务必须尚未开始，且必须提供 addition_reason。"},
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "前驱任务 ID 列表",
            },
            "dependency_policy": {
                "type": "string",
                "enum": list(DEPENDENCY_POLICIES),
                "description": "resolved 要求前驱已解决；finished 允许前驱以 open/failed/cancelled 结束后放行，并携带证据。",
            },
            "profile": {"type": "string", "enum": list(PROFILES)},
            "assignment": {
                **_assignment_schema(),
                "description": "必填。计划阶段标记此节点的执行者：execution_mode=main_session 或 subagent。分组节点设 main_session 表示主会话负责协调和综合验收；叶节点按分工指定。它不转移主会话的根任务统筹与集成职责。",
                "required": ["execution_mode"],
            },
        },
        "required": ["id", "title", "assignment"],
    }


TOOLS = [
    {
        "name": "review_stage_inventory", "title": "记录阶段任务盘点",
        "description": "主会话在主要阶段结束后盘点全部未解决叶任务。逐项记录继续、过期或已被覆盖及原因/证据或下一步；只记录判断，不改变任务状态或执行历史。基于最新 state_revision 和阶段 attempt；并发变化时重读任务树。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": _session_property(), "task_instance_id": _task_instance_property(),
            "task_id": {"type": "string"}, "attempt_id": {"type": "string"},
            "expected_state_revision": {"type": "integer", "minimum": 0},
            "decisions": {"type": "array", "items": {"type": "object", "properties": {
                "task_id": {"type": "string"}, "decision": {"type": "string", "enum": ["continue", "obsolete", "covered"]},
                "reason": {"type": "string", "minLength": 1}}, "required": ["task_id", "decision", "reason"]}}
        }, "required": ["session_id", "task_id", "attempt_id", "expected_state_revision", "decisions"]},
        "_meta": _data_tool_meta(),
    },
    {
        "name": "reassign_task", "title": "显式调整计划执行者",
        "description": "仅改派 pending 叶任务，保留 planned_assignment 和改派历史。需主会话 reviewer 与具体 reason；不能转移 running/已结束 attempt，结束后改派须显式重规划。声明未经宿主核验。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": _session_property(), "task_id": {"type": "string"},
            "assignment": _assignment_schema(), "reason": {"type": "string", "minLength": 1},
            "reviewer": _assignment_schema(), "task_instance_id": _task_instance_property(),
        }, "required": ["session_id", "task_id", "assignment", "reason", "reviewer"]},
        "_meta": _data_tool_meta(),
    },
    {
        "name": "bind_task_agent", "title": "绑定实际派发返回标识",
        "description": "将 spawn 返回的 agent_name 单次绑定到当前 running subagent 叶 attempt；相同绑定幂等，不同标识/旧 attempt 拒绝。不改变不可变执行配置；回执为调用方填报，非宿主认证。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": _session_property(), "task_id": {"type": "string"},
            "attempt_id": {"type": "string"}, "agent_name": {"type": "string", "minLength": 1},
            "task_instance_id": _task_instance_property(),
        }, "required": ["session_id", "task_id", "attempt_id", "agent_name"]},
        "_meta": _data_tool_meta(),
    },
    {
        "name": "review_task", "title": "主会话接受或退回子任务结果",
        "description": "仅验收当前 pending 的 subagent 叶 submission，需主会话 reviewer、summary 与非空 evidence。accept 保留提交的 resolved/open，reject 为 open；原上报和独立验收分别留存。旧/重复验收拒绝；父/root 使用 synthesis。角色未经宿主认证。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": _session_property(), "task_id": {"type": "string"},
            "attempt_id": {"type": "string"}, "decision": {"type": "string", "enum": ["accept", "reject"]},
            "summary": {"type": "string", "minLength": 1},
            "evidence": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            "reviewer": _assignment_schema(), "task_instance_id": _task_instance_property(),
        }, "required": ["session_id", "task_id", "attempt_id", "decision", "summary", "evidence", "reviewer"]},
        "_meta": _data_tool_meta(),
    },
    {
        "name": "consume_display_intent",
        "title": "消费进度展示意图",
        "description": "仅供 App 消费最新 render 的一次性展示意图；跨挂载去重，现有 fullscreen 租约或待处理请求阻止重复自动打开。只登记请求资格，不证明宿主已创建标签页，不修改任务进度。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": _session_property(),
            "view_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "display_intent": {"type": "string"}},
            "required": ["session_id", "view_id", "display_intent"]},
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "register_ui_view",
        "title": "登记当前进度视图",
        "description": "仅供 App 登记或续租进度视图；每个挂载使用唯一 view_id，注销后不可复用。每次宿主模式切换递增 mode_generation，续租复用当前世代；较旧世代及同世代不同模式会被拒绝，旧版无世代请求不能覆盖已带世代的登记。inline 保留来源高水位与唯一活动 owner，旧结果不能抢占新卡片；fullscreen 续租不提升 inline 来源顺序。不修改任务进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "view_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "view_sequence": {"type": "integer", "minimum": 0, "description": "首次工具结果的 view_sequence，刷新后不可替换"},
                "state_revision": {"type": "integer", "minimum": 0, "description": "首次工具结果的 state_revision"},
                "task_instance_id": _task_instance_property(),
                "display_mode": {"type": "string", "enum": ["inline", "fullscreen"], "description": "当前宿主展示模式"},
                "mode_generation": {"type": "integer", "minimum": 0, "description": "挂载内宿主模式世代；初始0，每次模式切换递增，心跳续租保持相同值。旧版可省略，但不可覆盖已带世代的登记。"},
            },
            "required": ["session_id", "view_id"],
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "check_ui_view",
        "title": "检查当前进度视图",
        "description": "仅供 App 查询当前视图状态；superseded inline 可继续轻量检查，活动 owner 注销后合格旧卡可恢复为 latest。已注销的 view_id 始终返回 superseded。fullscreen 标签页打开时返回 hidden_by_fullscreen。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "view_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "display_mode": {"type": "string", "enum": ["inline", "fullscreen"]},
            },
            "required": ["session_id", "view_id"],
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "unregister_ui_view",
        "title": "释放进度视图登记",
        "description": "仅供 App 在挂载销毁时注销视图；释放 fullscreen 租约并恢复合格的 inline owner。该 view_id 终身失效，迟到登记或续租返回 superseded；新挂载必须使用新 view_id。不修改任务进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "view_id": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            "required": ["session_id", "view_id"],
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "init_progress",
        "title": "开始跟踪任务",
        "description": "旧版线性任务 API。多步骤新任务必须使用 init_task_network；本工具默认拒绝两个及以上步骤。只有用户明确要求线性步骤或恢复旧版线性任务时，才设置 legacy_linear=true。此工具只写入任务数据，不触发展示；首次展示调用一次 render_progress，宿主优先以 fullscreen 展示。复用同一 Codex 会话的 session_id 会把旧任务归档到 task_history。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "任务步骤描述列表",
                },
                "task_title": {"type": "string", "description": "任务标题"},
                "new_task_reason": {"type": "string", "description": "用户明确开启新目标时，说明旧未完成任务如何处理；运行或待验收结果必须先收尾。"},
                "legacy_linear": {
                    "type": "boolean",
                    "default": False,
                    "description": "仅用于用户明确要求线性步骤或恢复旧版线性任务；默认 false，多个步骤将要求使用 HTN。",
                },
                "session_id": {
                    **_session_property(),
                    "description": "可选；同一会话创建新任务时复用，旧任务保留到 task_history。省略时生成新的 UUID。恢复当前任务请用 get_progress。",
                },
            },
            "required": ["steps"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "init_task_network",
        "title": "初始化任务网络",
        "description": "新任务的默认入口。多阶段任务一次传入至少两个粗粒度 tasks，或初始化后调用 decompose_task；未规划的空 root 不可执行。确为单步工作时填写 atomic_reason。可选 request_id 使用持久化账本安全重试：首次省略 session 也复用已分配身份，同实例重规划后返回当前计划；绑定任务已归档或清除时明确拒绝，不回滚当前任务。仅返回 task_instance_id、root_id、planning_status 等身份与状态小回执。此工具只写任务数据，不展示；主会话完成首次粗规划后调用一次 render_progress 自动请求展示。新任务复用本 Codex 会话的 session_id，恢复用 get_progress，重规划用 rebuild_task_network。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_title": {"type": "string", "description": "根任务标题"},
                "main_session": {
                    "type": "object",
                    "description": "主会话设置快照。model/reasoning_effort 可单独省略；未知时不要猜测，任务仍可初始化。当前插件只收到调用方入参，卡片会标明来源未由 Codex 宿主核验。",
                    "properties": {
                        "model": {"type": "string", "description": "仅填当前会话明确暴露的模型标识；未知时省略"},
                        "reasoning_effort": {"type": "string", "enum": list(REASONING_EFFORTS), "description": "仅填当前会话明确暴露的思维强度；未知时省略"},
                        "codex_thread_id": {"type": "string", "description": "可选，仅在 Codex 宿主明确提供时填写；当前插件不会验证"},
                        "codex_turn_id": {"type": "string", "description": "可选，仅在 Codex 宿主明确提供时填写；当前插件不会验证"},
                    },
                },
                "session_id": {
                    **_session_property(),
                    "description": "可选；同一会话创建新任务时复用，旧任务保留到 task_history。省略时生成新的 UUID。恢复当前任务请用 get_progress。",
                },
                "tasks": {"type": "array", "items": _task_schema(), "description": "初始粗粒度计划；多阶段任务至少两个有独立结果边界的阶段，每项必须包含 assignment.execution_mode。"},
                "atomic_reason": {"type": "string", "description": "明确说明为何这是单步任务；与 tasks 互斥。"},
                "new_task_reason": {"type": "string", "description": "用户明确开启新目标时，说明旧未完成任务如何处理；继续原目标应追加计划或重规划。"},
                "request_id": {"type": "string", "description": "可选稳定幂等键；重试同一初始化内容时复用。已知session按session隔离；省略session时键须全局唯一（建议UUID），并可随后带返回的session重试。哈希与身份墓碑保留至缓存整体删除，清除任务后旧键仍拒绝重建。"},
            },
        "required": ["task_title"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "decompose_task",
        "title": "分解任务",
        "description": "分解或追加子任务，继承祖先依赖并检查执行死锁。追加问题填写 addition_reason，并用 precedes 原子指定尚未开始的已有后继；后继将真实依赖新增任务，界面按依赖排序。已经开始的后继须先协调并重规划，不能改写历史。每个阶段结束后主会话盘点未闭环任务是否继续、过期或被覆盖，记录理由与证据。拆分应形成至少两个独立结果；已有分组可追加单项。每个子任务必须提供 assignment.execution_mode。分解运行中的任务前须协调停止旧执行者并传当前 attempt_id。会话存在 task_history 时必须传当前 task_instance_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "task_instance_id": _task_instance_property(),
                "task_id": {"type": "string", "description": "待分解的父任务 ID"},
                "attempt_id": {"type": "string", "description": "分解运行中任务时必须提供当前尝试 ID，先协调停止旧执行者"},
                "tasks": {"type": "array", "items": _task_schema(), "description": "该父任务的子任务列表"},
            },
            "required": ["session_id", "task_id", "tasks"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "update_task",
        "title": "更新任务节点",
        "description": "开始/执行更新须匹配 planned_assignment.execution_mode；改派用 reassign_task。主会话叶执行记录 self_execution_reason；复杂工作默认委派。子智能体启动记录完整派发配置，spawn后 bind_task_agent；finished 仅生成 submission/review_status=pending，默认与 finished 依赖均不放行，必须主会话 review_task。父/root synthesis 必须 main_session+非空summary，resolved 还需 evidence 和全部子结果resolved。运行中用当前attempt_id，禁止改快照；重试使后继/祖先旧验收失效并保留历史，有运行中或待验收消费者时拒绝。超过10分钟先复核拆分；等待不得记完成。所有角色和元数据均为调用方填报、未经宿主认证。旧in-flight允许恢复上报但子结果仍须review。会话切换或清空后须task_instance_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "task_instance_id": _task_instance_property(),
                "task_id": {"type": "string", "description": "当前 epoch 中的任务 ID"},
                "execution_status": {
                    "type": "string",
                    "enum": ["running", "finished", "failed", "cancelled"],
                    "description": "pending 是初始状态，不可通过更新工具设置",
                },
                "outcome": {"type": "string", "enum": list(OUTCOMES), "description": "finished 时必填，区分已解决与仍开放"},
                "summary": {"type": "string"},
                "failure_reason": {"type": "string", "description": "叶任务 failed 或 finished/open 时记录明确的失败原因；连续两次未解决后重试会提供最近原因和摘要，提示 agent 判断是否相关。"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "attempt_id": {"type": "string", "description": "running 任务返回的当前尝试 ID"},
                "retry": {"type": "boolean", "description": "从终态重新进入 running 时设为 true"},
                "assignment": _assignment_schema(),
            },
            "required": ["session_id", "task_id", "execution_status"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "get_ready_tasks",
        "title": "读取就绪任务",
        "description": "读取当前 epoch 中自身及祖先依赖均已满足的 ready 叶任务；dependencies 包含直接与继承的前驱结果、放行策略和 inherited_from。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "rebuild_task_network",
        "title": "重建任务网络",
        "description": "将当前计划快照保留到 history 并增加 epoch，建立新根任务以表达同一任务的重规划；保持 task_instance_id 和 task_history 不变，禁止存在运行中的尝试。会话存在 task_history 时必须传当前 task_instance_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "task_instance_id": _task_instance_property(),
                "reason": {"type": "string", "description": "重规划原因，写入计划轮次 history"},
                "task_title": {"type": "string", "description": "可选的新根任务标题"},
            },
            "required": ["session_id", "reason"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "update_step",
        "title": "更新任务步骤",
        "description": "更新旧版线性步骤。pending 须先 in_progress 才可 completed；每次启动生成新的 attempt_id，首次启动须提供 execution_mode；随后运行更新、完成、重置和重开都须传当前 attempt_id。重置把运行尝试记为 cancelled，保留 attempts 历史；重新启动取得新ID，旧ID不能完成新尝试。旧存储无ID的现有尝试仅在首次收尾/重置前兼容省略；以后启动均升级到严格ID校验。主会话的 model/reasoning_effort 可未知并省略；子智能体须提供派发配置。入参为调用方填报、未经宿主核验。会话存在 task_history 或清空过进度时须传当前 task_instance_id。普通更新只写数据，首次展示调用 render_progress。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "task_instance_id": _task_instance_property(),
                "attempt_id": {"type": "string", "description": "当前步骤返回的执行尝试ID；启动pending步骤可省略，其后的运行更新、完成、重置和重开必填，旧尝试ID始终拒绝。"},
                "step_index": {"type": "integer", "description": "步骤索引（从 0 开始）"},
                "status": {"type": "string", "enum": list(VALID_STATUSES)},
                "note": {"type": "string", "description": "可选备注"},
                "assignment": _assignment_schema(),
            },
            "required": ["session_id", "step_index", "status"],
        },
        "_meta": _data_tool_meta(),
    },
    {
        "name": "get_progress",
        "title": "按需读取任务进度",
        "description": "显式读取目标实例。默认overview仅概览；优先tree按层分页、tasks读取指定当前节点、records读取指定节点执行记录、context读取上下文及分页请求、history分页历史；仅明确需要时full。full与兼容include_history=true返回目标实例完整记录/历史，peer仅当前快照，其历史须指定peer实例另读。分页用expected_state_revision固定版本，版本变化须重读第一页。此工具不展示面板。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(), "task_instance_id": _task_instance_property(),
                "view": {"type": "string", "enum": list(_QUERY_VIEWS), "default": "overview"},
                "include_history": {"type": "boolean", "default": False, "description": "兼容显式full读取；不可与其他view混用。"},
                "task_id": {"type": "string", "description": "tree子树根或records节点ID；旧线性records用步骤索引字符串。"},
                "task_ids": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string"}, "description": "tasks视图的节点ID；旧线性任务用步骤索引字符串。"},
                "depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 1},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "record_kind": {"type": "string", "enum": list(_RECORD_KINDS), "default": "attempts"},
                "history_kind": {"type": "string", "enum": list(_HISTORY_KINDS), "default": "history"},
                "expected_state_revision": {"type": "integer", "minimum": 0},
            }, "required": ["session_id"],
        }, "_meta": _data_tool_meta(),
    },
    {
        "name": "get_progress_view",
        "title": "读取进度面板数据",
        "description": "仅供App主动读取当前完整面板快照；默认省略追加执行记录，include_history=true加载目标实例完整记录和历史用于展开/导出。并行实例历史分别读取。",
        "inputSchema": {"type": "object", "properties": {
            "session_id": _session_property(), "task_instance_id": _task_instance_property(),
            "include_history": {"type": "boolean", "default": False}}, "required": ["session_id"]},
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "render_progress",
        "title": "展示任务进度",
        "description": "主会话首次粗规划后调用一次，或用户明确重开时调用；仅返回bootstrap身份与一次性展示意图。资源优先fullscreen；App按宿主能力自动请求并独立读取数据。已有标签页复用租约，关闭/拒绝后不自动重试；实际展示由宿主决定。不要用于普通更新、查询、恢复或后台轮询。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _display_tool_meta(),
    },
    {
        "name": "save_export_files",
        "title": "保存任务导出文件",
        "description": "仅供 Task Progress App 将已生成的 Excel 与可视化 HTML 保存到本机 Downloads/Task Progress Exports；只接受同名的一对 .xlsx 与 .html 文件，不接受路径参数或其他文件类型。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "mime_type": {"type": "string"},
                            "encoding": {"type": "string", "enum": ["base64", "utf-8"]},
                            "content": {"type": "string"},
                        },
                        "required": ["name", "mime_type", "encoding", "content"],
                    },
                    "description": "Excel 使用 Base64；HTML 使用 UTF-8 文本。",
                },
            },
            "required": ["files"],
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "clear_progress",
        "title": "清除本会话进度",
        "description": "仅用于清理用户明确确认已归档的 Codex 会话中的全部任务内容。调用前必须按 cleanup-task-progress-history skill 核对归档依据、对应 session_id、保留期限与删除范围；本工具不会读取或验证 Codex 归档状态，不得用于活动会话或归档状态不明的会话。清除指定 session_id 的当前任务、task_history 历史任务和视图登记，不影响其他会话。只保留排序与请求哈希/身份墓碑，防止旧初始化或执行请求重建已清除任务；不保留任务内容。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _data_tool_meta(),
    },
]


_REQUEST_PROPERTIES = {
    "session_id": _session_property(), "task_instance_id": _task_instance_property(),
    "request_id": {"type": "string", "minLength": 1, "maxLength": 256},
    "reason": {"type": "string", "minLength": 1, "maxLength": 12000},
    "task_title": {"type": "string", "minLength": 1, "maxLength": 12000},
    "constraints": {"type": "array", "maxItems": 64, "items": {"type": "string", "minLength": 1, "maxLength": 12000}},
    "acceptance_criteria": {"type": "array", "minItems": 1, "maxItems": 64, "items": {"type": "string", "minLength": 1, "maxLength": 12000}},
    "context_brief": {"type": "object", "description": "调用方提供的显式上下文，不能从摘要补造未记录事实。"},
}
_CLOSURE_PROPERTIES = {
    "session_id": _session_property(), "task_instance_id": _task_instance_property(),
    "source_epoch": {"type": "integer", "minimum": 0},
    "source_fingerprint": {"type": "string", "description": "查询所得closure_review_binding.source_fingerprint；来源变化则拒绝迟到决定。"},
    "summary": {"type": "string", "minLength": 1},
    "evidence": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
    "reviewer": _assignment_schema(),
}
_HANDOFF_MAPPING = {
    "source_task_id": {"type": "string"}, "source_attempt_id": {"type": ["string", "null"]},
    "target_task_instance_id": {"type": "string"}, "target_epoch": {"type": "integer", "minimum": 0},
    "target_task_id": {"type": "string"}, "target_attempt_id": {"type": "string", "minLength": 1},
}
for _name, _title, _description, _properties, _required in [
    ("handoff_request", "登记并行请求移交验收", "以foreground身份，将已如实收尾的parallel全部未解决叶固定绑定到本会话承接叶instance/epoch/task/有效attempt。允许running/待复核承接；后续写入仅在其真实resolved且已验收后自动闭环。支持历史实例/计划补录；保留来源open/cancelled和冻结证据，拒绝漏项、自指、环、旧attempt。handoff_id幂等；身份由调用方声明。",
     {**_CLOSURE_PROPERTIES, "request_id": {"type": "string"}, "handoff_id": {"type": "string"}, "source_task_instance_id": {"type": "string"},
      "mappings": {"type": "array", "minItems": 1, "maxItems": 200, "items": {"type": "object", "properties": _HANDOFF_MAPPING, "required": list(_HANDOFF_MAPPING), "additionalProperties": False}}},
     [*_CLOSURE_PROPERTIES, "request_id", "handoff_id", "source_task_instance_id", "mappings"]),
    ("review_task_closure", "验收父级收尾或决定继续", "全部子项结束且无pending submission后，主会话追加独立收尾验收。continue明确继续retry/重规划；accept允许收尾归档但不改原节点outcome或解除resolved依赖。必须使用查询所得source_epoch/fingerprint，review_id幂等；保留历史与原始取消。",
     {**_CLOSURE_PROPERTIES, "task_id": {"type": "string"}, "review_id": {"type": "string"}, "decision": {"type": "string", "enum": ["continue", "accept"]}},
     [*_CLOSURE_PROPERTIES, "task_id", "review_id", "decision"]),
    ("route_request", "登记排队或并行独立请求", "显式主会话实例登记新请求，request_id 幂等。queue 不执行；parallel 创建隔离任务实例，叶执行限 subagent 并由主会话验收。必须说明模式理由；不自动派发、不扩大授权，声明不构成宿主认证。",
     {**_REQUEST_PROPERTIES, "mode": {"type": "string", "enum": ["queue", "parallel"]}, "request_text": {"type": "string", "minLength": 1, "maxLength": 12000}},
     ["session_id", "task_instance_id", "request_id", "request_text", "task_title", "mode", "reason", "acceptance_criteria"]),
    ("activate_request", "显式激活队首请求", "仅原主任务完成或如实收尾后，显式将FIFO队首queued请求设为主任务。保留所有并行实例/请求及原任务历史；不会自动启动执行。",
     {key: _REQUEST_PROPERTIES[key] for key in ("session_id", "task_instance_id", "request_id")},
     ["session_id", "task_instance_id", "request_id"]),
    ("update_request", "调整未启动的排队请求", "主会话以reason调整queued目标、约束和验收条件；保留请求原文及修订。当前实例用update_task_context或rebuild_task_network。",
     _REQUEST_PROPERTIES, ["session_id", "task_instance_id", "request_id", "reason"]),
    ("cancel_request", "取消尚未启动请求", "有理由取消queued或尚无attempt的parallel请求；不停止或假取消运行中的智能体/进程。已启动请求先协调执行者并如实收尾。",
     {key: _REQUEST_PROPERTIES[key] for key in ("session_id", "task_instance_id", "request_id", "reason")},
     ["session_id", "task_instance_id", "request_id", "reason"]),
    ("update_task_context", "保存当前任务上下文与恢复位置", "更新指定主任务或并行实例的目标、约束、验收和恢复位置，记录reason与旧版本；重规划保留上下文和既有证据。只记录明确提供的信息，不补造用户原文。",
     {key: _REQUEST_PROPERTIES[key] for key in ("session_id", "task_instance_id", "constraints", "acceptance_criteria", "reason")} | {
         "goal": {"type": "string", "minLength": 1, "maxLength": 12000},
         "request_text": {"type": "string", "minLength": 1, "maxLength": 12000,
                          "description": "可选，首次显式补录用户原文；已记录原文不能覆盖。"},
         "resume_note": {"type": "string", "minLength": 1, "maxLength": 12000}},
     ["session_id", "task_instance_id", "goal", "constraints", "acceptance_criteria", "resume_note", "reason"]),
]:
    TOOLS.append({"name": _name, "title": _title, "description": _description,
        "inputSchema": {"type": "object", "properties": _properties, "required": _required}, "_meta": _data_tool_meta()})

for _tool in TOOLS:
    if _tool["name"] in _MUTATION_TOOLS:
        _tool["description"] += " 仅返回身份与状态回执；详情按需 get_progress。"
    if _tool["name"] in ("get_progress", "get_ready_tasks"):
        _tool["inputSchema"]["properties"]["task_instance_id"] = _task_instance_property()
        _tool["description"] += " 可选task_instance_id读取隔离并行实例；省略读取当前主任务。"
        if _tool["name"] == "get_ready_tasks":
            _tool["description"] += " 仅返回分页ready_tasks；dependencies保留依赖ID、状态、dependency_policy和inherited_from，完整summary/evidence在dependency_evidence[依赖ID]去重返回。"
            _tool["inputSchema"]["properties"].update(offset={"type": "integer", "minimum": 0, "default": 0}, limit={"type": "integer", "minimum": 1, "maximum": 200, "default": 50})


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "handoff_request": lambda args: handoff_request(**args),
    "review_task_closure": lambda args: review_task_closure(**args),
    "route_request": lambda args: route_request(**args),
    "activate_request": lambda args: activate_request(**args),
    "update_request": lambda args: update_request(**args),
    "cancel_request": lambda args: cancel_request(**args),
    "update_task_context": lambda args: update_task_context(**args),
    "reassign_task": lambda args: reassign_task(
        args.get("session_id"), args.get("task_id"), args.get("assignment"), args.get("reason"),
        args.get("reviewer"), args.get("task_instance_id"),
    ),
    "bind_task_agent": lambda args: bind_task_agent(
        args.get("session_id"), args.get("task_id"), args.get("attempt_id"), args.get("agent_name"),
        args.get("task_instance_id"),
    ),
    "review_task": lambda args: review_task(
        args.get("session_id"), args.get("task_id"), args.get("attempt_id"), args.get("decision"),
        args.get("summary"), args.get("evidence"), args.get("reviewer"), args.get("task_instance_id"),
    ),
    "review_stage_inventory": lambda args: review_stage_inventory(
        args.get("session_id"), args.get("task_id"), args.get("attempt_id"), args.get("decisions"),
        args.get("expected_state_revision"), args.get("task_instance_id"),
    ),
    "consume_display_intent": lambda args: consume_display_intent(args.get("session_id"), args.get("view_id"), args.get("display_intent")),
    "register_ui_view": lambda args: register_ui_view(
        args.get("session_id"), args.get("view_id"), args.get("view_sequence"),
        args.get("state_revision"), args.get("task_instance_id"), args.get("display_mode", "inline"),
        args.get("mode_generation"),
    ),
    "check_ui_view": lambda args: check_ui_view(
        args.get("session_id"), args.get("view_id"), args.get("display_mode", "inline")
    ),
    "unregister_ui_view": lambda args: unregister_ui_view(args.get("session_id"), args.get("view_id")),
    "init_progress": lambda args: init_progress(
        args.get("steps", []), args.get("task_title", ""), args.get("session_id"),
        args.get("legacy_linear", False), args.get("new_task_reason"),
    ),
    "init_task_network": lambda args: init_task_network(
        args.get("task_title", ""), args.get("session_id"), args.get("main_session"),
        args.get("tasks"), args.get("atomic_reason"), args.get("request_id"), args.get("new_task_reason")
    ),
    "decompose_task": lambda args: decompose_task(
        args.get("session_id"), args.get("task_id"), args.get("tasks", []),
        args.get("task_instance_id"), args.get("attempt_id"),
    ),
    "update_task": lambda args: update_task(
        args.get("session_id"),
        args.get("task_id"),
        args.get("execution_status"),
        args.get("outcome"),
        args.get("summary", ""),
        args.get("evidence", []),
        args.get("attempt_id"),
        args.get("retry", False),
        args.get("assignment"),
        args.get("task_instance_id"),
        args.get("failure_reason"),
    ),
    "get_ready_tasks": lambda args: get_ready_tasks(args.get("session_id"), args.get("task_instance_id")),
    "rebuild_task_network": lambda args: rebuild_task_network(
        args.get("session_id"), args.get("reason", ""), args.get("task_title", ""),
        args.get("task_instance_id"),
    ),
    "update_step": lambda args: update_step(
        args.get("session_id"),
        args.get("step_index"),
        args.get("status"),
        args.get("note", ""),
        args.get("assignment"),
        args.get("task_instance_id"),
        args.get("attempt_id"),
    ),
    "get_progress": lambda args: get_progress(**args),
    "get_progress_view": lambda args: get_progress_view(**args),
    "render_progress": lambda args: render_progress(args.get("session_id")),
    "save_export_files": lambda args: save_export_files(args.get("files")),
    "clear_progress": lambda args: clear_progress(args.get("session_id")),
}


def _ok(message_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result})


def _err(message_id: Any, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}
    )


def _plugin_version() -> str:
    version = json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError("插件 manifest 缺少有效 version。")
    return version


def _resource_html() -> str:
    return APP_HTML_PATH.read_text(encoding="utf-8").replace("__TASK_PROGRESS_VERSION__", _plugin_version())


def handle(message: Any) -> str | None:
    if not isinstance(message, dict):
        return _err(None, -32600, "JSON-RPC 请求必须是对象。")
    message_id = message.get("id")
    if (message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
            or isinstance(message_id, bool) or not isinstance(message_id, (str, int, type(None)))):
        return _err(None, -32600, "无效的 JSON-RPC 请求。")
    if "id" not in message:
        return None
    method = message.get("method", "")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _err(message_id, -32602, "params 必须是对象。")

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        protocol_version = (
            requested_version
            if isinstance(requested_version, str)
            and requested_version in HANDSHAKE_PROTOCOL_VERSIONS
            else "2025-11-25"
        )
        return _ok(
            message_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {
                    "tools": {},
                    "resources": {"listChanged": False, "subscribe": False},
                    "extensions": {
                        "io.modelcontextprotocol/ui": {"mimeTypes": [RESOURCE_MIME]}
                    },
                },
                "serverInfo": {"name": "task-progress", "version": _plugin_version()},
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _ok(message_id, {"tools": TOOLS})

    if method == "resources/list":
        return _ok(
            message_id,
            {
                "resources": [
                    {
                        "uri": RESOURCE_URI,
                        "name": "task_progress_inspector",
                        "title": "Task Progress 面板",
                        "description": "显示当前会话的任务网络、层级、依赖、就绪状态、结果证据和历史 epoch；兼容旧版扁平步骤。",
                        "mimeType": RESOURCE_MIME,
                        "_meta": {"ui": {"prefersBorder": True}},
                    },
                    *[
                        {
                            "uri": uri,
                            "name": "task_progress_inspector_legacy",
                            "title": "Task Progress 面板（兼容旧会话）",
                            "description": "兼容历史工具结果中的旧 UI 资源地址；内容使用当前实现。",
                            "mimeType": RESOURCE_MIME,
                            "_meta": {"ui": {"prefersBorder": True}},
                        }
                        for uri in LEGACY_RESOURCE_URIS
                    ],
                ]
            },
        )

    if method == "resources/read":
        uri = params.get("uri")
        if uri != RESOURCE_URI and uri not in LEGACY_RESOURCE_URIS:
            return _err(message_id, -32602, f"未知资源 URI：{uri}")
        try:
            resource_uri = uri
            return _ok(
                message_id,
                {
                    "contents": [
                        {
                            "uri": resource_uri,
                            "mimeType": RESOURCE_MIME,
                            "_meta": {
                                "ui": {"prefersBorder": True},
                                "openai/ui": {
                                    "availableDisplayModes": ["inline", "fullscreen"],
                                    "preferredDisplayMode": "fullscreen" if uri == RESOURCE_URI else "inline",
                                },
                            },
                            "text": _resource_html(),
                        }
                    ]
                },
            )
        except OSError as exc:
            return _err(message_id, -32603, f"无法读取 Task Progress 面板：{exc}")

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(args, dict):
            return _err(message_id, -32602, "工具名称必须是字符串，arguments 必须是对象。")
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _err(message_id, -32601, f"未知工具：{name}")
        try:
            return _ok(message_id, _wire_result(name, args, handler(args)))
        except ProgressError as exc:
            return _ok(
                message_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )
        except Exception as exc:  # noqa: BLE001
            return _err(message_id, -32603, f"工具执行出错：{exc}")

    if method == "ping":
        return _ok(message_id, {})

    if message_id is not None:
        return _err(message_id, -32601, f"未知方法：{method}")
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _err(None, -32700, "无法解析 JSON-RPC 请求。")
        else:
            try:
                response = handle(message)
            except Exception as exc:  # Keep serving after an isolated request failure.
                message_id = message.get("id") if isinstance(message, dict) else None
                response = _err(message_id, -32603, f"请求执行出错：{exc}")
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
