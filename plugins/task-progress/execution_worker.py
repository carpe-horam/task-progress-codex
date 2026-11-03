#!/usr/bin/env python3
"""Durable command supervisor. Only executes a previously persisted task binding."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime
import uuid


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(path):
    path = Path(path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    # The atomic claim also prevents a retried start receipt from executing a
    # command twice if the MCP service stopped during process launch.
    try:
        fd = os.open(str(path) + ".claim", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as claim:
        claim.write(str(os.getpid()))
    receipt = {"execution_id": spec["execution_id"], "started_at": now()}
    try:
        with path.with_suffix(".log").open("ab") as output:
            os.chmod(path.with_suffix(".log"), 0o600)
            process = subprocess.Popen(spec["command"], cwd=spec["cwd"],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=output)
            code = process.wait()
        receipt.update(status="succeeded" if code == 0 else "failed", exit_code=code,
                       summary=f"托管命令结束，exit_code={code}。")
    except Exception as exc:
        receipt.update(status="failed", exit_code=None, summary=f"托管命令启动/执行失败：{exc}")
    receipt["finished_at"] = now()
    write(path.with_suffix(".result.json"), receipt)
    # The receipt is authoritative even if the service was restarted or this
    # best-effort immediate reconciliation fails. Reads reconcile it again.
    try:
        module_spec = importlib.util.spec_from_file_location("execution_worker_server", Path(__file__).with_name("server.py"))
        server = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(server)
        server.SESSION_DIR = path.parent.parent
        server._reconcile_executions(spec["session_id"])
    except Exception:
        pass


if __name__ == "__main__":
    main(sys.argv[1])
