"""Durable initialization, linear-attempt and view-mode regressions.

Every persistent fixture is below a TemporaryDirectory; no user session is read.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("revision_fix_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory(prefix="progress-import-") as import_home:
    with mock.patch.object(Path, "home", return_value=Path(import_home)):
        spec.loader.exec_module(server)
ASSIGNMENT = {"execution_mode": "main_session", "model": "executor-A", "reasoning_effort": "high"}


def content(result):
    return result["structuredContent"]


def progress(result):
    return content(result)["progress"]


class StateRevisionFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-state-fixes-")
        self.session_patch = mock.patch.object(server, "SESSION_DIR", Path(self.temp.name))
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()
        self.temp.cleanup()

    def initialize(self, sid=None, key="request-A", title="Task A"):
        return server.init_task_network(title, sid, atomic_reason="One isolated operation", request_id=key)

    def unchanged_on_rejection(self, sid, action):
        before = server._session_file(sid).read_bytes() if server._session_file(sid).exists() else None
        with self.assertRaises(server.ProgressError):
            action()
        after = server._session_file(sid).read_bytes() if server._session_file(sid).exists() else None
        self.assertEqual(before, after)

    def test_first_allocation_retry_and_returned_session_alias(self):
        first = content(self.initialize())
        second = content(self.initialize())
        third = content(self.initialize(first["session_id"]))
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual(first["progress"]["task_instance_id"], third["progress"]["task_instance_id"])
        self.assertEqual(third["progress"]["task_history"], [])
        self.assertEqual(first["state_revision"], third["state_revision"])
        self.assertGreater(third["view_sequence"], second["view_sequence"])

    def test_rebuild_retry_returns_latest_plan_without_rewriting(self):
        initial = content(self.initialize())
        sid = initial["session_id"]
        instance = initial["progress"]["task_instance_id"]
        rebuilt = progress(server.rebuild_task_network(sid, "Changed plan", task_instance_id=instance))
        server.decompose_task(sid, rebuilt["root_id"], [{"id": "new-work", "title": "New work", "assignment": ASSIGNMENT}], task_instance_id=instance)
        before = server._session_file(sid).read_bytes()
        retried = progress(self.initialize(sid))
        self.assertEqual(retried["task_instance_id"], instance)
        self.assertEqual(retried["epoch"], 1)
        self.assertIn("new-work", [n["id"] for n in retried["nodes"]])
        self.assertEqual(before, server._session_file(sid).read_bytes())

    def test_archived_request_replay_cannot_replace_running_task(self):
        first = content(self.initialize())
        sid = first["session_id"]
        second = progress(self.initialize(sid, "request-B", "Task B"))
        server.update_task(sid, "root", "running", assignment=ASSIGNMENT, task_instance_id=second["task_instance_id"])
        self.unchanged_on_rejection(sid, lambda: self.initialize(sid))
        self.unchanged_on_rejection(sid, lambda: self.initialize())
        current = progress(server.get_progress(sid))
        self.assertEqual(current["task_instance_id"], second["task_instance_id"])
        self.assertEqual(current["nodes"][0]["execution_status"], "running")

    def test_clear_retains_identity_tombstone_without_task_content(self):
        first = content(self.initialize(title="Sensitive task title"))
        sid = first["session_id"]
        server.clear_progress(sid)
        self.unchanged_on_rejection(sid, lambda: self.initialize(title="Sensitive task title"))
        self.unchanged_on_rejection(sid, lambda: self.initialize(sid, title="Sensitive task title"))
        self.assertIsNone(progress(server.get_progress(sid)))
        ledger_text = (server.SESSION_DIR / ".init-request-ledger.json").read_text()
        self.assertNotIn("Sensitive task title", ledger_text)
        self.assertNotIn("request-A", ledger_text)
        self.assertEqual(progress(self.initialize(sid, "fresh", "Fresh task"))["task_history"], [])
        self.unchanged_on_rejection(sid, lambda: self.initialize(sid, title="Sensitive task title"))

    def test_explicit_sessions_have_separate_request_scopes(self):
        first = content(self.initialize(str(uuid.uuid4())))
        second = content(self.initialize(str(uuid.uuid4())))
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.unchanged_on_rejection(first["session_id"], lambda: self.initialize(first["session_id"], title="Different content"))

    def test_reserved_allocation_recovers_after_failed_task_write(self):
        with mock.patch.object(server, "_write", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.initialize()
        bindings = list(json.loads((server.SESSION_DIR / ".init-request-ledger.json").read_text()).values())
        binding = bindings[0]
        self.assertEqual(binding["status"], "reserved")
        retried = content(self.initialize())
        self.assertEqual(retried["session_id"], binding["session_id"])
        self.assertEqual(retried["progress"]["task_instance_id"], binding["task_instance_id"])

    def test_failed_reservation_does_not_create_task(self):
        original = server._atomic_json
        def failing_ledger(path, data):
            if path.name == ".init-request-ledger.json":
                raise OSError("simulated ledger failure")
            return original(path, data)
        with mock.patch.object(server, "_atomic_json", side_effect=failing_ledger):
            with self.assertRaises(OSError):
                self.initialize()
        self.assertFalse(list(server.SESSION_DIR.glob("*.json")))
        self.assertEqual(progress(self.initialize())["task_history"], [])

    def test_reservation_cannot_overwrite_replacement_or_clear(self):
        with mock.patch.object(server, "_write", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                self.initialize()
        binding = next(iter(json.loads((server.SESSION_DIR / ".init-request-ledger.json").read_text()).values()))
        sid = binding["session_id"]
        self.initialize(sid, "request-B", "Task B")
        self.unchanged_on_rejection(sid, lambda: self.initialize())
        server.clear_progress(sid)
        self.unchanged_on_rejection(sid, lambda: self.initialize())

    def test_commit_marker_failure_recovers_existing_identity(self):
        original = server._atomic_json
        def fail_commit_marker(path, data):
            if path.name == ".init-request-ledger.json" and all(b["status"] == "committed" for b in data.values()):
                raise OSError("simulated final ledger failure")
            return original(path, data)
        with mock.patch.object(server, "_atomic_json", side_effect=fail_commit_marker):
            with self.assertRaises(OSError):
                self.initialize()
        binding = next(iter(json.loads((server.SESSION_DIR / ".init-request-ledger.json").read_text()).values()))
        current_before = server._session_file(binding["session_id"]).read_bytes()
        retried = progress(self.initialize())
        self.assertEqual(retried["task_instance_id"], binding["task_instance_id"])
        self.assertEqual(current_before, server._session_file(binding["session_id"]).read_bytes())

    def test_cross_process_first_retry_allocates_once(self):
        worker = '''import importlib.util,json,sys
from pathlib import Path
from unittest import mock
sys.dont_write_bytecode=True
p=Path(sys.argv[1]);d=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location("worker_server",p);s=importlib.util.module_from_spec(spec)
with mock.patch.object(Path,"home",return_value=d/"home"):
 spec.loader.exec_module(s)
s.SESSION_DIR=d
r=s.init_task_network("Concurrent retry",atomic_reason="One operation",request_id="shared-first-request")["structuredContent"]
print(json.dumps([r["session_id"],r["progress"]["task_instance_id"],len(r["progress"]["task_history"])]))
'''
        processes = [subprocess.Popen([sys.executable, "-B", "-c", worker, str(PLUGIN / "server.py"), self.temp.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(results[0][2], 0)

    def linear(self):
        result = content(server.init_progress(["Only step"]))
        return result["session_id"], result["progress"]["task_instance_id"]

    def test_linear_missing_and_old_attempts_rejected_before_correct_completion(self):
        sid, instance = self.linear()
        a = progress(server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT))["steps"][0]
        for status in ("in_progress", "completed", "pending"):
            self.unchanged_on_rejection(sid, lambda status=status: server.update_step(sid, 0, status))
        server.update_step(sid, 0, "pending", attempt_id=a["attempt_id"], note="Cancelled executor A")
        b_assignment = {**ASSIGNMENT, "model": "executor-B"}
        b = progress(server.update_step(sid, 0, "in_progress", assignment=b_assignment))["steps"][0]
        self.assertNotEqual(a["attempt_id"], b["attempt_id"])
        for status in ("in_progress", "completed", "pending"):
            self.unchanged_on_rejection(sid, lambda status=status: server.update_step(sid, 0, status, attempt_id=a["attempt_id"], task_instance_id=instance))
        final = progress(server.update_step(sid, 0, "completed", attempt_id=b["attempt_id"], note="Executor B completed"))
        self.assertEqual(final["status"], "completed")
        history = final["steps"][0]["attempts"]
        self.assertEqual([h["execution_status"] for h in history], ["cancelled", "finished"])
        self.assertEqual(history[0]["summary"], "Cancelled executor A")
        self.assertEqual(history[1]["assignment"]["model"], "executor-B")
        self.assertEqual(history[1]["summary"], "Executor B completed")

    def test_legacy_linear_storage_upgrades_before_retry(self):
        sid, _ = self.linear()
        data = server._read(sid)
        data["steps"][0].update(status="in_progress", started_at=server._now(), assignment=ASSIGNMENT, note="Legacy executor")
        server._write(sid, data)
        old = progress(server.update_step(sid, 0, "in_progress", note="Legacy update"))["steps"][0]
        self.assertTrue(old["legacy_attempt"])
        server.update_step(sid, 0, "pending")
        new = progress(server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT))["steps"][0]
        self.assertFalse(new["legacy_attempt"])
        self.unchanged_on_rejection(sid, lambda: server.update_step(sid, 0, "completed"))
        self.unchanged_on_rejection(sid, lambda: server.update_step(sid, 0, "completed", attempt_id=old["attempt_id"]))
        final = progress(server.update_step(sid, 0, "completed", attempt_id=new["attempt_id"]))
        self.assertEqual(len(final["steps"][0]["attempts"]), 2)

    def test_linear_completed_reopen_preserves_finished_attempt(self):
        sid, _ = self.linear()
        a = progress(server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT))["steps"][0]
        server.update_step(sid, 0, "completed", attempt_id=a["attempt_id"], note="First finished result")
        self.unchanged_on_rejection(sid, lambda: server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT))
        second = progress(server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT, attempt_id=a["attempt_id"]))["steps"][0]
        self.assertNotEqual(a["attempt_id"], second["attempt_id"])
        self.assertEqual(second["attempts"][0]["execution_status"], "finished")
        self.assertEqual(second["attempts"][0]["summary"], "First finished result")

    def test_legacy_completion_accepts_once_then_requires_returned_token(self):
        sid, _ = self.linear()
        data = server._read(sid)
        data["steps"][0].update(status="in_progress", started_at=server._now(), assignment=ASSIGNMENT)
        server._write(sid, data)
        finished = progress(server.update_step(sid, 0, "completed"))["steps"][0]
        self.assertFalse(finished["legacy_attempt"])
        self.assertTrue(finished["attempts"][0]["legacy_attempt"])
        self.unchanged_on_rejection(sid, lambda: server.update_step(sid, 0, "completed", note="Late old result"))
        reopened = progress(server.update_step(sid, 0, "in_progress", assignment=ASSIGNMENT, attempt_id=finished["attempt_id"]))["steps"][0]
        self.assertNotEqual(finished["attempt_id"], reopened["attempt_id"])
        self.unchanged_on_rejection(sid, lambda: server.update_step(sid, 0, "completed", attempt_id=finished["attempt_id"]))

    def register_fixture(self):
        result = content(self.initialize())
        sid = result["session_id"]
        args = dict(view_sequence=result["view_sequence"], state_revision=result["state_revision"], task_instance_id=result["progress"]["task_instance_id"])
        self.assertEqual(content(server.register_ui_view(sid, "inline", **args, mode_generation=0))["status"], "registered")
        return sid, args

    def test_mode_generation_rejects_late_heartbeat_without_registry_write(self):
        sid, args = self.register_fixture()
        server.TOOL_HANDLERS["register_ui_view"]({"session_id": sid, "view_id": "tab", **args, "display_mode": "fullscreen", "mode_generation": 0})
        server.register_ui_view(sid, "tab", **args, display_mode="inline", mode_generation=1)
        before = server._ui_view_file(sid).read_bytes()
        for generation in (0, 1, None):
            status = content(server.register_ui_view(sid, "tab", **args, display_mode="fullscreen", mode_generation=generation))["status"]
            self.assertEqual(status, "superseded")
            self.assertEqual(before, server._ui_view_file(sid).read_bytes())
        self.assertEqual(content(server.check_ui_view(sid, "inline"))["status"], "latest")

    def test_owner_recovery_preserves_historical_mount_rejection(self):
        sid, old_source = self.register_fixture()
        newer = content(server.get_progress(sid))
        new_source = dict(view_sequence=newer["view_sequence"], state_revision=newer["state_revision"], task_instance_id=newer["progress"]["task_instance_id"])
        server.register_ui_view(sid, "new", **new_source, mode_generation=0)
        self.assertEqual(content(server.register_ui_view(sid, "historical", **old_source, mode_generation=0))["status"], "superseded")
        self.assertEqual(content(server.check_ui_view(sid, "inline"))["status"], "superseded")
        server.unregister_ui_view(sid, "new")
        self.assertEqual(content(server.check_ui_view(sid, "inline"))["status"], "latest")
        self.assertEqual(content(server.check_ui_view(sid, "historical"))["status"], "superseded")

    def test_same_source_has_one_owner_and_retry_cannot_steal(self):
        sid, args = self.register_fixture()
        server.register_ui_view(sid, "duplicate", **args, mode_generation=0)
        self.assertEqual(content(server.check_ui_view(sid, "inline"))["status"], "superseded")
        self.assertEqual(content(server.register_ui_view(sid, "inline", **args, mode_generation=0))["status"], "superseded")
        self.assertEqual(content(server.check_ui_view(sid, "duplicate"))["status"], "latest")
        server.unregister_ui_view(sid, "duplicate")
        self.assertEqual(content(server.check_ui_view(sid, "inline"))["status"], "latest")

    def test_stale_demotion_releases_lease_with_new_generation(self):
        sid, args = self.register_fixture()
        server.register_ui_view(sid, "tab", **args, display_mode="fullscreen", mode_generation=0)
        newer = content(server.get_progress(sid))
        new_source = dict(view_sequence=newer["view_sequence"], state_revision=newer["state_revision"], task_instance_id=newer["progress"]["task_instance_id"])
        server.register_ui_view(sid, "new", **new_source, mode_generation=0)
        self.assertEqual(content(server.register_ui_view(sid, "tab", **args, mode_generation=1))["status"], "superseded")
        self.assertEqual(content(server.check_ui_view(sid, "new"))["status"], "latest")
        self.assertEqual(json.loads(server._ui_view_file(sid).read_text())["views"]["tab"]["mode_generation"], 1)
        self.assertEqual(content(server.register_ui_view(sid, "tab", **args, display_mode="fullscreen", mode_generation=0))["status"], "superseded")

    def test_late_demotion_cannot_release_current_fullscreen(self):
        sid, args = self.register_fixture()
        server.register_ui_view(sid, "tab", **args, display_mode="inline", mode_generation=0)
        server.register_ui_view(sid, "tab", **args, display_mode="fullscreen", mode_generation=1)
        before = server._ui_view_file(sid).read_bytes()
        for generation in (0, 1, None):
            self.assertEqual(content(server.register_ui_view(sid, "tab", **args, mode_generation=generation))["status"], "superseded")
            self.assertEqual(before, server._ui_view_file(sid).read_bytes())
        self.assertEqual(content(server.check_ui_view(sid, "inline"))["status"], "hidden_by_fullscreen")

    def test_cross_task_early_return_keeps_mode_generation_watermark(self):
        for initial_mode in ("fullscreen", "inline"):
            with self.subTest(initial_mode=initial_mode):
                sid = str(uuid.uuid4())
                old = content(server.init_task_network("Old task", sid, atomic_reason="One old task"))
                old_source = dict(view_sequence=old["view_sequence"], state_revision=old["state_revision"], task_instance_id=old["progress"]["task_instance_id"])
                server.register_ui_view(sid, "tab", **old_source, display_mode=initial_mode, mode_generation=0)
                new = content(server.init_task_network("New task", sid, atomic_reason="One new task"))
                new_source = dict(view_sequence=new["view_sequence"], state_revision=new["state_revision"], task_instance_id=new["progress"]["task_instance_id"])
                server.register_ui_view(sid, "new-inline", **new_source, mode_generation=0)
                # generation 1's fullscreen registration carries the polled new
                # task, but is delayed until after generation 2's stale inline.
                self.assertEqual(content(server.register_ui_view(sid, "tab", **old_source, mode_generation=2))["status"], "superseded")
                metadata = json.loads(server._ui_view_file(sid).read_text())
                self.assertEqual(metadata["views"]["tab"]["mode_generation"], 2)
                self.assertEqual(metadata["inline_owner"], "new-inline")
                self.assertEqual(metadata["views"]["tab"]["inline_source"], old_source)
                self.assertEqual(content(server.check_ui_view(sid, "new-inline"))["status"], "latest")
                before = server._ui_view_file(sid).read_bytes()
                self.assertEqual(content(server.register_ui_view(sid, "tab", **new_source, display_mode="fullscreen", mode_generation=1))["status"], "superseded")
                self.assertEqual(before, server._ui_view_file(sid).read_bytes())
                self.assertEqual(content(server.check_ui_view(sid, "new-inline"))["status"], "latest")

    def test_rejected_source_on_new_view_still_orders_later_requests(self):
        sid, args = self.register_fixture()
        self.assertEqual(content(server.register_ui_view(sid, "delayed-tab", task_instance_id="old-instance", view_sequence=args["view_sequence"], state_revision=args["state_revision"], mode_generation=2))["status"], "superseded")
        before = server._ui_view_file(sid).read_bytes()
        self.assertEqual(content(server.register_ui_view(sid, "delayed-tab", **args, display_mode="fullscreen", mode_generation=1))["status"], "superseded")
        self.assertEqual(before, server._ui_view_file(sid).read_bytes())
        self.assertEqual(content(server.register_ui_view(sid, "delayed-tab", **args, display_mode="fullscreen", mode_generation=2))["status"], "superseded")
        self.assertEqual(content(server.register_ui_view(sid, "delayed-tab", **args, display_mode="fullscreen", mode_generation=3))["status"], "registered")

    def test_duplicate_fullscreen_rejection_still_fences_old_mode(self):
        sid, args = self.register_fixture()
        server.register_ui_view(sid, "active-tab", **args, display_mode="fullscreen", mode_generation=0)
        self.assertEqual(content(server.register_ui_view(sid, "duplicate-tab", **args, display_mode="fullscreen", mode_generation=2))["status"], "duplicate_fullscreen")
        before = server._ui_view_file(sid).read_bytes()
        for generation in (1, 2, None):
            self.assertEqual(content(server.register_ui_view(sid, "duplicate-tab", **args, mode_generation=generation))["status"], "superseded")
            self.assertEqual(before, server._ui_view_file(sid).read_bytes())
        server.unregister_ui_view(sid, "duplicate-tab")
        before = server._ui_view_file(sid).read_bytes()
        self.assertEqual(content(server.register_ui_view(sid, "duplicate-tab", **args, mode_generation=3))["status"], "superseded")
        self.assertEqual(before, server._ui_view_file(sid).read_bytes())

    def test_generation_validation_legacy_compatibility_and_current_renewal(self):
        sid, args = self.register_fixture()
        for invalid in (-1, True, 1.5, "1"):
            before = server._ui_view_file(sid).read_bytes()
            with self.assertRaises(server.ProgressError):
                server.register_ui_view(sid, "bad", **args, mode_generation=invalid)
            self.assertEqual(before, server._ui_view_file(sid).read_bytes())
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args, display_mode="fullscreen"))["status"], "registered")
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args))["status"], "superseded")
        self.assertEqual(json.loads(server._ui_view_file(sid).read_text())["views"]["old-card"]["display_mode"], "inline")
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args, mode_generation=1))["status"], "superseded")
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args, display_mode="fullscreen", mode_generation=2))["status"], "registered")
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args, display_mode="fullscreen", mode_generation=2))["status"], "registered")
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args))["status"], "superseded")
        server.unregister_ui_view(sid, "old-card")
        self.assertEqual(content(server.register_ui_view(sid, "old-card", **args, mode_generation=3))["status"], "superseded")

    def test_protocol_schema_handlers_and_manifest_version_source(self):
        tools = {t["name"]: t for t in server.TOOLS}
        self.assertIn("attempt_id", tools["update_step"]["inputSchema"]["properties"])
        self.assertIn("mode_generation", tools["register_ui_view"]["inputSchema"]["properties"])
        sid, _ = self.linear()
        started = progress(server.TOOL_HANDLERS["update_step"]({"session_id": sid, "step_index": 0, "status": "in_progress", "assignment": ASSIGNMENT}))
        aid = started["steps"][0]["attempt_id"]
        ended = progress(server.TOOL_HANDLERS["update_step"]({"session_id": sid, "step_index": 0, "status": "completed", "attempt_id": aid}))
        self.assertEqual(ended["status"], "completed")
        manifest = server.SESSION_DIR / "fixture-plugin.json"
        app = server.SESSION_DIR / "fixture-app.html"
        manifest.write_text(json.dumps({"version": "91.2.3-test"}))
        app.write_text('<script>const info={version:"__TASK_PROGRESS_VERSION__"}</script>')
        with mock.patch.object(server, "PLUGIN_MANIFEST_PATH", manifest), mock.patch.object(server, "APP_HTML_PATH", app):
            handshake = json.loads(server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))["result"]
            resource = json.loads(server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": server.RESOURCE_URI}}))["result"]["contents"][0]["text"]
            self.assertEqual(handshake["serverInfo"]["version"], "91.2.3-test")
            self.assertIn('version:"91.2.3-test"', resource)
            self.assertNotIn("__TASK_PROGRESS_VERSION__", resource)


if __name__ == "__main__":
    unittest.main()
