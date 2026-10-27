#!/usr/bin/env python3
"""Install and inspect the user's single Task Progress HTTP service.

No operation kills a PID. Service lifecycle changes use only our validated
LaunchAgent label, and installed releases survive plugin cache replacement.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

PORT = 53421
LABEL = "io.task-progress.mcp"
OWNER = "task-progress-service-manager-v1"


def _uid():
    return os.getuid() if hasattr(os, "getuid") else None


class ServiceError(RuntimeError):
    """A safe, user-readable installation or lifecycle failure."""


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ServiceError(f"Refusing symlink directory: {path}")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not path.is_dir() or (_uid() is not None and path.stat().st_uid != _uid()):
        raise ServiceError(f"Directory is not owned by this user: {path}")
    path.chmod(0o700)
    return path


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise ServiceError(f"Refusing symlink file: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".task-progress-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ServiceManager:
    def __init__(self, home=None, plugin_root=None, python_executable=None,
                 platform=None, runner=None, health_timeout=8.0):
        self.home = Path(home or Path.home()).expanduser().resolve()
        self.plugin_root = Path(plugin_root or Path(__file__).parent).resolve()
        self.python = str(Path(python_executable or sys.executable).resolve())
        self.platform = platform or sys.platform
        self.runner = runner or subprocess.run
        self.health_timeout = health_timeout
        self.state_dir = self.home / ".cache/task-progress/service"
        self.credentials = self.state_dir / "http-headers.json"
        self.releases = self.home / "Library/Application Support/Task Progress/releases"
        self.plist = self.home / "Library/LaunchAgents" / f"{LABEL}.plist"
        self.domain = f"gui/{_uid()}"
        self.job = f"{self.domain}/{LABEL}"

    def _mac_only(self):
        if self.platform != "darwin":
            raise ServiceError("Automatic service management is unsupported on this platform; use prepare and foreground serve.")

    def _headers(self):
        if self.credentials.is_symlink():
            raise ServiceError("Refusing symlink credential file")
        try:
            if _uid() is not None and self.credentials.stat().st_uid != _uid():
                raise ServiceError("Credential file is not owned by this user")
            headers = json.loads(self.credentials.read_text(encoding="utf-8"))
            authorization = headers.get("Authorization", "")
            if (not isinstance(authorization, str) or
                    not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43,}", authorization)):
                raise ValueError("invalid bearer credential")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ServiceError("Missing or invalid service credentials; run prepare") from error
        return {"Authorization": authorization}

    def prepare(self):
        private_directory(self.state_dir)
        # Publish a fully written file with link's exclusive-create semantics.
        # Concurrent prepare calls cannot rotate or observe a partial token.
        fd, temporary = tempfile.mkstemp(prefix=".headers-", dir=self.state_dir)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"Authorization": "Bearer " + secrets.token_urlsafe(48)}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self.credentials)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
        self._headers()
        self.credentials.chmod(0o600)
        return {"ok": True, "state_dir": str(self.state_dir),
                "headers_file": str(self.credentials), "port": PORT}

    @contextmanager
    def _management_lock(self):
        import fcntl
        private_directory(self.state_dir)
        lock = self.state_dir / "manager.lock"
        if lock.is_symlink():
            raise ServiceError("Refusing symlink manager lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _launchctl(self, *args, check=True):
        result = self.runner(["/bin/launchctl", *args], capture_output=True, text=True)
        if check and result.returncode:
            # The command contains paths and a fixed label, never credentials.
            raise ServiceError(f"launchctl {args[0]} failed (exit {result.returncode})")
        return result

    def _loaded(self):
        return self._launchctl("print", self.job, check=False).returncode == 0

    def _owned_plist(self):
        if self.plist.is_symlink():
            raise ServiceError("Refusing symlink LaunchAgent")
        if not self.plist.exists():
            return None
        try:
            data = plistlib.loads(self.plist.read_bytes())
            args = data["ProgramArguments"]
            runtime = Path(args[1])
            valid = (self.plist.stat().st_uid == os.getuid() and
                     data.get("TaskProgressManagedBy") == OWNER and
                     data.get("Label") == LABEL and len(args) == 6 and
                     Path(args[0]).is_absolute() and runtime.is_absolute() and
                     runtime.name == "shared_service.py" and
                     runtime.parent.parent.resolve() == self.releases.resolve() and
                     args[2:] == ["--port", str(PORT), "--state-dir", str(self.state_dir)])
        except (OSError, ValueError, KeyError, TypeError, IndexError, plistlib.InvalidFileException):
            valid = False
        if not valid:
            raise ServiceError("Refusing to replace or stop a LaunchAgent not managed by Task Progress")
        return data

    def health(self):
        """Authenticated direct loopback request; never uses proxies or redirects."""
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=0.5)
        try:
            connection.request("GET", "/healthz", headers=self._headers())
            response = connection.getresponse()
            payload = response.read(65537)
            if response.status != 200 or len(payload) > 65536:
                return None
            data = json.loads(payload)
            if (not isinstance(data, dict) or data.get("service") != "task-progress" or
                    data.get("transport") != "streamable-http" or data.get("port") != PORT or
                    not isinstance(data.get("pid"), int) or not data.get("instance_id") or
                    not isinstance(data.get("runtime_path"), str) or not data.get("version")):
                return None
            return data
        except (OSError, ValueError, http.client.HTTPException, ServiceError):
            return None
        finally:
            connection.close()

    def _wait_health(self, runtime):
        deadline = time.monotonic() + self.health_timeout
        while True:
            data = self.health()
            if data and Path(data["runtime_path"]).resolve() == Path(runtime).resolve():
                return data
            if time.monotonic() >= deadline:
                raise ServiceError("Service failed authenticated health verification for the installed release")
            time.sleep(0.05)

    def _port_busy(self):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
                return True
        except ConnectionRefusedError:
            return False
        except OSError as error:
            raise ServiceError("Unable to inspect the local service port") from error

    def _release(self):
        required = ["server.py", "app.html", "shared_service.py", "service_manager.py",
                    ".codex-plugin/plugin.json"]
        files = [self.plugin_root / name for name in required]
        assets = self.plugin_root / "assets"
        if assets.exists():
            files.extend(path for path in assets.rglob("*") if path.is_file())
        digest = hashlib.sha256()
        contents = []
        for path in sorted(files):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.plugin_root):
                raise ServiceError(f"Missing or unsafe runtime source: {path.name}")
            relative = path.relative_to(self.plugin_root)
            data = path.read_bytes()
            digest.update(str(relative).encode() + b"\0" + data + b"\0")
            contents.append((relative, data))
        manifest = json.loads((self.plugin_root / ".codex-plugin/plugin.json").read_text())
        version = manifest.get("version", "")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", version):
            raise ServiceError("Invalid plugin version for release directory")
        private_directory(self.releases.parent)
        private_directory(self.releases)
        release = self.releases / f"{version}-{digest.hexdigest()[:20]}"
        if release.exists():
            if release.is_symlink() or any((release / relative).is_symlink() or
                    not (release / relative).is_file() or
                    (release / relative).read_bytes() != data for relative, data in contents):
                raise ServiceError("Installed release content differs from its digest; refusing reuse")
            return release
        temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=self.releases))
        try:
            for relative, data in contents:
                target = temporary / relative
                private_directory(target.parent)
                target.write_bytes(data)
                target.chmod(0o600)
            temporary.rename(release)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return release

    def _definition(self, runtime):
        return {"Label": LABEL, "TaskProgressManagedBy": OWNER,
                "ProgramArguments": [self.python, str(runtime), "--port", str(PORT),
                                     "--state-dir", str(self.state_dir)],
                "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 5,
                "ProcessType": "Background", "Umask": 0o077,
                "StandardOutPath": str(self.state_dir / "service.stdout.log"),
                "StandardErrorPath": str(self.state_dir / "service.stderr.log")}

    def install(self):
        self._mac_only()
        self.prepare()
        with self._management_lock():
            previous = self._owned_plist()
            loaded = self._loaded()
            if loaded and previous is None:
                raise ServiceError("An unmanaged launchd job already uses the Task Progress label")
            live = self.health()
            if self._port_busy() and (not loaded or previous is None or not live or
                    Path(live["runtime_path"]).resolve() != Path(previous["ProgramArguments"][1]).resolve()):
                raise ServiceError(f"Port {PORT} is occupied by an unverified service; no process was stopped")
            release = self._release()
            runtime = release / "shared_service.py"
            if previous and previous == self._definition(runtime) and live and loaded:
                return {"ok": True, "changed": False, "release": str(release), "health": live}
            private_directory(self.plist.parent)
            for name in ("service.stdout.log", "service.stderr.log"):
                log = self.state_dir / name
                if log.is_symlink():
                    raise ServiceError("Refusing symlink service log")
                fd = os.open(log, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                os.fchmod(fd, 0o600)
                os.close(fd)
            old_bytes = self.plist.read_bytes() if previous else None
            changed_job = False
            written = False
            try:
                if loaded:
                    self._launchctl("bootout", self.job)
                    changed_job = True
                atomic_write(self.plist, plistlib.dumps(self._definition(runtime)))
                written = True
                self._launchctl("bootstrap", self.domain, str(self.plist))
                changed_job = True
                health = self._wait_health(runtime)
                return {"ok": True, "changed": True, "release": str(release), "health": health}
            except Exception as error:
                if not changed_job and not written:
                    raise ServiceError(f"Installation stopped before changing the previous job: {error}") from error
                rollback_error = None
                try:
                    if written and self._loaded():
                        self._launchctl("bootout", self.job)
                    if old_bytes is None:
                        self.plist.unlink(missing_ok=True)
                    else:
                        atomic_write(self.plist, old_bytes)
                        if loaded:
                            self._launchctl("bootstrap", self.domain, str(self.plist))
                            self._wait_health(previous["ProgramArguments"][1])
                except Exception as failure:
                    rollback_error = str(failure)
                message = f"Installation failed: {error}. "
                message += (f"Rollback also failed: {rollback_error}" if rollback_error else "Previous installation restored.")
                raise ServiceError(message) from error

    def status(self):
        self._mac_only()
        definition = self._owned_plist()
        loaded = self._loaded()
        health = self.health()
        verified = bool(definition and loaded and health and
                        Path(health["runtime_path"]).resolve() == Path(definition["ProgramArguments"][1]).resolve())
        return {"ok": True, "installed": bool(definition), "loaded": loaded,
                "healthy": verified, "health": health if verified else None,
                "plist": str(self.plist), "port": PORT}

    def stop(self):
        self._mac_only()
        with self._management_lock():
            definition = self._owned_plist()
            stopped = False
            if definition and self._loaded():
                self._launchctl("bootout", self.job)
                stopped = True
            return {"ok": True, "stopped": stopped, "data_preserved": True}

    def uninstall(self):
        self._mac_only()
        with self._management_lock():
            definition = self._owned_plist()
            if definition:
                if self._loaded():
                    self._launchctl("bootout", self.job)
                self.plist.unlink()
            return {"ok": True, "uninstalled": bool(definition), "data_preserved": True,
                    "releases_preserved": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "install", "status", "stop", "uninstall"))
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON (no credentials)")
    parser.add_argument("--home", type=Path, help="Explicit user home, primarily for isolated validation")
    args = parser.parse_args(argv)
    try:
        result = getattr(ServiceManager(home=args.home), args.operation)()
    except (ServiceError, OSError, ValueError) as error:
        result = {"ok": False, "error": str(error)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["ok"]:
        print(f"Task Progress {args.operation}: complete")
    else:
        print(result["error"], file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
