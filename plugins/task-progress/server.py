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

from task_progress_v3 import V3Error, V3Store

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
V3_STORE = V3Store(CACHE_DIR / "progress-v3.sqlite3")

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
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


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
    if task_instance_id is None and not data.get("task_history"):
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
    return {
        "schema_version": 1,
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
        "steps": [
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
        ],
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


def _normalize_main_session(value: Any) -> dict[str, str] | None:
    """Validate metadata supplied by the trusted main session at initialization."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProgressError("main_session 必须是包含主会话模型信息的对象。")
    model = value.get("model")
    effort = value.get("reasoning_effort")
    if not isinstance(model, str) or not model.strip():
        raise ProgressError("main_session.model 必须是非空字符串。")
    if effort not in REASONING_EFFORTS:
        raise ProgressError(f"main_session.reasoning_effort 必须是 {REASONING_EFFORTS} 之一。")
    return {"model": model.strip(), "reasoning_effort": effort}


def _new_network(
    session_id: str,
    task_title: str,
    *,
    epoch: int = 0,
    main_session: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = _now()
    root_id = "root" if epoch == 0 else f"root-e{epoch}"
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


def _normalize_assignment(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProgressError("assignment 必须是对象。")
    assignment: dict[str, Any] = {}
    string_fields = ("tool_identifier", "tool_name", "dispatch_text", "agent_name", "model", "batch")
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
    if assignment is None:
        raise ProgressError("启动任务必须提供 assignment，记录 execution_mode、model 和 reasoning_effort。")
    missing = [key for key in ("execution_mode", "model", "reasoning_effort") if not assignment.get(key)]
    if missing:
        raise ProgressError(f"启动任务时 assignment 必须包含实际执行配置：{', '.join(missing)}。")


def _historical_ids(data: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for epoch in data.get("history", []):
        nodes = epoch.get("nodes", {}) if isinstance(epoch, dict) else {}
        if isinstance(nodes, dict):
            found.update(nodes)
    return found


def _validate_network(data: dict[str, Any]) -> None:
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
    edges = {node_id: set() for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}
    for node_id, node in nodes.items():
        if node.get("id") != node_id:
            raise ProgressError("任务节点 ID 与网络索引不一致。")
        parent_id = node.get("parent_id")
        if node_id != root_id:
            if not isinstance(parent_id, str) or parent_id not in nodes:
                raise ProgressError(f"任务 {node_id} 的父节点不存在。")
            children[parent_id].append(node_id)
            edges[parent_id].add(node_id)
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
            edges[dependency_id].add(node_id)
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
    if visited != len(nodes):
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


def _node_is_ready(data: dict[str, Any], node: dict[str, Any]) -> bool:
    return (
        not node.get("children")
        and node.get("execution_status") == "pending"
        and all(_dependency_satisfied(data, node, dep_id) for dep_id in node.get("depends_on", []))
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
        if any(value == "ready" for value in child_statuses):
            return "decomposing"
        return "blocked"
    return "ready" if _node_is_ready(data, node) else "blocked"


def _network_progress_payload(data: dict[str, Any]) -> dict[str, Any]:
    _validate_network(data)
    nodes = data["nodes"]
    leaves = [node for node in nodes.values() if not node.get("children")]
    ready_nodes = [node for node in leaves if _node_is_ready(data, node)]
    terminal_leaves = [node for node in leaves if _node_is_terminal(node)]
    resolved_leaves = [node for node in leaves if node.get("outcome") == "resolved"]
    open_leaves = [node for node in leaves if node.get("outcome") == "open"]
    failed_leaves = [node for node in leaves if node.get("execution_status") == "failed"]
    running_leaves = [node for node in leaves if node.get("execution_status") == "running"]
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
    elif running_leaves or ready_nodes:
        overall_status = "in_progress"
    elif total and completed == total:
        overall_status = "waiting_synthesis"
    else:
        overall_status = "pending"
    enriched_nodes = []
    for node_id, node in nodes.items():
        unmet = [dep_id for dep_id in node.get("depends_on", []) if not _dependency_satisfied(data, node, dep_id)]
        dependencies = []
        for dep_id in node.get("depends_on", []):
            dependency = nodes[dep_id]
            dependencies.append({
                "id": dep_id,
                "title": dependency["title"],
                "execution_status": dependency["execution_status"],
                "outcome": dependency.get("outcome"),
                "summary": dependency.get("summary", ""),
                "evidence": dependency.get("evidence", []),
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
) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        data = _read(sid)
        if data is None or data.get("schema_version") != 2:
            raise ProgressError("该会话没有 HTN 任务网络；请先调用 init_task_network。")
        _validate_task_instance(data, task_instance_id)
        _add_tasks(data, task_id, tasks)
        data["revision"] = int(data.get("revision", 0)) + 1
        _write(sid, data)
    progress = _progress_payload(data)
    return _tool_result(_format_detail(progress), progress, sid)


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
        if node.get("children") and execution_status in ("running", "failed", "cancelled"):
            raise ProgressError("分组任务由子任务执行；请完成子任务后再提交父任务综合结果。")
        if previous_status in TERMINAL_EXECUTION_STATUSES and execution_status != "running":
            raise ProgressError("已结束任务不能覆盖结果；如需重试，请使用 running 并设置 retry=true。")
        if execution_status == "running":
            if previous_status in TERMINAL_EXECUTION_STATUSES and not retry:
                raise ProgressError("已结束任务重试时必须设置 retry=true，且会创建新的 attempt_id。")
            if previous_status == "pending" and not _node_is_ready(data, node):
                raise ProgressError("该任务尚未 ready；请先完成依赖，或显式设置 dependency_policy=finished。")
            if previous_status != "running":
                _require_execution_metadata(normalized_assignment)
                node["attempt"] = int(node.get("attempt", 0)) + 1
                node["attempt_id"] = str(uuid.uuid4())
                # An assignment is an immutable snapshot for this attempt. A retry
                # replaces the node's current assignment instead of merging stale fields.
                node["assignment"] = dict(normalized_assignment or {})
                node["started_at"] = _now()
                node["finished_at"] = None
                node["outcome"] = None
                node["summary"] = ""
                node["evidence"] = []
                node.setdefault("attempts", []).append({
                    "attempt": node["attempt"],
                    "attempt_id": node["attempt_id"],
                    "execution_status": "running",
                    "outcome": None,
                    "summary": "",
                    "evidence": [],
                    "started_at": node["started_at"],
                    "assignment": dict(node["assignment"]),
                })
            elif attempt_id != node.get("attempt_id"):
                raise ProgressError("更新运行中任务必须提供当前 attempt_id。")
        elif previous_status == "running" and attempt_id != node.get("attempt_id"):
            raise ProgressError("attempt_id 不匹配；迟到或旧尝试结果已拒绝。")
        if (
            previous_status == "pending"
            and execution_status in ("finished", "failed")
            and not node.get("children")
            and not _node_is_ready(data, node)
        ):
            raise ProgressError("依赖尚未满足的任务不能直接结束；先完成前驱或改用 dependency_policy=finished。")
        if execution_status == "finished" and node.get("children"):
            if not all(_node_is_terminal(data["nodes"][child_id]) for child_id in node["children"]):
                raise ProgressError("父任务必须等所有直接子任务结束后再综合验收。")
        node["execution_status"] = execution_status
        node["outcome"] = outcome
        if summary:
            node["summary"] = summary
        if evidence:
            node["evidence"] = evidence
        if execution_status in TERMINAL_EXECUTION_STATUSES:
            node["finished_at"] = _now()
        if node.get("attempts") and node.get("attempt_id"):
            attempt = node["attempts"][-1]
            attempt.update({
                "execution_status": execution_status,
                "outcome": outcome,
                "summary": node.get("summary", ""),
                "evidence": node.get("evidence", []),
                "finished_at": node.get("finished_at"),
                "assignment": attempt.get("assignment", {}),
            })
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
        replacement = _new_network(sid, new_title, epoch=next_epoch)
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
        by_id = {node["id"]: node for node in progress["nodes"]}

        def visit(node_id: str, depth: int = 0) -> None:
            node = by_id[node_id]
            prefix = "  " * depth
            lines.append(f"{prefix}- [{node['id']}] {node['title']} — {node['status']}")
            if node.get("depends_on"):
                edges = node['depends_on']
                labels = [str(edge.get('node_id')) + ('（执行结束即可）' if edge.get('policy') == 'returned' else '（需要已解决）') if isinstance(edge, dict) else str(edge) for edge in edges]
                lines.append(f"{prefix}  依赖：{', '.join(labels)}")
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
                lines.append(f"{prefix}  执行：{executor}；model={assignment.get('model') or '未记录'}；reasoning={assignment.get('reasoning_effort') or '未记录'}")
            if node.get("summary"):
                label = "整体说明" if node_id == progress.get("root_id") else "完成情况"
                lines.append(f"{prefix}  {label}：{node['summary']}")
            for output in node.get("outputs", []):
                lines.append(f"{prefix}  产出：{output}")
            for check in node.get("checks", []):
                lines.append(f"{prefix}  验收检查：{check}")
            for evidence in node.get("evidence_refs", node.get("evidence", [])):
                lines.append(f"{prefix}  证据：{evidence}")
            for unresolved in node.get("unresolved", []):
                lines.append(f"{prefix}  尚未解决：{unresolved}")
            for child_id in node.get("children", []):
                visit(child_id, depth + 1)

        visit(progress["root_id"])
        return "\n".join(lines)
    lines = [
        f"📋 任务：{progress['task_title']}",
        f"📊 进度：{progress['completed']}/{progress['total']} 步完成（{progress['percent']}%）",
        f"状态：{progress['status']}",
    ]
    for step in progress["steps"]:
        icon = STATUS_ICONS.get(step["status"], "⬜")
        note = f" — {step['note']}" if step.get("note") else ""
        lines.append(f"  {icon} [{step['index']}] {step['description']}{note}")
    return "\n".join(lines)


def _tool_result(
    text: str, progress: dict[str, Any] | None, session_id: str
) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            **({"api_version": 3} if progress and progress.get("api_version") == 3 else {}),
            "progress": progress,
            "session_id": session_id,
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
                step["assignment"] = dict(normalized_assignment or {})
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
    if V3_STORE.has_session(sid):
        progress = V3_STORE.read_progress(sid)
        legacy = _read(sid)
        if progress is not None and legacy is not None:
            archived = _progress_payload(legacy)
            archived["archived_at"] = legacy.get("updated_at") or legacy.get("created_at")
            existing = {item.get("task_instance_id") for item in progress.get("task_history", []) if isinstance(item, dict)}
            if archived.get("task_instance_id") not in existing and archived.get("task_instance_id") != progress.get("task_instance_id"):
                progress["task_history"] = [*progress.get("task_history", []), archived]
        return _tool_result(_format_detail(progress), progress, sid)
    data = _read(sid)
    progress = _progress_payload(data) if data is not None else None
    return _tool_result(_format_detail(progress), progress, sid)


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


def register_ui_view(session_id: str, view_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    if V3_STORE.has_session(sid):
        result = V3_STORE.register_view(sid, vid)
        return _ui_view_result(sid, vid, "registered")
    with _session_lock(sid):
        if not _session_file(sid).exists():
            return _ui_view_result(sid, vid, "missing_progress")
        path = _ui_view_file(sid)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps({"view_id": vid}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
    return _ui_view_result(sid, vid, "registered")


def check_ui_view(session_id: str, view_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    vid = _normalize_view_id(view_id)
    if V3_STORE.has_session(sid):
        result = V3_STORE.check_view(sid, vid)
        status = {"current": "latest"}.get(result["status"], result["status"])
        return _ui_view_result(sid, vid, status)
    with _session_lock(sid):
        if not _session_file(sid).exists():
            return _ui_view_result(sid, vid, "missing_progress")
        path = _ui_view_file(sid)
        if not path.exists():
            return _ui_view_result(sid, vid, "unregistered")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        status = "latest" if metadata.get("view_id") == vid else "superseded"
    return _ui_view_result(sid, vid, status)


def clear_progress(session_id: str) -> dict[str, Any]:
    sid = _normalize_session_id(session_id)
    with _session_lock(sid):
        path = _session_file(sid)
        if path.exists():
            path.unlink()
        _ui_view_file(sid).unlink(missing_ok=True)
        V3_STORE.clear_session(sid)
    return _tool_result("当前会话的进度数据已清除。", None, sid)


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
        "description": "当前 progress 返回的 task_instance_id。同一 session_id 已创建过新任务、有 task_history 时必填；不匹配的旧任务更新会被拒绝。首个任务兼容省略。",
    }


def _tool_meta() -> dict[str, Any]:
    return {"ui": {"resourceUri": RESOURCE_URI}}


def _assignment_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "执行者与运行参数；启动任务/步骤时必须提供 execution_mode、model 和 reasoning_effort。Task Progress 只记录信息，不实际创建或调用子智能体。",
        "properties": {
            "execution_mode": {"type": "string", "enum": list(EXECUTION_MODES), "description": "实际执行任务的是主会话还是子智能体"},
            "tool_identifier": {"type": "string", "description": "派发工具标识"},
            "tool_name": {"type": "string", "description": "工具中文名称"},
            "dispatch_text": {"type": "string", "description": "完整、自包含的派发任务文本"},
            "agent_name": {"type": "string", "description": "子智能体标识或名称"},
            "fork_turns": {"type": "string", "enum": ["none"]},
            "model": {"type": "string", "description": "选定模型"},
            "reasoning_effort": {"type": "string", "enum": list(REASONING_EFFORTS)},
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
            "assignment": _assignment_schema(),
        },
        "required": ["id", "title"],
    }


TOOLS = [
    {
        "name": "register_ui_view",
        "title": "登记当前进度视图",
        "description": "仅供 App 登记指定会话最新的视图实例；不修改任务进度，也不创建卡片。",
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
                    "description": "仅填写当前主会话已明确提供的准确模型和思考强度；不可猜测或从模型目录、子智能体、历史记录回填。未知时整体省略，卡片会显示‘未记录’。",
                    "properties": {
                        "model": {"type": "string", "description": "主会话模型标识"},
                        "reasoning_effort": {"type": "string", "enum": list(REASONING_EFFORTS), "description": "主会话思考强度"},
                    },
                    "required": ["model", "reasoning_effort"],
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
        "description": "将一个叶任务分解为子任务，可同时描述依赖、依赖放行策略、复杂度档位与子智能体派发信息。父子层级与依赖合并后必须无环。会话存在 task_history 时必须传当前 task_instance_id。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": _session_property(),
                "task_instance_id": _task_instance_property(),
                "task_id": {"type": "string", "description": "待分解的父任务 ID"},
                "tasks": {"type": "array", "items": _task_schema(), "description": "该父任务的子任务列表"},
            },
            "required": ["session_id", "task_id", "tasks"],
        },
        "_meta": _tool_meta(),
    },
    {
        "name": "update_task",
        "title": "更新任务节点",
        "description": "更新任务执行状态、resolved/open 结果、摘要、证据或派发元数据。首次启动 running 必须提供完整 assignment（execution_mode、model、reasoning_effort）；运行中的结果必须匹配当前 attempt_id；完成的父任务用于综合验收全部直接子任务。会话存在 task_history 时必须传当前 task_instance_id。",
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
        "description": "读取当前 epoch 中所有依赖已满足、可开始执行的叶任务及其前驱结果证据。",
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
        "description": "更新指定会话中的某一步骤状态、备注及执行者/模型/思考强度；首次启动 in_progress 必须提供完整 assignment（execution_mode、model、reasoning_effort）。会话存在 task_history 时必须传当前 task_instance_id。返回文本及可视化面板数据。",
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
        "description": "读取指定会话的当前任务详情、task_instance_id 和 task_history 历史任务快照；HTN history 仅表示当前任务的计划轮次。MCP Apps 客户端会显示交互式进度面板。",
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
        "description": "清除指定 session_id 的当前任务、task_history 历史任务和视图登记，不影响其他会话。",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": _session_property()},
            "required": ["session_id"],
        },
        "_meta": _tool_meta(),
    },
]


def _v3_tool(name: str, title: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name, "title": title, "description": description,
        "inputSchema": {"type": "object", "properties": properties, "required": required or []},
        "_meta": _tool_meta(),
    }


_scope_schema = {"type": "object", "properties": {
    "session_id": {"type": "string", "format": "uuid"},
    "task_instance_id": {"type": "string"}, "epoch": {"type": "integer"},
}}
_request_id = {"type": "string", "minLength": 1, "description": "写操作的幂等 UUID"}
_revision = {"type": "integer", "minimum": 0}
TOOLS.extend([
    _v3_tool("task_open", "创建或恢复任务", "v3 入口：先记录目标和验收需求，再通过 PLAN 动作选择直接执行或拆分。", {
        "session_id": _session_property(), "mode": {"type": "string", "enum": ["ensure", "new", "resume"]}, "request_id": _request_id,
        "task_title": {"type": "string"}, "objective": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "object"}},
        "acceptance_criteria": {"type": "array"}, "main_session": {"type": "object"}, "binding": {"type": "object"},
    }, ["request_id"]),
    _v3_tool("task_read", "读取任务详情", "按范围读取当前任务、单个任务项、执行上下文或事件增量。", {
        "scope": _scope_schema, "session_id": _session_property(), "projection": {"type": "string", "enum": ["summary", "node", "context", "events"]},
        "node_id": {"type": "string"}, "after_event_seq": {"type": "integer"}, "limit": {"type": "integer"},
    }, ["session_id"]),
    _v3_tool("task_advance", "读取或认领待办动作", "preview 只读展示动作；claim 以 lease 原子认领 PLAN、ORCHESTRATE、DISPATCH、COLLECT 或 SYNTHESIZE 动作。", {
        "scope": _scope_schema, "session_id": _session_property(), "mode": {"type": "string", "enum": ["preview", "claim"]},
        "max_actions": {"type": "integer", "minimum": 1, "maximum": 8}, "request_id": _request_id, "expected_revision": _revision,
    }, ["session_id"]),
    _v3_tool("task_decide", "提交规划或协调决策", "提交计划执行/拆分、分发/等待/完成验收或同任务重规划决策。", {
        "scope": _scope_schema, "session_id": _session_property(), "request_id": _request_id, "expected_revision": _revision,
        "action_id": {"type": "string"}, "lease_token": {"type": "string"}, "kind": {"type": "string"}, "node_id": {"type": "string"},
        "rationale": {"type": "string"}, "tasks": {"type": "array", "items": {"type": "object"}}, "role": {"type": "string"},
        "instructions": {"type": "string"}, "deliverables": {"type": "array"}, "profile": {"type": "string"}, "resources": {"type": "array"},
        "execution_mode": {"type": "string", "enum": ["subagent"]}, "model": {"type": "string"}, "reasoning_effort": {"type": "string", "enum": list(REASONING_EFFORTS)},
        "assignment_id": {"type": "string"}, "wait_for": {"type": "string"}, "outcome": {"type": "string"}, "summary": {"type": "string"},
        "outputs": {"type": "array"}, "unresolved": {"type": "array"}, "requirement_results": {"type": "array"}, "evidence_refs": {"type": "array"}, "reason": {"type": "string"},
    }, ["session_id", "request_id", "kind"]),
    _v3_tool("task_report", "记录执行与对账", "记录外部派发前后的事实、worker 报告、观察状态、未知结果对账、停止确认或主会话执行结果。", {
        "scope": _scope_schema, "session_id": _session_property(), "request_id": _request_id, "expected_revision": _revision,
        "event_kind": {"type": "string"}, "assignment_id": {"type": "string"}, "action_id": {"type": "string"}, "lease_token": {"type": "string"},
        "provider_ref": {"type": "object"}, "observed_status": {"type": "string"}, "source_identity": {"type": "string"}, "evidence": {"type": "array"},
        "summary": {"type": "string"}, "outputs": {"type": "array"}, "evidence_refs": {"type": "array"}, "unresolved": {"type": "array"},
        "report_seq": {"type": "integer"}, "final": {"type": "boolean"}, "node_id": {"type": "string"}, "assignment": {"type": "object"}, "outcome": {"type": "string"},
    }, ["session_id", "request_id", "event_kind"]),
    _v3_tool("task_control", "控制当前任务", "暂停、继续、取消、确认停止或重新打开受结果影响的任务分支。", {
        "scope": _scope_schema, "session_id": _session_property(), "request_id": _request_id, "expected_revision": _revision,
        "operation": {"type": "string"}, "node_id": {"type": "string"}, "input_request_id": {"type": "string"}, "input_version": {"type": "integer"}, "answer": {}, "source": {"type": "string"},
    }, ["session_id", "request_id", "operation"]),
    _v3_tool("task_history", "查询任务历史", "分页查询当前会话归档任务、同一任务旧计划轮次或执行尝试。", {
        "session_id": _session_property(), "type": {"type": "string", "enum": ["task", "epoch", "attempt"]}, "task_instance_id": {"type": "string"},
        "node_id": {"type": "string"}, "limit": {"type": "integer"}, "cursor": {"type": "string"},
    }, ["session_id", "type"]),
])


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "task_open": lambda args: V3_STORE.call("task_open", args),
    "task_read": lambda args: V3_STORE.call("task_read", args),
    "task_advance": lambda args: V3_STORE.call("task_advance", args),
    "task_decide": lambda args: V3_STORE.call("task_decide", args),
    "task_report": lambda args: V3_STORE.call("task_report", args),
    "task_control": lambda args: V3_STORE.call("task_control", args),
    "task_history": lambda args: V3_STORE.call("task_history", args),
    "register_ui_view": lambda args: register_ui_view(
        args.get("session_id"), args.get("view_id")
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
        args.get("task_instance_id"),
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


def handle(message: dict[str, Any]) -> str | None:
    method = message.get("method", "")
    message_id = message.get("id")
    params = message.get("params", {})

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
                "serverInfo": {"name": "task-progress", "version": "0.2.0"},
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
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return _err(message_id, -32601, f"未知工具：{name}")
        try:
            return _ok(message_id, handler(args))
        except V3Error as exc:
            payload = {"api_version": 3, "error": {"code": exc.code, "message": str(exc), "recovery": exc.recovery}}
            return _ok(message_id, {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload, "isError": True})
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
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
