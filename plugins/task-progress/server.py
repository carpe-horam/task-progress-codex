#!/usr/bin/env python3
"""Task Progress MCP server with session-scoped storage and an MCP App view."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sys
import threading
import uuid
from contextlib import contextmanager
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

RESOURCE_URI = "ui://task-progress/progress-inspector/v2"
RESOURCE_MIME = "text/html;profile=mcp-app"
APP_HTML_PATH = Path(__file__).with_name("app.html")

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
_SESSION_LOCKS: dict[str, threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


class ProgressError(ValueError):
    """An expected progress operation error that should be shown to the caller."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def _write(session_id: str, data: dict[str, Any]) -> None:
    path = _session_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    data["state_revision"] = _advance_order(session_id, "state_revision")
    _atomic_json(path, data)


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
            and not order.get("require_task_instance_id", False)):
        return
    if task_instance_id != _task_instance_id(data):
        raise ProgressError(
            "task_instance_id 缺失或不匹配；旧任务的迟到更新已拒绝。"
            "请先 get_progress 读取当前任务的 task_instance_id。"
        )


def _archive_current_task(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if data is None:
        return []
    # Exclude task history before building the snapshot, keeping archives flat.
    current = {key: value for key, value in data.items() if key != "task_history"}
    snapshot = _progress_payload(current)
    snapshot.pop("task_history", None)
    snapshot["archived_at"] = _now()
    return [*data.get("task_history", []), deepcopy(snapshot)]


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
    return {
        "schema_version": 2,
        "session_id": session_id,
        "task_instance_id": str(uuid.uuid4()),
        "task_history": [],
        "task_title": task_title,
        "main_session": main_session,
        "epoch": epoch,
        "revision": 0,
        "root_id": root_id,
        "nodes": {root_id: _new_node(root_id, task_title, None)},
        "history": [],
        "created_at": now,
        "updated_at": now,
    }


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
    string_fields = ("tool_identifier", "tool_name", "dispatch_text", "agent_name", "model", "batch", "codex_thread_id", "codex_turn_id")
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


def _require_execution_metadata(assignment: dict[str, Any] | None) -> None:
    if assignment is None or not assignment.get("execution_mode"):
        raise ProgressError("启动任务必须提供 assignment.execution_mode。")
    if assignment.get("execution_mode") == "main_session":
        # A missing host-readable setting is better represented as unknown than
        # guessed from config or model self-report; it must not block the work.
        return
    missing = [key for key in ("model", "reasoning_effort") if not assignment.get(key)]
    if missing:
        raise ProgressError(f"启动子智能体任务时 assignment 必须包含派发配置：{', '.join(missing)}。")


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
        node["depends_on"] = list(dependencies)
        node["dependency_policy"] = policy
        node["profile"] = profile
        node["assignment"] = _normalize_assignment(item.get("assignment"))
        new_nodes.append((node_id, node))
    for node_id, node in new_nodes:
        nodes[node_id] = node
    try:
        _validate_network(data)
    except ProgressError:
        for node_id, _ in new_nodes:
            nodes.pop(node_id, None)
        _validate_network(data)
        raise


def _node_is_terminal(node: dict[str, Any]) -> bool:
    status = node.get("execution_status")
    return status in TERMINAL_EXECUTION_STATUSES and (
        status != "finished" or node.get("outcome") in OUTCOMES
    )


def _dependency_satisfied(data: dict[str, Any], node: dict[str, Any], dependency_id: str) -> bool:
    dependency = data["nodes"][dependency_id]
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
    return (
        not node.get("children")
        and node.get("execution_status") == "pending"
        and _dependencies_satisfied(data, node)
    )


def _display_node_status(data: dict[str, Any], node: dict[str, Any]) -> str:
    if node.get("execution_status") == "finished" and node.get("outcome"):
        return node["outcome"]
    status = node.get("execution_status")
    if status == "running":
        return "executing"
    if status in ("failed", "cancelled"):
        return status
    if node.get("children"):
        child_statuses = [_display_node_status(data, data["nodes"][child_id]) for child_id in node["children"]]
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
    terminal_leaves = [node for node in leaves if _node_is_terminal(node)]
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
    if root.get("outcome") == "resolved":
        overall_status = "completed"
    elif root.get("outcome") == "open":
        overall_status = "open"
    elif root.get("execution_status") == "failed":
        overall_status = "failed"
    elif root.get("execution_status") == "cancelled":
        overall_status = "cancelled"
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
        "split_review_due": split_review_due,
        "blocked_count": len(blocked_leaves),
        "epoch": data.get("epoch", 0),
        "revision": data.get("revision", 0),
        "root_id": data["root_id"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "nodes": enriched_nodes,
        "ready_tasks": [node["id"] for node in ready_nodes],
        "history": data.get("history", []),
    }


def init_task_network(
    task_title: str,
    session_id: str | None = None,
    main_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id, generate=True)
    if not isinstance(task_title, str) or not task_title.strip():
        raise ProgressError("task_title 必须是非空字符串。")
    with _session_lock(sid):
        previous = _read(sid)
        data = _new_network(
            sid,
            task_title.strip(),
            main_session=_normalize_main_session(main_session),
        )
        data["task_history"] = _archive_current_task(previous)
        _write(sid, data)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


def decompose_task(
    session_id: str,
    task_id: str,
    tasks: list[dict[str, Any]],
    task_instance_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data = _read(sid)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        _validate_task_instance(data, task_instance_id)
        parent = data["nodes"].get(task_id)
        if parent is not None:
            if parent["execution_status"] == "running" and attempt_id != parent.get("attempt_id"):
                raise ProgressError("分解运行中的任务必须提供当前 attempt_id；请先协调停止旧执行者。")
            if attempt_id is not None and attempt_id != parent.get("attempt_id"):
                raise ProgressError("attempt_id 不匹配，不能用旧尝试分解当前任务。")
        _add_tasks(data, task_id, tasks)
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
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


def _begin_attempt(node: dict[str, Any], assignment: dict[str, Any] | None, *, kind: str = "execution") -> None:
    _require_execution_metadata(assignment)
    assignment = _stamp_assignment(assignment or {}, scope="attempt_start")
    node.pop("invalidated_by", None)
    node["attempt"] = int(node.get("attempt", 0)) + 1
    node["attempt_id"] = str(uuid.uuid4())
    node["assignment"] = dict(assignment)
    node["started_at"] = _now()
    node["finished_at"] = None
    node["outcome"] = None
    node["summary"] = ""
    node["evidence"] = []
    node.setdefault("attempts", []).append({
        "attempt": node["attempt"], "attempt_id": node["attempt_id"], "kind": kind,
        "execution_status": "running", "outcome": None, "summary": "", "evidence": [],
        "started_at": node["started_at"], "assignment": dict(node["assignment"]),
    })


def _record_attempt_result(node: dict[str, Any]) -> None:
    if node.get("attempts") and node.get("attempt_id"):
        node["attempts"][-1].update({key: deepcopy(node.get(key)) for key in (
            "execution_status", "outcome", "summary", "evidence", "finished_at"
        )})


def _reset_execution(node: dict[str, Any]) -> None:
    node.update(execution_status="pending", outcome=None, summary="", evidence=[],
                assignment={}, attempt_id=None, started_at=None, finished_at=None)


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
                "attempt_id", "execution_status", "outcome", "assignment", "summary", "evidence", "started_at", "finished_at"
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
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ProgressError("evidence 必须是字符串列表。")
    normalized_assignment = _normalize_assignment(assignment) if assignment is not None else None
    with _session_lock(sid):
        data = _read(sid)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        _validate_task_instance(data, task_instance_id)
        node = data["nodes"].get(node_id)
        if node is None:
            raise ProgressError(f"当前 epoch 中不存在任务 {node_id}；旧 epoch 的结果不会覆盖新计划。")
        previous_status = node["execution_status"]
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
            if previous_status in TERMINAL_EXECUTION_STATUSES and not retry:
                raise ProgressError("已结束任务重试时必须设置 retry=true，且会创建新的 attempt_id。")
            if previous_status != "running" and not _dependencies_satisfied(data, node):
                raise ProgressError("该任务尚未 ready；请先完成依赖，或显式设置 dependency_policy=finished。")
            if previous_status != "running":
                _require_execution_metadata(normalized_assignment)
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
                and outcome == "resolved" and not node.get("children")):
            raise ProgressError("叶任务必须先进入 running 并创建执行尝试，才能标记为 finished/resolved。")
        if execution_status in TERMINAL_EXECUTION_STATUSES and node.get("children"):
            if not all(_node_is_terminal(data["nodes"][child_id]) for child_id in node["children"]):
                raise ProgressError("父任务必须等所有直接子任务结束后再综合验收。")
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
        if summary:
            node["summary"] = summary
        if evidence:
            node["evidence"] = evidence
        if execution_status in TERMINAL_EXECUTION_STATUSES:
            node["finished_at"] = _now()
        _record_attempt_result(node)
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


def get_ready_tasks(session_id: str) -> dict[str, Any]:
    result = get_progress(session_id)
    structured = result.get("structuredContent", {})
    progress = structured.get("progress")
    if not isinstance(progress, dict) or progress.get("schema_version") != 2:
        raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
    ready = [node for node in progress["nodes"] if node.get("ready")]
    lines = [f"当前 ready 叶任务：{len(ready)} 个"]
    for node in ready:
        lines.append(f"- [{node['id']}] {node['title']}（依赖策略：{node['dependency_policy']}）")
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
        data = _read(sid)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        _validate_task_instance(data, task_instance_id)
        if any(node.get("execution_status") == "running" for node in data["nodes"].values()):
            raise ProgressError("仍有任务处于 running，核实或结束这些尝试后再重建网络。")
        data["history"].append({
            "epoch": data["epoch"],
            "root_id": data["root_id"],
            "task_title": data["task_title"],
            "reason": reason.strip(),
            "closed_at": _now(),
            "nodes": data["nodes"],
        })
        next_epoch = int(data["epoch"]) + 1
        new_title = task_title.strip() if isinstance(task_title, str) and task_title.strip() else data["task_title"]
        replacement = _new_network(sid, new_title, epoch=next_epoch,
                                   reserved_ids=set(data["nodes"]) | _historical_ids(data))
        data.update({key: replacement[key] for key in ("epoch", "root_id", "nodes", "task_title")})
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


def _format_detail(progress: dict[str, Any] | None) -> str:
    if progress is None:
        return "当前会话没有活跃的进度跟踪。"
    if progress.get("schema_version") == 2:
        lines = [
            f"📋 目标：{progress['task_title']}（计划第 {progress['epoch'] + 1} 轮）",
            f"📊 叶任务结束：{progress['completed']}/{progress['total']}（{progress['percent']}%），已解决 {progress['resolved_leaf_count']}，未解决 {progress['open_leaf_count']}，待执行 {progress['ready_count']}",
            f"状态：{progress['status']}",
        ]
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
        by_id = {node["id"]: node for node in progress["nodes"]}

        def visit(node_id: str, depth: int = 0) -> None:
            node = by_id[node_id]
            prefix = "  " * depth
            lines.append(f"{prefix}- [{node['id']}] {node['title']} — {node['status']}")
            if node.get("depends_on"):
                lines.append(f"{prefix}  依赖：{', '.join(node['depends_on'])}（{node['dependency_policy']}）")
            assignment = node.get("assignment", {})
            if assignment:
                execution_mode = assignment.get("execution_mode")
                if execution_mode == "main_session":
                    executor = "主会话"
                elif execution_mode == "subagent":
                    executor = "子智能体"
                elif assignment.get("agent_name") or assignment.get("tool_identifier") or assignment.get("tool_name"):
                    executor = "子智能体"
                else:
                    executor = "执行者未记录"
                provenance = assignment.get("metadata_provenance", {})
                verification = "调用方填报，未经 Codex 宿主核验" if provenance.get("verification") == "not_verified_by_codex_host" else "来源未标注"
                lines.append(f"{prefix}  执行：{executor}；model={assignment.get('model') or '未记录'}；reasoning={assignment.get('reasoning_effort') or '未记录'}；{verification}")
            if node.get("summary"):
                lines.append(f"{prefix}  结果：{node['summary']}")
            for evidence in node.get("evidence", []):
                lines.append(f"{prefix}  证据：{evidence}")
            for child_id in node.get("children", []):
                visit(child_id, depth + 1)

        visit(progress["root_id"])
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
    for step in progress["steps"]:
        icon = STATUS_ICONS.get(step["status"], "⬜")
        note = f" — {step['note']}" if step.get("note") else ""
        lines.append(f"  {icon} [{step['index']}] {step['description']}{note}")
    return "\n".join(lines)


def _tool_result(
    text: str, progress: dict[str, Any] | None, session_id: str,
    *, state_revision: int | None = None,
) -> dict[str, Any]:
    with _session_lock(session_id):
        view_sequence = _advance_order(session_id, "view_sequence")
    if state_revision is None:
        state_revision = progress.get("state_revision", 0) if progress is not None else 0
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "progress": progress,
            "session_id": session_id,
            "state_revision": state_revision,
            "view_sequence": view_sequence,
        },
    }


