"""Local, read-only bridge from the hook process to Task Progress state."""
from functools import lru_cache
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from contextlib import contextmanager


def _identity(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _scope(event: dict[str, Any]) -> tuple[str, str] | None:
    session_id, agent_id = _identity(event.get("session_id")), _identity(event.get("agent_id"))
    # Missing actor identity is unknown; in particular it is not the main agent.
    return (session_id, agent_id) if session_id and agent_id else None


def _state_dir() -> Path:
    return Path.home() / ".cache" / "task-progress" / "hook-contexts"


def _digest(kind: str, host_session: str, agent_id: str) -> str:
    raw = json.dumps([kind, host_session, agent_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    with lock.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0"); handle.flush()
            handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hook-context-", suffix=".tmp", dir=path.parent)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _binding_path(scope: tuple[str, str]) -> Path:
    return _state_dir() / ("binding-" + _digest("binding", *scope) + ".json")


def _binding_path_for_digest(digest: str) -> Path:
    return _state_dir() / ("binding-" + digest + ".json")


def _revoke_child_dispatch(scope: tuple[str, str], target: dict[str, Any], kind: str) -> None:
    """Invalidate a child's exact observed map, then unlink it from its parent index."""
    if not target.get("task_id") or not target.get("attempt_id"):
        return
    dispatch_digest = _digest("dispatch", scope[0], scope[1])
    map_path = _state_dir() / ("dispatch-" + dispatch_digest + ".json")
    parent_digest = None
    with _locked(map_path):
        mapping = _read_json(map_path)
        mapped_target = mapping.get("target") or {}
        same_task = all(mapped_target.get(key) == target.get(key)
                        for key in ("session_id", "task_instance_id", "epoch", "task_id"))
        same_attempt = mapped_target.get("attempt_id") == target.get("attempt_id")
        if (mapping.get("schema_version") != 1 or not same_task
                or (not same_attempt and kind != "attempt_restarted")):
            return
        mapping.update(status="stale", reason_code="child_attempt_ended",
                       updated_at=int(time.time()))
        _write_json(map_path, mapping)
        parent_digest = mapping.get("parent_binding_digest")
    if not isinstance(parent_digest, str) or not parent_digest:
        return
    parent_path = _binding_path_for_digest(parent_digest)
    with _locked(parent_path):
        parent = _read_json(parent_path)
        observed = parent.get("observed_dispatches", [])
        if not isinstance(observed, list):
            return
        updated = [item for item in observed
                   if not (isinstance(item, dict)
                           and item.get("dispatch_map_digest") == dispatch_digest)]
        if len(updated) != len(observed):
            parent["observed_dispatches"] = updated
            parent["binding_revision"] = int(parent.get("binding_revision", 0)) + 1
            parent["updated_at"] = int(time.time())
            _write_json(parent_path, parent)


def observe_receipt(event: dict[str, Any], receipt: dict[str, Any]) -> None:
    """Store a host-observed successful receipt as a scoped, untrusted association."""
    scope = _scope(event)
    effect = receipt.get("context_effect")
    if scope is None or not isinstance(effect, dict) or receipt.get("receipt_version") != 1:
        return
    path = _binding_path(scope)
    with _locked(path):
        binding = _read_json(path)
        if binding.get("schema_version") != 1:
            binding = {"schema_version": 1, "binding_revision": 0, "status": "unbound",
                       "foreground": None, "active_work": None, "pending_submission": None,
                       "pending_dispatches": [], "observed_dispatches": []}
        kind = effect.get("kind")
        target = {key: effect.get(key) for key in
                  ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id")}
        current = binding.get("foreground") or {}
        receipt_revision = effect.get("state_revision")
        receipt_view_sequence = effect.get("view_sequence")
        actor_scope = binding.get("actor_scope")
        if not isinstance(actor_scope, dict):
            map_path = _state_dir() / ("dispatch-" + _digest("dispatch", scope[0], scope[1]) + ".json")
            with _locked(map_path):
                mapping = _read_json(map_path)
            mapped_target = mapping.get("target") or {}
            identity_keys = ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id")
            if (mapping.get("schema_version") == 1 and mapping.get("status") == "observed"
                    and all(mapped_target.get(key) == target.get(key) for key in identity_keys)):
                actor_scope = {"kind": "dispatched_child", **{
                    key: mapped_target.get(key) for key in identity_keys[:-1]}}
        last_revision = binding.get("last_state_revision")
        last_view_sequence = binding.get("last_view_sequence", -1)
        if (current.get("session_id") == effect.get("session_id")
                and isinstance(receipt_revision, int) and not isinstance(receipt_revision, bool)
                and isinstance(last_revision, int) and not isinstance(last_revision, bool)
                and isinstance(receipt_view_sequence, int) and not isinstance(receipt_view_sequence, bool)
                and isinstance(last_view_sequence, int) and not isinstance(last_view_sequence, bool)
                and (receipt_revision, receipt_view_sequence) < (last_revision, last_view_sequence)):
            return
        if kind == "context_cleared":
            if (binding.get("foreground") or {}).get("session_id") == effect.get("session_id"):
                binding.update(status="stale", active_work=None, pending_dispatches=[])
        elif kind == "work_context_selected":
            foreground = {key: target[key] for key in
                          ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id")}
            binding.update(status="active", foreground=foreground,
                           active_work=foreground if effect.get("execution_status") == "running"
                           and target.get("attempt_id") else None,
                           pending_submission=foreground if effect.get("review_status") == "pending" else None,
                           pending_dispatches=[])
        elif kind in {"anchor_created", "epoch_changed", "foreground_activated"}:
            binding.update(status="active", foreground=target, active_work=None,
                           pending_submission=None, pending_dispatches=[])
        elif kind == "attempt_running":
            binding.update(status="active", foreground={key: target[key] for key in
                           ("session_id", "task_instance_id", "epoch")}, active_work=target)
        elif kind == "dispatch_prepared":
            pending = binding.setdefault("pending_dispatches", [])
            if not any(item.get("task_id") == target["task_id"] and item.get("attempt_id") == target["attempt_id"]
                       for item in pending if isinstance(item, dict)):
                pending.append(target)
                if len(pending) > 32:
                    del pending[:-32]
            binding.update(status="active", foreground={key: target[key] for key in
                           ("session_id", "task_instance_id", "epoch")})
        elif kind == "dispatch_bound":
            if binding.get("status") != "active" or not isinstance(binding.get("foreground"), dict):
                binding["status"] = "active"
                binding["foreground"] = {key: target[key] for key in
                                          ("session_id", "task_instance_id", "epoch")}
            for item in binding.get("pending_dispatches", []):
                if isinstance(item, dict) and item.get("task_id") == target["task_id"] and item.get("attempt_id") == target["attempt_id"]:
                    item["caller_declared_agent_name"] = effect.get("caller_declared_agent_name")
                    observed_digest = item.get("host_agent_id_digest")
                    declared = effect.get("caller_declared_agent_name")
                    if observed_digest and _identity(declared):
                        item["agent_binding_match"] = observed_digest == _digest("agent", scope[0], declared)
                        if not item["agent_binding_match"]:
                            map_path = _state_dir() / ("dispatch-" + item.get("dispatch_map_digest", "") + ".json")
                            if item.get("dispatch_map_digest"):
                                with _locked(map_path):
                                    mapping = _read_json(map_path)
                                    if mapping.get("target", {}).get("attempt_id") == item.get("attempt_id"):
                                        mapping.update(status="uncertain", reason_code="declared_agent_mismatch")
                                        _write_json(map_path, mapping)
            binding["pending_dispatches"] = [
                item for item in binding.get("pending_dispatches", [])
                if not (isinstance(item, dict) and item.get("task_id") == target["task_id"]
                        and item.get("attempt_id") == target["attempt_id"])
            ]
        elif kind in {"attempt_ended", "submission_pending_review", "submission_reviewed", "attempt_restarted"}:
            if binding.get("status") != "active" or not isinstance(binding.get("foreground"), dict):
                binding["status"] = "active"
                binding["foreground"] = {key: target[key] for key in
                                          ("session_id", "task_instance_id", "epoch")}
            current = binding.get("active_work") or {}
            same_active_task = all(current.get(key) == target.get(key)
                                   for key in ("session_id", "task_instance_id", "epoch", "task_id"))
            if same_active_task and (current.get("attempt_id") == target.get("attempt_id")
                                     or kind == "attempt_restarted"):
                binding["active_work"] = None
            binding["pending_dispatches"] = [
                item for item in binding.get("pending_dispatches", [])
                if not (isinstance(item, dict)
                        and all(item.get(key) == target.get(key)
                                for key in ("session_id", "task_instance_id", "epoch", "task_id"))
                        and (item.get("attempt_id") == target["attempt_id"]
                             or (kind == "attempt_restarted" and item.get("attempt_id") != target["attempt_id"])))
            ]
            # A returned host agent ID remains in the child map after it is
            # bound, so invalidate those maps when their source attempt ends.
            # A retry receipt refers to the new attempt; revoke every older
            # map for that task in the same instance/epoch.
            observed = binding.get("observed_dispatches", [])
            kept = []
            for item in observed if isinstance(observed, list) else []:
                if not isinstance(item, dict):
                    continue
                same_task = all(item.get(key) == target.get(key)
                                for key in ("session_id", "task_instance_id", "epoch", "task_id"))
                matches_attempt = item.get("attempt_id") == target.get("attempt_id")
                older_attempt = (kind == "attempt_restarted" and same_task
                                 and item.get("attempt_id") != target.get("attempt_id"))
                if same_task and (matches_attempt or older_attempt):
                    digest = item.get("dispatch_map_digest")
                    if isinstance(digest, str) and digest:
                        map_path = _state_dir() / ("dispatch-" + digest + ".json")
                        with _locked(map_path):
                            mapping = _read_json(map_path)
                            mapped_target = mapping.get("target") or {}
                            if (mapping.get("schema_version") == 1
                                    and all(mapped_target.get(key) == item.get(key)
                                            for key in ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id"))):
                                mapping.update(status="stale", reason_code="source_attempt_ended",
                                               updated_at=int(time.time()))
                                _write_json(map_path, mapping)
                else:
                    kept.append(item)
            binding["observed_dispatches"] = kept
            if kind == "submission_pending_review":
                binding["pending_submission"] = target
            elif kind == "submission_reviewed":
                pending = binding.get("pending_submission") or {}
                if pending.get("task_id") == target["task_id"] and pending.get("attempt_id") == target["attempt_id"]:
                    binding["pending_submission"] = None
            elif kind == "attempt_restarted" and effect.get("execution_status") == "running":
                pending_submission = binding.get("pending_submission") or {}
                if all(pending_submission.get(key) == target.get(key)
                       for key in ("session_id", "task_instance_id", "epoch", "task_id")):
                    binding["pending_submission"] = None
                binding["foreground"] = {key: target[key] for key in
                                          ("session_id", "task_instance_id", "epoch")}
                if effect.get("execution_mode") == "subagent":
                    pending = binding.setdefault("pending_dispatches", [])
                    pending.append(target)
                    if len(pending) > 32:
                        del pending[:-32]
                else:
                    binding["active_work"] = target
        if isinstance(actor_scope, dict) and actor_scope.get("kind") == "dispatched_child":
            actor_scope_keys = ("session_id", "task_instance_id", "epoch", "task_id")
            if all(actor_scope.get(key) == target.get(key) for key in actor_scope_keys):
                binding["actor_scope"] = actor_scope
                binding["foreground"] = {key: target[key] for key in
                                          ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id")}
            else:
                binding.pop("actor_scope", None)
        binding["binding_revision"] = int(binding.get("binding_revision", 0)) + 1
        binding["association_source"] = "host_observed_successful_task_progress_receipt"
        binding["verification"] = "task_progress_state_only"
        if isinstance(receipt_revision, int) and not isinstance(receipt_revision, bool):
            binding["last_state_revision"] = receipt_revision
        if isinstance(receipt_view_sequence, int) and not isinstance(receipt_view_sequence, bool):
            binding["last_view_sequence"] = receipt_view_sequence
        binding["updated_at"] = int(time.time())
        _write_json(path, binding)
    if kind in {"attempt_ended", "submission_pending_review", "submission_reviewed", "attempt_restarted"}:
        _revoke_child_dispatch(scope, target, kind)


def observe_dispatch(event: dict[str, Any], host_agent_id: str) -> bool:
    """Associate one unambiguous prepared dispatch with the host ID returned by spawn."""
    scope = _scope(event)
    if scope is None or not _identity(host_agent_id):
        return False
    path = _binding_path(scope)
    with _locked(path):
        binding = _read_json(path)
        pending = [item for item in binding.get("pending_dispatches", [])
                   if isinstance(item, dict) and item.get("attempt_id")]
        if len(pending) != 1:
            return False
        item = pending[0]
        map_path = _state_dir() / ("dispatch-" + _digest("dispatch", scope[0], host_agent_id) + ".json")
        mapping = {"schema_version": 1, "status": "observed", "host_session_digest": _digest("session", scope[0], ""),
                   "host_agent_digest": _digest("agent", scope[0], host_agent_id),
                   "parent_binding_digest": _digest("binding", *scope),
                   "target": {key: item.get(key) for key in
                              ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id")},
                   "association_source": "host_observed_successful_dispatch",
                   "verification": "task_progress_state_only", "updated_at": int(time.time())}
        declared = item.get("caller_declared_agent_name")
        if _identity(declared) and declared != host_agent_id:
            mapping.update(status="uncertain", reason_code="declared_agent_mismatch")
        with _locked(map_path):
            _write_json(map_path, mapping)
        if _identity(item.get("caller_declared_agent_name")):
            binding["pending_dispatches"] = [
                candidate for candidate in binding.get("pending_dispatches", [])
                if candidate is not item
            ]
        item["host_agent_id_digest"] = _digest("agent", scope[0], host_agent_id)
        item["dispatch_map_digest"] = _digest("dispatch", scope[0], host_agent_id)
        observed = binding.setdefault("observed_dispatches", [])
        observed.append({key: item.get(key) for key in
                         ("session_id", "task_instance_id", "epoch", "task_id", "attempt_id",
                          "dispatch_map_digest")})
        if len(observed) > 32:
            del observed[:-32]
        binding["binding_revision"] = int(binding.get("binding_revision", 0)) + 1
        _write_json(path, binding)
        return mapping["status"] == "observed"


def revoke_work_selection(event: dict[str, Any]) -> None:
    """A host clear forgets actor selection while preserving Task Progress data."""
    scope = _scope(event)
    if scope is None:
        return
    path = _binding_path(scope)
    with _locked(path):
        binding = _read_json(path)
        if binding.get("schema_version") != 1 or not isinstance(binding.get("foreground"), dict):
            return
        binding.update(status="needs_reselection", active_work=None, pending_dispatches=[])
        binding["binding_revision"] = int(binding.get("binding_revision", 0)) + 1
        binding["updated_at"] = int(time.time())
        _write_json(path, binding)


def read_context(event: dict[str, Any]) -> dict[str, Any]:
    """Read an explicit actor binding, or an exact observed spawn-to-agent mapping."""
    scope = _scope(event)
    if scope is None:
        return {"status": "unknown", "reason_code": "actor_unknown", "actor_resolution": "unknown"}
    binding = _read_json(_binding_path(scope))
    foreground = binding.get("foreground")
    active = binding.get("active_work")
    if binding.get("schema_version") == 1 and binding.get("status") == "stale":
        return {"status": "stale", "reason_code": "binding_stale", "actor_resolution": "unknown"}
    if binding.get("schema_version") == 1 and binding.get("status") == "needs_reselection":
        return {"status": "needs_reselection", "reason_code": "host_context_cleared",
                "foreground": foreground, "actor_resolution": "unknown"}
    if binding.get("schema_version") == 1 and binding.get("status") == "active" and isinstance(foreground, dict):
        return {"status": "bound", "reason_code": "receipt_observed", "foreground": foreground,
                "active_work": active if isinstance(active, dict) else None,
                "pending_submission": binding.get("pending_submission"),
                "actor_scope": binding.get("actor_scope"),
                "association_source": binding.get("association_source"), "verification": binding.get("verification")}
    # A child scope is associated only when its exact host ID was returned by a
    # successful dispatch while one prepared attempt was unambiguous.
    map_path = _state_dir() / ("dispatch-" + _digest("dispatch", scope[0], scope[1]) + ".json")
    mapping = _read_json(map_path)
    if mapping.get("schema_version") == 1 and mapping.get("status") == "observed" and isinstance(mapping.get("target"), dict):
        target = mapping["target"]
        return {"status": "bound", "reason_code": "dispatch_observed", "foreground": mapping["target"],
                "active_work": target, "pending_submission": None,
                "actor_scope": {"kind": "dispatched_child", **{
                    key: target.get(key) for key in ("session_id", "task_instance_id", "epoch", "task_id")}},
                "association_source": mapping.get("association_source"),
                "verification": mapping.get("verification")}
    return {"status": binding.get("status", "unbound"),
            "reason_code": "binding_unavailable" if not binding else "binding_stale",
            "actor_resolution": "unknown"}


@lru_cache(maxsize=1)
def _load_state_reader():
    """Load the local state projection only when a hook actually needs it."""
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("task_progress_hook_state_core", server_path)
    if spec is None or spec.loader is None:
        raise ImportError("Task Progress state reader is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reader = getattr(module, "read_hook_snapshot", None)
    if not callable(reader):
        raise ImportError("Task Progress state reader is unsupported.")
    return reader


def read_snapshot(
    session_id: str,
    task_instance_id: str,
    epoch: int,
    task_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Read explicit plugin state; unavailable or invalid state fails open."""
    try:
        return _load_state_reader()(session_id, task_instance_id, epoch, task_id, attempt_id)
    except Exception:  # Hook guidance is advisory; state read failures must not block work.
        return {
            "snapshot_version": 1,
            "status": "unknown",
            "reason_code": "snapshot_unavailable",
            "actor_resolution": "unknown",
            "verification": "server_state_only",
        }
