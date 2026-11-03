"""Execution identity and bounded decomposition decisions, independent of transport."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json


TERMINAL = {"succeeded", "failed", "blocked", "cancelled"}
PROVIDERS = {"dagu", "subagent", "external", "managed_command"}
REVIEW_SECONDS = 600


def fingerprint(node):
    scope = [node.get("attempt_id"), node.get("title"), node.get("execution", {}).get("execution_id")]
    return hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()


def split_due(node, now=None):
    if node.get("execution_status") != "running" or node.get("children"):
        return False
    try:
        current = now or datetime.now().astimezone()
        started = datetime.fromisoformat(node["started_at"])
        if (current - started).total_seconds() <= REVIEW_SECONDS:
            return False
    except (KeyError, TypeError, ValueError):
        # An unknown legacy boundary is not evidence of a ten-minute runtime.
        return False
    try:
        review = node.get("decomposition_review") or {}
        return not (review.get("scope_fingerprint") == fingerprint(node)
                    and current < datetime.fromisoformat(review["expires_at"]))
    except (KeyError, TypeError, ValueError):
        return True


def bind(node, execution_id, provider, summary, now, metadata=None):
    existing = node.get("execution")
    if existing:
        if existing["execution_id"] == execution_id and existing["provider"] == provider:
            if existing.get("purpose") != summary or existing.get("metadata", {}) != (metadata or {}):
                raise ValueError("execution_id 已绑定不同内容，不能修改原始执行输入。")
            return False
        raise ValueError("execution_already_bound: 每个 attempt 只能绑定一轮执行；先收尾，再 retry=true 创建新 attempt。")
    if node.get("execution_status") != "running" or node.get("children"):
        raise ValueError("只能给当前 running 的叶 attempt 绑定执行。")
    node["execution"] = {
        "execution_id": execution_id, "provider": provider, "attempt_id": node["attempt_id"],
        "status": "running", "purpose": summary, "summary": summary,
        "started_at": now, "finished_at": None, "exit_code": None,
        "metadata": deepcopy(metadata or {}), "evidence": [],
        "verification": "managed_worker" if provider == "managed_command" else "caller_declared",
    }
    return True


def finish(node, execution_id, provider, status, summary, evidence, exit_code, finished_at):
    execution = node.get("execution") or {}
    if execution.get("execution_id") != execution_id or execution.get("provider") != provider:
        raise ValueError("execution_id/provider 不匹配，不能把其他运行的回执用于当前 attempt。")
    if status not in TERMINAL:
        raise ValueError("终态必须为 succeeded/failed/blocked/cancelled。")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise ValueError("exit_code 必须为整数。")
    if (status == "succeeded" and exit_code not in (None, 0)) or (status == "failed" and exit_code == 0):
        raise ValueError("status 与 exit_code 冲突。")
    try:
        start, end = datetime.fromisoformat(execution["started_at"]), datetime.fromisoformat(finished_at)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("执行结束时间必须有时区且不早于开始时间。") from exc
    receipt = {"status": status, "summary": summary, "evidence": evidence, "exit_code": exit_code}
    digest = hashlib.sha256(json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if execution.get("status") in TERMINAL:
        if execution.get("receipt_digest") == digest:
            return False
        raise ValueError("execution_receipt_conflict: 已结束执行的回执不可覆盖。")
    if node.get("execution_status") != "running":
        raise ValueError("当前 attempt 已结束；不能用迟到执行回执改写结果。")
    execution.update(**deepcopy(receipt), finished_at=finished_at, receipt_digest=digest)
    # A successful process is evidence, not automatic acceptance of the task.
    # Its end is visible while the owner validates and closes the goal.
    if status == "succeeded":
        return True
    node.update(execution_status="finished" if status == "blocked" else status,
                outcome="open" if status == "blocked" else None,
                summary=summary, evidence=list(evidence), finished_at=finished_at)
    if status in ("failed", "blocked"):
        node["failure_reason"] = summary
    return True


def execution_summary(execution):
    if not execution:
        return None
    return {key: deepcopy(execution.get(key)) for key in
            ("execution_id", "provider", "status", "started_at", "finished_at", "exit_code", "verification")}