def init_progress(
    steps: list[str], task_title: str = "", session_id: str | None = None,
    legacy_linear: bool = False,
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
        now = _now()
        data = {
            "schema_version": 1,
            "session_id": sid,
            "task_instance_id": str(uuid.uuid4()),
            "task_history": _archive_current_task(previous),
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
        _write(sid, data)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


def update_step(
    session_id: str,
    step_index: int,
    status: str,
    note: str = "",
    assignment: dict[str, Any] | None = None,
    task_instance_id: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data = _read(sid)
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
        if status == "pending":
            step["started_at"] = None
            step["finished_at"] = None
            step.pop("assignment", None)
        elif status == "in_progress":
            if previous_status != "in_progress" or not step["started_at"]:
                _require_execution_metadata(normalized_assignment)
                step["started_at"] = _now()
                # A new run (including reopening a completed step) gets a fresh
                # snapshot so fields from an earlier dispatch cannot linger.
                step["assignment"] = _stamp_assignment(normalized_assignment or {}, scope="step_start")
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
        step["status"] = status
        if note:
            data["steps"][step_index]["note"] = note
        elif status == "pending":
            data["steps"][step_index].pop("note", None)
        _write(sid, data)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


def get_progress(session_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data = _read(sid)
        progress = _progress_payload(data) if data is not None else None
        revision = data.get("state_revision", 0) if data is not None else _read_order(sid)["state_revision"]
    return _tool_result(_format_detail(progress), progress, sid, state_revision=revision)


def render_progress(session_id: str) -> dict[str, Any]:
    return get_progress(session_id)


def _ui_view_result(session_id: str, view_id: str, status: str) -> dict[str, Any]:
    messages = {
        "registered": "已登记为本会话最新的进度视图。",
        "latest": "当前实例是本会话最新的进度视图。",
        "superseded": "当前实例已被本会话的新进度视图替代。",
        "unregistered": "本会话尚未登记进度视图。",
        "missing_progress": "本会话已无进度数据，视图不再有效。",
    }
    return {
        "content": [{"type": "text", "text": messages[status]}],
        "structuredContent": {
            "session_id": session_id,
            "view_id": view_id,
            "status": status,
            "registered": status in ("registered", "latest", "superseded"),
            "is_latest": status in ("registered", "latest"),
        },
    }


def register_ui_view(
    session_id: str, view_id: str, view_sequence: int | None = None,
    state_revision: int | None = None, task_instance_id: str | None = None,
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    with _session_lock(sid):
        data = _read(sid)
        if data is None:
            return _ui_view_result(sid, vid, "missing_progress")
        # Legacy cards have no issuance order. They may keep displaying progress,
        # but must never replace a card whose source result can be ordered.
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
        path = _ui_view_file(sid)
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if (state_revision, view_sequence) < (current.get("state_revision", -1), current.get("view_sequence", -1)):
            return _ui_view_result(sid, vid, "superseded")
        _atomic_json(path, {"view_id": vid, "view_sequence": view_sequence,
                            "state_revision": state_revision, "task_instance_id": task_instance_id})
    return _ui_view_result(sid, vid, "registered")


def check_ui_view(session_id: str, view_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    with _session_lock(sid):
        if not _session_file(sid).exists():
            return _ui_view_result(sid, vid, "missing_progress")
        path = _ui_view_file(sid)
        if not path.exists():
            return _ui_view_result(sid, vid, "unregistered")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        # Keep the currently registered card usable across a task switch until
        # a replacement actually registers; an instance change alone is not a view.
        status = "latest" if metadata.get("view_id") == vid else "superseded"
    return _ui_view_result(sid, vid, status)


def clear_progress(session_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        # Persist the binding requirement first: even if deletion fails halfway,
        # a later stale write without an instance ID can never bind to new data.
        order = _read_order(sid)
        order["require_task_instance_id"] = True
        _atomic_json(_order_file(sid), order)
        path = _session_file(sid)
        if path.exists():
            path.unlink()
        _ui_view_file(sid).unlink(missing_ok=True)
        revision = _advance_order(sid, "state_revision")
    return _tool_result("当前会话的进度数据已清除。", None, sid, state_revision=revision)


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
        "description": "当前 progress 返回的 task_instance_id。同一 session_id 有 task_history 或曾 clear_progress 后必填；不匹配或缺失的旧任务更新会被拒绝。未清理过的首个任务兼容省略。",
    }


def _tool_meta() -> dict[str, Any]:
    return {"ui": {"resourceUri": RESOURCE_URI}}


def _assignment_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "execution_mode 表示本次 attempt 的实际执行者，不表示父任务负责人：主会话负责总计划、协调、集成和父级验收；子智能体只执行明确派发的子任务并向主会话报告。主会话只需 execution_mode；model/reasoning_effort 拿不到可信来源时可省略。子智能体须提供本次派发配置。所有 MCP 入参标记为调用方填报、未经 Codex 宿主核验。Task Progress 不会自动读取 Codex 当前 thread/turn。",
        "properties": {
            "execution_mode": {"type": "string", "enum": list(EXECUTION_MODES), "description": "本次叶任务/attempt 的实际执行者；主会话仍负责根任务统筹与验收，子智能体仅负责派发范围"},
            "tool_identifier": {"type": "string", "description": "派发工具标识"},
            "tool_name": {"type": "string", "description": "工具中文名称"},
            "dispatch_text": {"type": "string", "description": "完整、自包含的派发任务文本"},
            "agent_name": {"type": "string", "description": "子智能体标识或名称"},
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
        "name": "register_ui_view",
        "title": "登记当前进度视图",
        "description": "仅供 App 用首次工具结果的顺序登记视图；旧结果不能抢占新卡片。不修改任务进度。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "view_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "view_sequence": {"type": "integer", "minimum": 0, "description": "首次工具结果的 view_sequence，刷新后不可替换"},
                "state_revision": {"type": "integer", "minimum": 0, "description": "首次工具结果的 state_revision"},
                "task_instance_id": _task_instance_property(),
            },
            "required": ["session_id", "view_id"],
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "check_ui_view",
        "title": "检查当前进度视图",
        "description": "仅供 App 查询当前实例是否为指定会话最新视图；明确返回未登记或进度已清除。",
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
        "description": "旧版线性任务 API。多步骤新任务必须使用 init_task_network；本工具默认拒绝两个及以上步骤。只有用户明确要求线性步骤或恢复旧版线性任务时，才设置 legacy_linear=true。复用同一 Codex 会话的 session_id 会把旧任务归档到 task_history，并由最新进度视图展示；恢复当前任务请用 get_progress。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "任务步骤描述列表",
                },
                "task_title": {"type": "string", "description": "任务标题"},
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
        "_meta": _tool_meta(),
    },
    {
        "name": "init_task_network",
        "title": "初始化任务网络",
        "description": "多步骤新任务的默认且必选入口：创建 HTN 任务树与依赖网络，返回 progress.task_instance_id。开始同一 Codex 会话的新任务时必须复用该会话之前保存的 session_id；这样旧任务会归档到 task_history，最新卡片显示新任务并可展开查看历史，旧卡片自动折叠。未提供 session_id 只用于该会话第一次初始化。恢复当前任务请用 get_progress，当前任务重规划请用 rebuild_task_network。根任务默认 ready。",
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
            },
            "required": ["task_title"],
        },
        "_meta": _tool_meta(),
    },
    {
        "name": "decompose_task",
        "title": "分解任务",
        "description": "分解或追加子任务，继承祖先依赖并检查执行死锁。拆分时评估可并行的独立工作，默认委派给子智能体；每个子任务都必须提供 assignment.execution_mode（分组节点设 main_session，叶节点按分工设 main_session/subagent）。主会话负责总计划、派发、协调、集成和父级验收；子智能体只执行派发范围并向主会话报告，不验收父节点。Task Progress 不会自动派发。分解运行中的任务前须协调停止旧执行者并传当前 attempt_id；旧尝试以 cancelled 保留，父任务转为等待子任务验收。会话存在 task_history 时必须传当前 task_instance_id。",
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
        "_meta": _tool_meta(),
    },
    {
        "name": "update_task",
        "title": "更新任务节点",
        "description": "持续维护任务状态、结果与证据；在开始/派发、阶段结果、长命令或门禁阶段变化、阻塞、重规划和收尾时更新，不要只在开始时建计划或只在普通消息中汇报。运行中命令/子智能体保持 running 并记录可恢复的阶段摘要，禁止把排队或等待标成完成。超过 10 分钟的运行中节点会在返回结果中附 split_review_due；必须先评估拆分可独立阶段，或在摘要记录具体的不拆原因，再继续常规工作/等待。叶任务须先 running 并创建 attempt 才能 finished/resolved；启动时须提供 execution_mode（本次执行者，不等同父任务负责人）。主会话负责总计划、协调、集成和父级验收；子智能体只执行派发范围。主会话的 model/reasoning_effort 可省略；子智能体须提供本次派发配置。插件会标记元数据为调用方填报、未经 Codex 宿主核验。运行中更新须匹配 attempt_id。重试会使直接/传递消费者及祖先旧验收失效，须重新执行或验收；有运行中消费者时拒绝重试。分组直接子任务全部结束后可 cancelled/failed（须 summary），或在前置已结束但未解决时 finished/open（须 summary）；resolved 仍要求依赖满足。会话存在 task_history 或清空过进度时必须传当前 task_instance_id。",
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
                "evidence": {"type": "array", "items": {"type": "string"}},
                "attempt_id": {"type": "string", "description": "running 任务返回的当前尝试 ID"},
                "retry": {"type": "boolean", "description": "从终态重新进入 running 时设为 true"},
                "assignment": _assignment_schema(),
            },
            "required": ["session_id", "task_id", "execution_status"],
        },
        "_meta": _tool_meta(),
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
        "_meta": _tool_meta(),
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
        "_meta": _tool_meta(),
    },
    {
        "name": "update_step",
        "title": "更新任务步骤",
        "description": "更新指定会话中的某一步骤状态、备注及执行者/模型/思考强度；pending 步骤须先 in_progress 才可 completed，首次启动 in_progress 必须提供 execution_mode。主会话的 model/reasoning_effort 可未知并省略；子智能体须提供派发配置。所有入参会标记为调用方填报、未经 Codex 宿主核验。会话存在 task_history 或清空过进度时必须传当前 task_instance_id。返回文本及可视化面板数据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "task_instance_id": _task_instance_property(),
                "step_index": {"type": "integer", "description": "步骤索引（从 0 开始）"},
                "status": {"type": "string", "enum": list(VALID_STATUSES)},
                "note": {"type": "string", "description": "可选备注"},
                "assignment": _assignment_schema(),
            },
            "required": ["session_id", "step_index", "status"],
        },
        "_meta": _tool_meta(),
    },
    {
        "name": "get_progress",
        "title": "读取任务进度",
        "description": "读取指定会话的当前任务详情、task_instance_id 和 task_history 历史任务快照；HTN history 仅表示当前任务的计划轮次。超过 10 分钟的运行中节点会在 split_review_due 中列出并返回强制拆分复核提示；读取后必须立即评估并拆分可独立阶段，或记录不拆分的具体原因。MCP Apps 客户端会显示交互式进度面板。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _tool_meta(),
    },
    {
        "name": "render_progress",
        "title": "展示任务进度",
        "description": "返回指定会话的任务进度文本和交互式面板数据。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _tool_meta(),
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
        "description": "清除指定 session_id 的当前任务、task_history 历史任务和视图登记，不影响其他会话。为阻止清空前的迟到写入，后续新任务写操作必须传 task_instance_id。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _tool_meta(),
    },
]


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "register_ui_view": lambda args: register_ui_view(
        args.get("session_id"), args.get("view_id"), args.get("view_sequence"),
        args.get("state_revision"), args.get("task_instance_id"),
    ),
    "check_ui_view": lambda args: check_ui_view(
        args.get("session_id"), args.get("view_id")
    ),
    "init_progress": lambda args: init_progress(
        args.get("steps", []), args.get("task_title", ""), args.get("session_id"),
        args.get("legacy_linear", False),
    ),
    "init_task_network": lambda args: init_task_network(
        args.get("task_title", ""), args.get("session_id"), args.get("main_session")
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
    ),
    "get_ready_tasks": lambda args: get_ready_tasks(args.get("session_id")),
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
    ),
    "get_progress": lambda args: get_progress(args.get("session_id")),
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


def _resource_html() -> str:
    return APP_HTML_PATH.read_text(encoding="utf-8")


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
                "serverInfo": {"name": "task-progress", "version": "0.1.1"},
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
                    }
                ]
            },
        )

    if method == "resources/read":
        uri = params.get("uri")
        if uri != RESOURCE_URI:
            return _err(message_id, -32602, f"未知资源 URI：{uri}")
        try:
            return _ok(
                message_id,
                {
                    "contents": [
                        {
                            "uri": RESOURCE_URI,
                            "mimeType": RESOURCE_MIME,
                            "_meta": {"ui": {"prefersBorder": True}},
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
            return _ok(message_id, handler(args))
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
