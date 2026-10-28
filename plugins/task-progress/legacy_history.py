"""Bounded, read-only archive access to the retired SQLite schema 3 store.

This is a separate source, never an input to the current JSON task state. Old
``resolved`` outcomes remain old outcomes; no contemporary review is invented.
Only explicit display fields are selected. Request receipts, bindings, actions,
assignments, raw state, evidence payloads and logs are deliberately excluded.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
from typing import Any


MAX_TASKS = 100
MAX_NODES = 100
MAX_TEXT = 2000
MAX_PAYLOAD = 65536
_TASK_COLUMNS = (
    "task_instance_id", "generation", "title", "status", "control_state",
    "epoch", "root_id", "revision", "created_at", "updated_at", "archived_at",
)
_NODE_COLUMNS = (
    "node_id", "parent_id", "title", "planning_state", "execution_status", "outcome",
)
_CREDENTIAL = re.compile(
    r"\bBearer\s+[^\s\"'<>]+|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)|"
    r"(?<=://)[^\s/@:]+:[^\s/@]+@",
    re.IGNORECASE,
)


def _text(value: Any, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    return _CREDENTIAL.sub("[redacted]", value[:limit])


def _display(row: sqlite3.Row, columns: tuple[str, ...]) -> dict[str, Any]:
    return {key: (_text(row[key]) if isinstance(row[key], str) else
                  row[key] if row[key] is None or isinstance(row[key], (int, float)) else None)
            for key in columns}


def _envelope(session_id: str) -> dict[str, Any]:
    return {"schema": 3, "source": "sqlite_v3", "read_only": True,
            "status": "available", "session_id": session_id,
            "current_task_id": None, "task_count": 0, "records": []}


def _supported(connection: sqlite3.Connection, include_details: bool) -> bool:
    required = {
        "sessions": {"session_id", "current_task_id"},
        "task_instances": {"session_id", *_TASK_COLUMNS},
    }
    if include_details:
        required["nodes"] = {"task_instance_id", "epoch", "payload_json", *_NODE_COLUMNS}
    for table, columns in required.items():
        # A view with matching names must not execute arbitrary source queries.
        if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return False
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns.issubset(actual):
            return False
    return True


def _nodes(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    # Large payloads are not transferred from SQLite or parsed in Python.
    query = ("SELECT " + ",".join(_NODE_COLUMNS) + ", CASE WHEN length(payload_json)<=? "
             "THEN payload_json ELSE NULL END AS safe_payload FROM nodes "
             "WHERE task_instance_id=? AND epoch=? ORDER BY node_id LIMIT ?")
    rows = connection.execute(query, (MAX_PAYLOAD, record["task_instance_id"],
                                      record["epoch"], MAX_NODES + 1)).fetchall()
    record["nodes"] = []
    if len(rows) > MAX_NODES:
        record["nodes_truncated"] = True
    for row in rows[:MAX_NODES]:
        node = _display(row, _NODE_COLUMNS)
        node["id"] = node.pop("node_id")
        payload = row["safe_payload"]
        if payload is None:
            node["details_unavailable"] = True
        else:
            try:
                parsed = json.loads(payload)
                summary = _text(parsed.get("summary")) if isinstance(parsed, dict) else None
                if summary is not None:
                    node["summary"] = summary
            except (TypeError, ValueError, RecursionError):
                node["details_unavailable"] = True
        record["nodes"].append(node)
        if node["id"] == record["root_id"] and "summary" in node:
            record["summary"] = node["summary"]


def read_legacy_history(db_path: str | Path, session_id: str,
                        include_details: bool = False) -> dict[str, Any] | None:
    """Return a separate archive envelope, or None for a missing DB/session.

    Failures use fixed warnings without paths, SQL, exception strings or source
    payloads. Clear-history tombstones belong to the current store's wrapper:
    callers must suppress this archive after an explicit clear, not delete it.
    Reads use SQLite ``mode=ro`` (including WAL contents), never immutable mode,
    migration, schema repair, or a writable fallback connection.
    """
    result = _envelope(session_id)
    connection = None
    try:
        path = Path(db_path).expanduser().resolve()
        if not path.exists():
            return None
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        # Bound unusual or malicious schemas and unexpectedly costly queries.
        calls = 0

        def budget():
            nonlocal calls
            calls += 1
            return calls > 1000

        connection.set_progress_handler(budget, 1000)
        connection.execute("BEGIN")
        if not _supported(connection, include_details):
            result.update(status="unavailable", warning="legacy_history_unsupported_schema")
            return result
        session = connection.execute(
            "SELECT current_task_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            return None
        result["current_task_id"] = _text(session["current_task_id"])
        result["task_count"] = connection.execute(
            "SELECT count(*) FROM task_instances WHERE session_id=?", (session_id,)).fetchone()[0]
        rows = connection.execute(
            "SELECT " + ",".join(_TASK_COLUMNS) + " FROM task_instances WHERE session_id=? "
            "ORDER BY generation DESC, task_instance_id LIMIT ?", (session_id, MAX_TASKS)).fetchall()
        result["truncated"] = result["task_count"] > MAX_TASKS
        for row in rows:
            record = _display(row, _TASK_COLUMNS)
            record["is_current"] = record["task_instance_id"] == result["current_task_id"]
            if include_details:
                _nodes(connection, record)
            result["records"].append(record)
        return result
    except (OSError, sqlite3.Error, ValueError, TypeError):
        # Do not return partially loaded records after a database failure.
        result = _envelope(session_id)
        result.update(status="unavailable", warning="legacy_history_unavailable")
        return result
    finally:
        if connection is not None:
            connection.close()
