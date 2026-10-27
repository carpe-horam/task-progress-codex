"""Isolated LaunchAgent orchestration tests; never touch the real user job."""
import concurrent.futures
import contextlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("service_manager", PLUGIN / "service_manager.py")
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class LocalService:
    def __init__(self, port, headers, runtime, version="fixture", service="task-progress"):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != headers["Authorization"]:
                    self.send_response(401)
                    self.end_headers()
                    return
                body = json.dumps({"service": service, "version": version, "pid": os.getpid(),
                                   "instance_id": "fixture-instance", "runtime_path": str(runtime),
                                   "transport": "streamable-http", "port": manager.PORT}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class FakeLaunchctl:
    def __init__(self):
        self.calls = []
        self.service = None
        self.fail_next_bootstrap = False
        self.fail_next_bootout = False
        self.bad_next_health = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        action = args[1]
        code = 0
        if action == "print":
            code = 0 if self.service else 113
        elif action == "bootout":
            if self.fail_next_bootout:
                self.fail_next_bootout = False
                return subprocess.CompletedProcess(args, 5, "", "fixture failure")
            if self.service:
                self.service.close()
                self.service = None
            else:
                code = 113
        elif action == "bootstrap":
            if self.fail_next_bootstrap:
                self.fail_next_bootstrap = False
                return subprocess.CompletedProcess(args, 5, "", "fixture failure")
            definition = plistlib.loads(Path(args[3]).read_bytes())
            runtime = definition["ProgramArguments"][1]
            state = Path(definition["ProgramArguments"][5])
            headers = json.loads((state / "http-headers.json").read_text())
            if self.bad_next_health:
                runtime = "/different/runtime/shared_service.py"
                self.bad_next_health = False
            self.service = LocalService(manager.PORT, headers, runtime)
        else:
            raise AssertionError(f"Unexpected lifecycle operation: {args}")
        return subprocess.CompletedProcess(args, code, "", "")

    def close(self):
        if self.service:
            self.service.close()
            self.service = None


class ServiceManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="Task Progress home with spaces ")
        self.home = Path(self.temporary.name) / "user home"
        self.home.mkdir()
        self.source = Path(self.temporary.name) / "plugin source"
        (self.source / ".codex-plugin").mkdir(parents=True)
        (self.source / ".codex-plugin/plugin.json").write_text('{"version":"0.2.0"}')
        for name in ("shared_service.py", "server.py", "service_manager.py", "app.html"):
            (self.source / name).write_text(name + " fixture")
        self.runner = FakeLaunchctl()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.port_patch = patch.object(manager, "PORT", port)
        self.port_patch.start()
        self.service = manager.ServiceManager(home=self.home, plugin_root=self.source,
                                              platform="darwin", runner=self.runner, health_timeout=0.15)

    def tearDown(self):
        self.runner.close()
        self.port_patch.stop()
        self.temporary.cleanup()

    def test_prepare_is_private_idempotent_and_never_returns_token(self):
        result = self.service.prepare()
        token_file = self.service.credentials.read_bytes()
        self.assertEqual(self.service.prepare(), result)
        self.assertEqual(self.service.credentials.read_bytes(), token_file)
        self.assertEqual(self.service.state_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.service.credentials.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("Bearer", json.dumps(result))
        self.assertGreaterEqual(len(json.loads(token_file)["Authorization"].removeprefix("Bearer ")), 43)

    def test_concurrent_prepare_reuses_fully_published_credentials(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.service.prepare(), range(12)))
        self.assertTrue(all(value == results[0] for value in results))

    def test_install_uses_stable_absolute_paths_and_reuses_same_release(self):
        result = self.service.install()
        definition = plistlib.loads(self.service.plist.read_bytes())
        self.assertTrue(result["changed"])
        self.assertTrue(Path(result["release"]).is_relative_to(self.service.releases))
        self.assertIn("Application Support/Task Progress", result["release"])
        self.assertEqual(definition["ProgramArguments"][1], result["release"] + "/shared_service.py")
        self.assertEqual(definition["ProgramArguments"][0], str(Path(os.sys.executable).resolve()))
        self.assertTrue(definition["RunAtLoad"] and definition["KeepAlive"])
        count = len(self.runner.calls)
        again = self.service.install()
        self.assertFalse(again["changed"])
        self.assertFalse(any(call[1] in ("bootstrap", "bootout") for call in self.runner.calls[count:]))
        self.assertEqual(self.service.plist.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.service.status()["healthy"])

    def test_port_conflict_never_stops_unknown_process(self):
        self.service.prepare()
        other = LocalService(manager.PORT, self.service._headers(), "/unknown/shared_service.py", service="other")
        try:
            with self.assertRaisesRegex(manager.ServiceError, "occupied"):
                self.service.install()
            self.assertFalse(any(call[1] in ("bootstrap", "bootout") for call in self.runner.calls))
            self.assertFalse(self.service.plist.exists())
        finally:
            other.close()

    def test_upgrade_bootstrap_failure_restores_previous_healthy_job(self):
        first = self.service.install()
        previous = self.service.plist.read_bytes()
        (self.source / "app.html").write_text("changed release")
        self.runner.fail_next_bootstrap = True
        with self.assertRaisesRegex(manager.ServiceError, "Previous installation restored"):
            self.service.install()
        self.assertEqual(self.service.plist.read_bytes(), previous)
        self.assertEqual(self.service.status()["health"]["runtime_path"], first["release"] + "/shared_service.py")
        self.assertEqual(len(list(self.service.releases.iterdir())), 2)

    def test_wrong_runtime_health_is_failure_and_rolls_back(self):
        first = self.service.install()
        (self.source / "app.html").write_text("another release")
        self.runner.bad_next_health = True
        with self.assertRaisesRegex(manager.ServiceError, "health verification"):
            self.service.install()
        self.assertEqual(self.service.status()["health"]["runtime_path"], first["release"] + "/shared_service.py")

    def test_failed_stop_during_upgrade_does_not_bootstrap_over_running_job(self):
        first = self.service.install()
        previous = self.service.plist.read_bytes()
        (self.source / "app.html").write_text("another release")
        self.runner.fail_next_bootout = True
        count = len(self.runner.calls)
        with self.assertRaisesRegex(manager.ServiceError, "before changing"):
            self.service.install()
        self.assertEqual(self.service.plist.read_bytes(), previous)
        self.assertFalse(any(call[1] == "bootstrap" for call in self.runner.calls[count:]))
        self.assertEqual(self.service.status()["health"]["runtime_path"], first["release"] + "/shared_service.py")

    def test_first_install_failure_removes_own_job_and_plist_only(self):
        self.runner.bad_next_health = True
        with self.assertRaises(manager.ServiceError):
            self.service.install()
        self.assertFalse(self.service.plist.exists())
        self.assertIsNone(self.runner.service)
        self.assertTrue(self.service.credentials.exists())

    def test_unknown_plist_is_never_overwritten_stopped_or_uninstalled(self):
        self.service.plist.parent.mkdir(parents=True)
        self.service.plist.write_bytes(plistlib.dumps({"Label": manager.LABEL, "ProgramArguments": ["unknown"]}))
        original = self.service.plist.read_bytes()
        for method in (self.service.install, self.service.stop, self.service.uninstall):
            with self.assertRaisesRegex(manager.ServiceError, "not managed"):
                method()
            self.assertEqual(self.service.plist.read_bytes(), original)
        self.assertFalse(self.runner.calls)

    def test_stop_and_uninstall_preserve_sessions_credentials_and_releases(self):
        result = self.service.install()
        sessions = self.home / ".cache/task-progress/sessions"
        sessions.mkdir()
        task = sessions / "existing.json"
        task.write_text('{"keep":true}')
        self.assertTrue(self.service.stop()["stopped"])
        self.assertTrue(self.service.plist.exists())
        self.assertFalse(self.service.status()["healthy"])
        self.assertTrue(self.service.uninstall()["uninstalled"])
        self.assertFalse(self.service.plist.exists())
        self.assertEqual(task.read_text(), '{"keep":true}')
        self.assertTrue(Path(result["release"]).exists())
        self.assertTrue(self.service.credentials.exists())
        self.assertFalse(self.service.uninstall()["uninstalled"])

    def test_symlink_credentials_and_logs_are_rejected(self):
        self.service.state_dir.mkdir(parents=True)
        elsewhere = self.home / "secret.json"
        elsewhere.write_text("do not touch")
        self.service.credentials.symlink_to(elsewhere)
        with self.assertRaisesRegex(manager.ServiceError, "symlink"):
            self.service.prepare()
        self.assertEqual(elsewhere.read_text(), "do not touch")
        self.service.credentials.unlink()
        self.service.prepare()
        (self.service.state_dir / "service.stdout.log").symlink_to(elsewhere)
        with self.assertRaisesRegex(manager.ServiceError, "symlink service log"):
            self.service.install()
        self.assertEqual(elsewhere.read_text(), "do not touch")

    def test_non_mac_auto_management_is_explicitly_unsupported(self):
        service = manager.ServiceManager(home=self.home, platform="linux")
        self.assertTrue(service.prepare()["ok"])
        for method in (service.install, service.stop, service.status, service.uninstall):
            with self.assertRaisesRegex(manager.ServiceError, "unsupported"):
                method()

    def test_prepare_does_not_require_unix_uid_or_fchmod(self):
        with patch.object(manager, "_uid", return_value=None), patch.object(manager.os, "fchmod", side_effect=AssertionError("Unix only")):
            service = manager.ServiceManager(home=self.home, platform="win32")
            self.assertTrue(service.prepare()["ok"])

    def test_json_cli_does_not_expose_credential(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(manager.main(["prepare", "--home", str(self.home), "--json"]), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertNotIn("Bearer", output.getvalue())


if __name__ == "__main__":
    unittest.main()
