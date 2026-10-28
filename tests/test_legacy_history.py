"""The retired SQLite store is an independent, read-only archive source."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


PLUGIN = Path(__file__).resolve().parents[1] / "plugins/task-progress"
spec = importlib.util.spec_from_file_location("legacy_history", PLUGIN / "legacy_history.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="legacy history ? # ")
        self.db = Path(self.temporary.name) / "progress-v3.sqlite3"
        with sqlite3.connect(self.db) as connection:
            connection.executescript("""
                CREATE TABLE sessions(session_id TEXT PRIMARY KEY, current_task_id TEXT, binding_json TEXT);
                CREATE TABLE task_instances(task_instance_id TEXT PRIMARY KEY, session_id TEXT,
                    generation INTEGER, title TEXT, status TEXT, control_state TEXT, epoch INTEGER,
                    root_id TEXT, revision INTEGER, created_at TEXT, updated_at TEXT, archived_at TEXT,
                    state_json TEXT, objective_json TEXT);
                CREATE TABLE nodes(task_instance_id TEXT, epoch INTEGER, node_id TEXT, parent_id TEXT,
                    title TEXT, planning_state TEXT, execution_status TEXT, outcome TEXT, payload_json TEXT);
                CREATE TABLE request_receipts(result_json TEXT);
            """)
            connection.execute("INSERT INTO sessions VALUES (?,?,?)", ("session", "newer", "private-binding"))
            connection.execute("INSERT INTO sessions VALUES (?,?,?)", ("other", "foreign", "private-other"))
            for task, session, generation, status in [("older", "session", 1, "open"),
                                                      ("newer", "session", 2, "resolved"),
                                                      ("foreign", "other", 1, "resolved")]:
                connection.execute("INSERT INTO task_instances VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                   (task, session, generation, task + " title", status, "active", 0,
                                    "root", 5, "2026-10-25T21:00:00+08:00", "2026-10-25T22:00:00+08:00",
                                    "2026-10-25T22:00:00+08:00" if task == "older" else None,
                                    '{"raw_request":"private-state"}', '{"secret":"private-objective"}'))
                payload = json.dumps({"summary": "original result", "raw_request": "private-request",
                                      "credentials": "private-key", "review": {"status": "accepted"},
                                      "logs": "private-log", "assignment": {"provider": "private-provider"}})
                connection.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)",
                                   (task, 0, "root", None, task + " title", "executable", "finished", status, payload))
            connection.execute("INSERT INTO request_receipts VALUES (?)", ('{"token":"private-receipt"}',))

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_keeps_original_results_source_and_task_identity(self):
        before = self.db.read_bytes()
        with patch.object(legacy.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            result = legacy.read_legacy_history(self.db, "session", include_details=True)
        self.assertTrue(connect.call_args.kwargs["uri"])
        self.assertTrue(connect.call_args.args[0].endswith("?mode=ro"))
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual((result["source"], result["schema"], result["read_only"]), ("sqlite_v3", 3, True))
        self.assertEqual(result["task_count"], 2)
        self.assertEqual([record["task_instance_id"] for record in result["records"]], ["newer", "older"])
        self.assertEqual([record["status"] for record in result["records"]], ["resolved", "open"])
        self.assertEqual([record["is_current"] for record in result["records"]], [True, False])
        self.assertEqual(result["records"][0]["summary"], "original result")
        self.assertEqual(result["records"][1]["nodes"][0]["outcome"], "open")
        encoded = json.dumps(result)
        for excluded in ("private-", "review", "accepted", "raw_request", "binding_json", "objective_json"):
            self.assertNotIn(excluded, encoded)

    def test_summary_does_not_read_node_payloads_or_raw_state(self):
        # Summary remains usable even when optional node storage is missing.
        with sqlite3.connect(self.db) as connection:
            connection.execute("DROP TABLE nodes")
        result = legacy.read_legacy_history(self.db, "session")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["task_count"], 2)
        self.assertNotIn("nodes", result["records"][0])
        self.assertNotIn("summary", result["records"][0])
        self.assertEqual(legacy.read_legacy_history(self.db, "session", True)["warning"],
                         "legacy_history_unsupported_schema")

    def test_missing_database_or_session_does_not_create_database_or_read_other_session(self):
        missing = self.db.with_name("missing.sqlite3")
        self.assertIsNone(legacy.read_legacy_history(missing, "session"))
        self.assertFalse(missing.exists())
        self.assertIsNone(legacy.read_legacy_history(self.db, "missing"))
        self.assertIsNone(legacy.read_legacy_history(self.db, "' OR 1=1 --"))

    def test_bad_database_and_io_error_have_fixed_warnings_without_internal_details(self):
        self.db.write_bytes(b"private-path private-token is not a database")
        result = legacy.read_legacy_history(self.db, "session", True)
        self.assertEqual((result["status"], result["records"], result["task_count"]), ("unavailable", [], 0))
        self.assertEqual(result["warning"], "legacy_history_unavailable")
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn(str(self.db), json.dumps(result))
        with patch.object(legacy.sqlite3, "connect", side_effect=sqlite3.OperationalError("private-token")):
            self.assertEqual(legacy.read_legacy_history(self.db, "session")["warning"], "legacy_history_unavailable")

    def test_unknown_schema_or_view_is_rejected_without_running_it(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("DROP TABLE sessions")
            connection.execute("CREATE VIEW sessions AS SELECT 'session' AS session_id, 'newer' AS current_task_id")
        result = legacy.read_legacy_history(self.db, "session")
        self.assertEqual(result["warning"], "legacy_history_unsupported_schema")
        self.assertEqual(result["records"], [])

    def test_bad_or_oversized_optional_payload_keeps_original_metadata(self):
        for payload in ("{invalid-json", "[[]]", "x" * (legacy.MAX_PAYLOAD + 1)):
            with self.subTest(payload_length=len(payload)):
                with sqlite3.connect(self.db) as connection:
                    connection.execute("UPDATE nodes SET payload_json=? WHERE task_instance_id='newer'", (payload,))
                result = legacy.read_legacy_history(self.db, "session", True)
                record = result["records"][0]
                self.assertEqual(record["status"], "resolved")
                self.assertNotIn("summary", record)
                self.assertNotIn(payload, json.dumps(record))
                self.assertLess(len(json.dumps(record)), 2000)

    def test_display_text_is_bounded_and_common_credentials_are_redacted(self):
        payload = json.dumps({"summary": "Bearer top-secret token=token-secret api_key=key-secret "
                              "password:pass-secret https://alice:login-secret@example.org " + "x" * 9000})
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE nodes SET payload_json=? WHERE task_instance_id='newer'", (payload,))
        result = legacy.read_legacy_history(self.db, "session", True)
        summary = result["records"][0]["summary"]
        for secret in ("top-secret", "token-secret", "key-secret", "pass-secret", "login-secret"):
            self.assertNotIn(secret, summary)
        self.assertIn("[redacted]", summary)
        self.assertLessEqual(len(summary), legacy.MAX_TEXT)

    def test_record_and_node_limits_keep_counts_explicit(self):
        with patch.object(legacy, "MAX_TASKS", 1), patch.object(legacy, "MAX_NODES", 0):
            result = legacy.read_legacy_history(self.db, "session", True)
        self.assertEqual(result["task_count"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["records"]), 1)
        self.assertTrue(result["records"][0]["nodes_truncated"])
        self.assertEqual(result["records"][0]["nodes"], [])

    def test_uncheckpointed_wal_contents_remain_visible_to_read_only_reader(self):
        with sqlite3.connect(self.db) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE task_instances SET title='new WAL title' WHERE task_instance_id='newer'")
            writer.commit()
            self.assertTrue(Path(str(self.db) + "-wal").exists())
            before = self.db.read_bytes()
            result = legacy.read_legacy_history(self.db, "session")
            self.assertEqual(result["records"][0]["title"], "new WAL title")
            self.assertEqual(before, self.db.read_bytes())

    def test_unexpected_blob_metadata_cannot_escape_as_raw_bytes(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE task_instances SET title=? WHERE task_instance_id='newer'",
                               (b"private-binary",))
        result = legacy.read_legacy_history(self.db, "session")
        self.assertIsNone(result["records"][0]["title"])
        self.assertNotIn("private-binary", json.dumps(result))

    def test_server_keeps_sources_separate_and_clear_tombstone_prevents_archive_resurrection(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE sessions SET session_id=? WHERE session_id='session'", (sid,))
            connection.execute("UPDATE task_instances SET session_id=? WHERE session_id='session'", (sid,))
        before = self.db.read_bytes()
        server_spec = importlib.util.spec_from_file_location("legacy_integration_server", PLUGIN / "server.py")
        server = importlib.util.module_from_spec(server_spec)
        with patch.object(Path, "home", return_value=Path(self.temporary.name)):
            server_spec.loader.exec_module(server)
        sessions = Path(self.temporary.name) / "isolated-sessions"
        sessions.mkdir()

        def progress(result):
            return result["structuredContent"]["progress"]

        with patch.object(server, "SESSION_DIR", sessions), patch.object(server, "LEGACY_DB_PATH", self.db):
            archive = progress(server.get_progress(sid, include_history=True))
            self.assertEqual(archive["mode"], "legacy_archive")
            self.assertTrue(archive["read_only"])
            self.assertEqual(archive["legacy_history"]["task_count"], 2)
            with self.assertRaises(server.ProgressError):
                server.update_task(sid, "root", "running", assignment={"execution_mode": "main_session"})

            current = progress(server.init_task_network("Current JSON task", sid, atomic_reason="One fixture"))
            instance = current["task_instance_id"]
            server.update_task(sid, "root", "running", task_instance_id=instance,
                               assignment={"execution_mode": "main_session", "self_execution_reason": "Fixture"})
            compact = progress(server.get_progress(sid))
            self.assertEqual(compact["task_instance_id"], instance)
            self.assertEqual(compact["status"], "in_progress")
            self.assertEqual(compact["history_summary"]["legacy_task_count"], 2)
            self.assertNotIn("legacy_history", compact)
            full = progress(server.get_progress(sid, include_history=True))
            self.assertEqual(full["task_title"], "Current JSON task")
            self.assertEqual(full["legacy_history"]["records"][0]["status"], "resolved")
            self.assertNotEqual(instance, full["legacy_history"]["current_task_id"])

            server.clear_progress(sid)
            self.assertIsNone(progress(server.get_progress(sid, include_history=True)))
            self.assertTrue(server._read_order(sid)["legacy_history_cleared"])
            server.init_task_network("After clear", sid, atomic_reason="One fresh fixture")
            after = progress(server.get_progress(sid, include_history=True))
            self.assertNotIn("legacy_history", after)
            self.assertEqual(after["history_summary"]["legacy_task_count"], 0)
        self.assertEqual(before, self.db.read_bytes())


if __name__ == "__main__":
    unittest.main()
