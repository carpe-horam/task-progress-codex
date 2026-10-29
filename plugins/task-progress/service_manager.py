#!/usr/bin/env python3
"""Install and inspect the user's single Task Progress HTTP service.

No operation kills a PID. Service lifecycle changes use only our validated
user service definition, and installed releases survive plugin cache replacement.
"""
from __future__ import annotations

import argparse
import base64
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
import xml.etree.ElementTree as ET
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
        if hasattr(os, "fchmod"):
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
                 platform=None, runner=None, health_timeout=8.0, lifecycle_timeout=20.0):
        self.home = Path(home or Path.home()).expanduser().resolve()
        self.plugin_root = Path(plugin_root or Path(__file__).parent).resolve()
        self.python = str(Path(python_executable or sys.executable).resolve())
        self.platform = platform or sys.platform
        self.runner = runner or subprocess.run
        self.health_timeout = health_timeout
        self.lifecycle_timeout = lifecycle_timeout
        self.state_dir = self.home / ".cache/task-progress/service"
        self.credentials = self.state_dir / "http-headers.json"
        self.releases = self.home / "Library/Application Support/Task Progress/releases"
        self.plist = self.home / "Library/LaunchAgents" / f"{LABEL}.plist"
        if self.platform == "linux":
            self.releases = self.home / ".local/share/task-progress/releases"
            self.plist = self.home / ".config/systemd/user" / f"{LABEL}.service"
        elif self.platform == "win32":
            self.releases = self.home / "AppData/Local/Task Progress/releases"
            self.plist = self.state_dir / "scheduled-task.xml"
        self._windows_sid = None
        self.domain = f"gui/{_uid()}"
        self.job = f"{self.domain}/{LABEL}"

    def _supported(self):
        if self.platform not in ("darwin", "linux", "win32"):
            raise ServiceError("Automatic service management is unsupported on this platform")

    def _command(self, args, check=True, encoding=None):
        try:
            result = self.runner(args, capture_output=True, text=True, timeout=30, encoding=encoding)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ServiceError(f"Unable to run user service manager: {args[0]}") from error
        if check and result.returncode:
            raise ServiceError(f"{Path(args[0]).name} user service operation failed (exit {result.returncode})")
        return result

    @staticmethod
    def _ps_literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    def _powershell(self, script):
        encoded = base64.b64encode(("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + script).encode("utf-16-le")).decode("ascii")
        return self._command(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], encoding="utf-8")

    def _sid(self):
        if self._windows_sid is None:
            self._windows_sid = self._powershell("[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value").stdout.strip()
            if not re.fullmatch(r"S-1-[0-9-]+", self._windows_sid):
                raise ServiceError("Unable to identify the current Windows user")
        return self._windows_sid

    def _systemctl(self, *args, check=True):
        return self._command(["systemctl", "--user", *args], check=check)

    def _task(self):
        # Query only this user's task. The SID in its name also avoids collisions
        # with another logged-in user's Task Progress installation.
        return LABEL + "-" + self._sid()

    def _windows_registered(self):
        name = self._ps_literal(self._task())
        result = self._powershell(
            f"$t=Get-ScheduledTask -TaskPath '\\' -ErrorAction Stop | Where-Object {{$_.TaskName -eq {name}}}; "
            "if($t){ @{xml=(Export-ScheduledTask -InputObject $t); state=[string]$t.State} | ConvertTo-Json -Compress }")
        if not result.stdout.strip():
            return None
        try:
            record = json.loads(result.stdout)
            self._parse_definition(record["xml"], registered=True)
            return record
        except (ValueError, KeyError, TypeError, ET.ParseError) as error:
            raise ServiceError("Refusing a scheduled task not managed by Task Progress") from error

    @staticmethod
    def _unit_quote(value):
        # systemd performs percent specifier expansion even inside quotes.
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$').replace('\n', '\\n').replace('\r', '\\r') + '"'

    def _definition_bytes(self, definition):
        if self.platform == "darwin":
            return plistlib.dumps(definition)
        metadata = json.dumps(definition, sort_keys=True, separators=(",", ":"))
        args = definition["ProgramArguments"]
        if self.platform == "linux":
            command = " ".join(self._unit_quote(arg) for arg in args)
            return (f"# TaskProgressDefinition={metadata}\n[Unit]\nDescription=Task Progress MCP service\n"
                    f"[Service]\nType=simple\nExecStart={command}\nRestart=on-failure\nRestartSec=5\n"
                    "UMask=0077\n[Install]\nWantedBy=default.target\n").encode("utf-8")
        namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        ET.register_namespace("", namespace)
        def element(parent, tag, value=None, **attrs):
            child = ET.SubElement(parent, "{" + namespace + "}" + tag, attrs)
            if value is not None:
                child.text = str(value)
            return child
        root = ET.Element("{" + namespace + "}Task", {"version": "1.2"})
        registration = element(root, "RegistrationInfo")
        element(registration, "Description", metadata)
        triggers = element(root, "Triggers")
        logon = element(triggers, "LogonTrigger")
        element(logon, "Enabled", "true")
        element(logon, "UserId", self._sid())
        principals = element(root, "Principals")
        principal = element(principals, "Principal", id="Author")
        element(principal, "UserId", self._sid())
        element(principal, "LogonType", "InteractiveToken")
        element(principal, "RunLevel", "LeastPrivilege")
        settings = element(root, "Settings")
        element(settings, "MultipleInstancesPolicy", "IgnoreNew")
        element(settings, "DisallowStartIfOnBatteries", "false")
        element(settings, "StopIfGoingOnBatteries", "false")
        element(settings, "ExecutionTimeLimit", "PT0S")
        restart = element(settings, "RestartOnFailure")
        element(restart, "Interval", "PT1M")
        element(restart, "Count", "999")
        actions = element(root, "Actions", Context="Author")
        action = element(actions, "Exec")
        element(action, "Command", args[0])
        element(action, "Arguments", subprocess.list2cmdline(args[1:]))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def _parse_definition(self, raw, registered=False):
        if self.platform == "darwin":
            return plistlib.loads(raw)
        if self.platform == "linux":
            first = raw.decode("utf-8").splitlines()[0]
            data = json.loads(first.removeprefix("# TaskProgressDefinition="))
            if self._definition_bytes(data) != raw:
                raise ValueError("Unit contents do not match their managed definition")
            return data
        root = ET.fromstring(raw)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        data = json.loads(root.findtext("t:RegistrationInfo/t:Description", namespaces=ns))
        args = data["ProgramArguments"]
        actions = root.findall("t:Actions/t:Exec", ns)
        principal = root.find("t:Principals/t:Principal", ns)
        if (len(root.findall("t:Actions/*", ns)) != 1 or len(actions) != 1 or principal is None or
                principal.findtext("t:UserId", namespaces=ns) != self._sid() or
                principal.findtext("t:LogonType", namespaces=ns) != "InteractiveToken" or
                principal.findtext("t:RunLevel", namespaces=ns) != "LeastPrivilege" or
                actions[0].findtext("t:Command", namespaces=ns) != args[0] or
                actions[0].findtext("t:Arguments", namespaces=ns) != subprocess.list2cmdline(args[1:])):
            raise ValueError("Scheduled task actions or principal differ from managed definition")
        if not registered and self._definition_bytes(data) != raw:
            raise ValueError("Scheduled task contents differ from managed definition")
        self._validate_definition(data)
        return data

    def _refresh_definition(self):
        if self.platform == "linux":
            self._systemctl("daemon-reload")
        elif self.platform == "win32" and self.plist.exists():
            # Registration is separate from Start so rollback restores an old
            # stopped task too, without accidentally starting it.
            self._windows_registered()
            name, path = self._ps_literal(self._task()), self._ps_literal(self.plist)
            self._powershell(f"Register-ScheduledTask -TaskName {name} -TaskPath '\\' -Xml ([IO.File]::ReadAllText({path})) -Force | Out-Null")

    def _start_native(self):
        if self.platform == "darwin":
            return self._launchctl("bootstrap", self.domain, str(self.plist), check=False)
        if self.platform == "linux":
            self._systemctl("enable", self.plist.name)
            return self._systemctl("start", self.plist.name, check=False)
        return self._powershell(f"Start-ScheduledTask -TaskName {self._ps_literal(self._task())} -TaskPath '\\'")

    def _stop_native(self):
        if self.platform == "darwin":
            self._launchctl("bootout", self.job)
        elif self.platform == "linux":
            self._systemctl("stop", self.plist.name)
        else:
            self._windows_registered()  # Refuse replacement actions before stopping.
            self._powershell(f"Stop-ScheduledTask -TaskName {self._ps_literal(self._task())} -TaskPath '\\'")

    def _remove_native(self):
        if self.platform == "linux":
            self._systemctl("disable", self.plist.name)
        elif self.platform == "win32" and self._windows_registered():
            self._powershell(f"Unregister-ScheduledTask -TaskName {self._ps_literal(self._task())} -TaskPath '\\' -Confirm:$false")

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
        private_directory(self.state_dir)
        lock = self.state_dir / "manager.lock"
        if lock.is_symlink():
            raise ServiceError("Refusing symlink manager lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                deadline = time.monotonic() + self.lifecycle_timeout
                while True:
                    os.lseek(fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ServiceError("Timed out waiting for service management lock")
                        time.sleep(0.05)
            else:
                import fcntl
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
        if self.platform == "darwin":
            return self._launchctl("print", self.job, check=False).returncode == 0
        if self.platform == "win32":
            task = self._windows_registered()
            return bool(task and task["state"] in ("Running", "Queued"))
        result = self._systemctl("show", self.plist.name, "--property=ActiveState", "--property=FragmentPath", "--property=DropInPaths", check=False)
        if result.returncode:
            raise ServiceError("Unable to contact systemd user manager; a login session with systemd --user is required")
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        fragment = fields.get("FragmentPath", "")
        if fields.get("DropInPaths") or (fragment and Path(fragment).resolve() != self.plist.resolve()):
            raise ServiceError("Refusing a systemd unit not managed by Task Progress")
        return fields.get("ActiveState") in ("active", "activating", "deactivating", "reloading")

    def _wait_unloaded(self, deadline):
        # bootout can return while launchd and the HTTP listener are still
        # exiting. Do not race either resource with a replacement bootstrap.
        while True:
            loaded = self._loaded()
            busy = self._port_busy()
            if not loaded and not busy:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceError("Timed out waiting for the managed job to unload and its port to become free")
            time.sleep(min(0.1, remaining))

    def _bootstrap_owned(self, expected, deadline):
        # Exit 5 can be transient during launchd namespace cleanup, but is not
        # proof of that cause. Retry only this verified definition, only while
        # both resources remain vacant. Never bootout a new occupant to retry.
        delay = 0.25
        while True:
            if time.monotonic() >= deadline:
                raise ServiceError("Timed out before the managed bootstrap could complete")
            if self._owned_plist() != expected:
                raise ServiceError("Managed service definition changed before bootstrap")
            if self._loaded() or self._port_busy():
                raise ServiceError("Refusing bootstrap while the job label or port is occupied")
            result = self._start_native()
            if result.returncode == 0:
                return
            remaining = deadline - time.monotonic()
            if self.platform != "darwin" or result.returncode != 5 or remaining <= 0:
                raise ServiceError(f"User service bootstrap failed (exit {result.returncode})")
            if self._loaded() or self._port_busy():
                raise ServiceError("launchctl bootstrap failed (exit 5); job label or port became occupied")
            time.sleep(min(delay, remaining))
            if time.monotonic() >= deadline:
                raise ServiceError("launchctl bootstrap failed (exit 5; bounded retry expired)")
            delay = min(delay * 2, 2.0)

    def _validate_definition(self, data):
        args = data["ProgramArguments"]
        runtime = Path(args[1])
        if not (data.get("TaskProgressManagedBy") == OWNER and
                data.get("Label") == LABEL and len(args) == 6 and
                Path(args[0]).is_absolute() and runtime.is_absolute() and
                runtime.name == "shared_service.py" and
                runtime.parent.parent.resolve() == self.releases.resolve() and
                args[2:] == ["--port", str(PORT), "--state-dir", str(self.state_dir)]):
            raise ValueError("Unmanaged runtime definition")

    def _owned_plist(self):
        if self.plist.is_symlink():
            raise ServiceError("Refusing symlink service definition")
        if not self.plist.exists():
            return None
        try:
            if _uid() is not None and self.plist.stat().st_uid != _uid():
                raise ValueError("Definition belongs to another user")
            data = self._parse_definition(self.plist.read_bytes())
            self._validate_definition(data)
        except (OSError, ValueError, KeyError, TypeError, IndexError, ET.ParseError, plistlib.InvalidFileException) as error:
            raise ServiceError("Refusing to replace or stop a service not managed by Task Progress") from error
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
        required = ["server.py", "app.html", "shared_service.py", "service_manager.py", "legacy_history.py",
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
        self._supported()
        self.prepare()
        with self._management_lock():
            previous = self._owned_plist()
            loaded = self._loaded()
            if loaded and previous is None:
                raise ServiceError("An unmanaged user service already uses the Task Progress label")
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
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                os.close(fd)
            old_bytes = self.plist.read_bytes() if previous else None
            changed_job = False
            written = False
            started = False
            definition = self._definition(runtime)
            deadline = time.monotonic() + self.lifecycle_timeout
            try:
                if loaded:
                    self._stop_native()
                    changed_job = True
                    self._wait_unloaded(deadline)
                atomic_write(self.plist, self._definition_bytes(definition))
                written = True
                self._refresh_definition()
                self._bootstrap_owned(definition, deadline)
                started = True
                changed_job = True
                health = self._wait_health(runtime)
                return {"ok": True, "changed": True, "release": str(release), "health": health}
            except Exception as error:
                if not changed_job and not written:
                    raise ServiceError(f"Installation stopped before changing the previous job: {error}") from error
                rollback_error = None
                try:
                    rollback_deadline = time.monotonic() + self.lifecycle_timeout
                    if (started or self.platform != "darwin") and self._loaded():
                        if self._owned_plist() != definition:
                            raise ServiceError("Managed service definition changed before rollback")
                        self._stop_native()
                    if self._owned_plist() != (definition if written else previous):
                        raise ServiceError("Managed service definition changed during rollback")
                    if old_bytes is None:
                        self._remove_native()
                        self.plist.unlink(missing_ok=True)
                    else:
                        atomic_write(self.plist, old_bytes)
                    if self.platform == "win32":
                        self._wait_unloaded(rollback_deadline)
                    self._refresh_definition()
                    self._wait_unloaded(rollback_deadline)
                    if old_bytes is not None and loaded:
                        self._bootstrap_owned(previous, rollback_deadline)
                        self._wait_health(previous["ProgramArguments"][1])
                except Exception as failure:
                    rollback_error = str(failure)
                message = f"Installation failed: {error}. "
                message += (f"Rollback also failed: {rollback_error}" if rollback_error else "Previous installation restored.")
                raise ServiceError(message) from error

    def status(self):
        self._supported()
        definition = self._owned_plist()
        loaded = self._loaded()
        health = self.health()
        verified = bool(definition and loaded and health and
                        Path(health["runtime_path"]).resolve() == Path(definition["ProgramArguments"][1]).resolve())
        return {"ok": True, "installed": bool(definition), "loaded": loaded,
                "healthy": verified, "health": health if verified else None,
                "plist": str(self.plist), "service_manager": {"darwin": "launchd", "linux": "systemd-user", "win32": "windows-task-scheduler"}[self.platform], "port": PORT}

    def stop(self):
        self._supported()
        with self._management_lock():
            definition = self._owned_plist()
            stopped = False
            if definition and self._loaded():
                self._stop_native()
                stopped = True
            return {"ok": True, "stopped": stopped, "data_preserved": True}

    def uninstall(self):
        self._supported()
        with self._management_lock():
            definition = self._owned_plist()
            if definition:
                if self._loaded():
                    self._stop_native()
                self._wait_unloaded(time.monotonic() + self.lifecycle_timeout)
                self._remove_native()
                self.plist.unlink()
                self._refresh_definition()
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
