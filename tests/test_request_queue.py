"""Session requests share durable storage but never execution identity."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"

def load_server():
    spec = importlib.util.spec_from_file_location("request_queue_server", PLUGIN / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

server = load_server()
MAIN = {"execution_mode": "main_session", "self_execution_reason": "Check this bounded fixture result"}
SUB = {"execution_mode": "subagent", "model": "fixture", "reasoning_effort": "medium",
       "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Produce this isolated fixture result"}


class RequestQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-requests-")
        self.old_dir = server.SESSION_DIR
        server.SESSION_DIR = Path(self.temp.name)
        result = server.init_task_network("Original A", tasks=[
            {"id": "same", "title": "A work", "assignment": MAIN},
            {"id": "accept", "title": "A check", "assignment": MAIN, "depends_on": ["same"]},
        ])["structuredContent"]
        self.sid = result["session_id"]
        self.a = result["progress"]["task_instance_id"]

    def tearDown(self):
        server.SESSION_DIR = self.old_dir
        self.temp.cleanup()

    def p(self, iid=None, history=False):
        return server.get_progress_view(self.sid, history, iid)["structuredContent"]["progress"]

    def raw(self):
        return json.loads(server._session_file(self.sid).read_text())

    def route(self, rid="B", mode="queue", **kwargs):
        return server.route_request(self.sid, self.a, rid, "  user original\nB  ", "Independent " + rid,
            mode, "Disjoint output; shared resources isolated" if mode == "parallel" else "Continue original A first",
            ["Deliver B evidence"], ["No publication"], **kwargs)["structuredContent"]

    def item(self, rid):
        return next(r for r in self.p()["session_work"]["requests"] if r["request_id"] == rid)

    def node(self, iid, node_id):
        return next(n for n in self.p(iid)["nodes"] if n["id"] == node_id)

    def start(self, iid, node_id, assignment=MAIN, **kwargs):
        server.update_task(self.sid, node_id, "running", task_instance_id=iid, assignment=assignment, **kwargs)
        return self.node(iid, node_id)["attempt_id"]

    def finish(self, iid, node_id, aid=None):
        server.update_task(self.sid, node_id, "finished", outcome="resolved", task_instance_id=iid,
            attempt_id=aid, summary="Evidence checked", evidence=["fixture://evidence"], assignment=MAIN if node_id == self.p(iid)["root_id"] else None)

    def finish_a(self):
        for node_id in ("same", "accept"):
            node = self.node(self.a, node_id)
            aid = node["attempt_id"] if node["execution_status"] == "running" else self.start(self.a, node_id)
            self.finish(self.a, node_id, aid)
        self.finish(self.a, "root")

    def assert_rejected(self, action):
        before = server._session_file(self.sid).read_bytes()
        with self.assertRaises(server.ProgressError):
            action()
        self.assertEqual(before, server._session_file(self.sid).read_bytes())

    def test_queue_running_a_preserves_a_and_requires_explicit_activation(self):
        aid = self.start(self.a, "same")
        before = self.raw()["nodes"]
        result = self.route()
        self.assertEqual(before, self.raw()["nodes"])
        self.assertEqual(result["progress"]["task_instance_id"], self.a)
        self.assertEqual(self.item("B")["status"], "queued")
        self.assertIsNone(self.item("B")["task_instance_id"])
        self.assertEqual(self.item("B")["request_text"], "  user original\nB  ")
        self.assertFalse(result["session_work"]["can_finish_session"])
        self.assertEqual(result["session_work"]["return_to"]["task_instance_id"], self.a)
        self.assert_rejected(lambda: server.activate_request(self.sid, self.a, "B"))
        self.assert_rejected(lambda: server.init_task_network("Bypass", session_id=self.sid, new_task_reason="New goal"))
        self.finish(self.a, "same", aid)

    def test_parallel_same_node_and_attempt_cannot_touch_foreground(self):
        a_attempt = self.start(self.a, "same")
        before = self.raw()["nodes"]
        self.route(mode="parallel")
        b = self.item("B")["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "B work", "assignment": SUB}], task_instance_id=b)
        self.assert_rejected(lambda: self.start(b, "same", MAIN))
        b_attempt = self.start(b, "same", SUB)
        self.assertNotEqual(a_attempt, b_attempt)
        self.assert_rejected(lambda: server.update_task(self.sid, "same", "finished", outcome="resolved", attempt_id=b_attempt, task_instance_id=self.a))
        self.assert_rejected(lambda: server.bind_task_agent(self.sid, "same", a_attempt, "wrong", task_instance_id=b))
        server.bind_task_agent(self.sid, "same", b_attempt, "fixture-agent-B", task_instance_id=b)
        server.update_task(self.sid, "same", "finished", outcome="resolved", attempt_id=b_attempt,
                           summary="B produced", evidence=["fixture://B"], task_instance_id=b)
        self.assertEqual(self.item("B")["status"], "pending_review")
        self.assertFalse(self.p()["session_work"]["can_finish_session"])
        self.assert_rejected(lambda: server.review_task(self.sid, "same", b_attempt, "accept", "Wrong instance", ["fixture://B"], MAIN, task_instance_id=self.a))
        server.review_task(self.sid, "same", b_attempt, "accept", "Independently checked B", ["fixture://checked-B"], MAIN, task_instance_id=b)
        self.finish(b, "root")
        self.assertEqual(self.item("B")["status"], "completed")
        self.assertEqual(before, self.raw()["nodes"])
        self.assertEqual(self.p()["task_instance_id"], self.a)
        self.assertFalse(self.p()["session_work"]["can_finish_session"])
        self.assertEqual(self.p()["session_work"]["return_to"]["task_instance_id"], self.a)
        self.assertEqual(len(self.raw()["task_history"]), 1)
        self.assertEqual(self.raw()["task_history"][0]["task_instance_id"], b)

    def test_fifo_explicit_activation_preserves_parallel_and_rejects_stale_a(self):
        self.route("B")
        self.route("C")
        self.route("P", mode="parallel")
        parallel = self.item("P")["task_instance_id"]
        self.finish_a()
        self.assertEqual(self.p()["task_instance_id"], self.a)
        self.assert_rejected(lambda: server.activate_request(self.sid, self.a, "C"))
        server.activate_request(self.sid, self.a, "B")
        b = self.p()["task_instance_id"]
        revision = self.raw()["state_revision"]
        server.activate_request(self.sid, self.a, "B")  # lost successful response
        self.assertEqual(revision, self.raw()["state_revision"])
        self.assertEqual(b, self.p()["task_instance_id"])
        self.assertNotEqual(b, self.a)
        self.assertEqual(self.p(parallel)["task_title"], "Independent P")
        self.assertNotIn("session_work_state", self.raw()["task_history"][-1])
        self.assertEqual(self.item("C")["status"], "queued")
        self.assert_rejected(lambda: server.update_task(self.sid, "same", "running", assignment=MAIN, task_instance_id=self.a))
        self.assert_rejected(lambda: server.route_request(self.sid, self.a, "late", "late", "late", "queue", "late", ["x"]))
        self.assert_rejected(lambda: server.cancel_request(self.sid, self.a, "C", "stale cancellation"))

    def test_rebuild_restart_preserves_brief_original_queue_and_completed_evidence(self):
        self.route()
        server.update_task_context(self.sid, self.a, "Original A goal", ["Keep source"], ["Acceptance A"], "Continue at acceptance", "Make context explicit")
        aid = self.start(self.a, "same")
        self.finish(self.a, "same", aid)
        server.rebuild_task_network(self.sid, "Replan remaining A", task_instance_id=self.a)
        p = self.p(history=True)
        self.assertEqual(p["task_context"]["goal"], "Original A goal")
        self.assertEqual(p["history"][0]["nodes"]["same"]["evidence"], ["fixture://evidence"])
        self.assertEqual(self.item("B")["request_text"], "  user original\nB  ")
        other = load_server()
        other.SESSION_DIR = server.SESSION_DIR
        recovered = other.get_progress_view(self.sid)["structuredContent"]
        self.assertEqual(recovered["session_work"], self.p()["session_work"])
        self.assertFalse(recovered["session_work"]["can_finish_session"])
        self.assert_rejected(lambda: server.update_task(self.sid, "same", "finished", outcome="resolved", attempt_id=aid, task_instance_id=self.a))

    def test_dedupe_and_queued_edit_preserve_request_original(self):
        self.route()
        size = len(self.p()["session_work"]["requests"])
        self.route()
        self.assertEqual(len(self.p()["session_work"]["requests"]), size)
        server.update_request(self.sid, self.a, "B", "Scope refined", task_title="B revised", constraints=["Local only"], acceptance_criteria=["Check new output"])
        self.route()  # retry uses initial fingerprint, never restores an old edit
        self.assertEqual(self.item("B")["task_title"], "B revised")
        self.assertEqual(self.item("B")["request_text"], "  user original\nB  ")
        self.assert_rejected(lambda: server.route_request(self.sid, self.a, "B", "different", "B", "queue", "Reason", ["criterion"]))
        self.finish_a()
        server.activate_request(self.sid, self.a, "B")
        self.assertEqual(self.p()["task_context"]["constraints"], ["Local only"])
        self.assertEqual(self.p()["task_context"]["acceptance_criteria"], ["Check new output"])
        self.assertEqual(len(self.raw()["session_work_state"]["requests"][1]["revisions"]), 1)

    def test_cancel_pending_only_and_late_updates_rejected(self):
        self.route()
        server.cancel_request(self.sid, self.a, "B", "No longer needed")
        self.assertEqual(self.item("B")["status"], "cancelled")
        self.assert_rejected(lambda: server.activate_request(self.sid, self.a, "B"))
        self.route("P", mode="parallel")
        p = self.item("P")["task_instance_id"]
        server.cancel_request(self.sid, self.a, "P", "Cancelled before dispatch")
        self.assert_rejected(lambda: server.decompose_task(self.sid, "root", [{"id": "same", "title": "Late", "assignment": SUB}], task_instance_id=p))
        self.route("Q", mode="parallel")
        q = self.item("Q")["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "Work", "assignment": SUB}], task_instance_id=q)
        self.start(q, "same", SUB)
        self.assert_rejected(lambda: server.cancel_request(self.sid, self.a, "Q", "Cannot fake process stop"))

    def test_activation_atomic_write_failure_keeps_queue_and_foreground(self):
        self.route()
        self.finish_a()
        before = server._session_file(self.sid).read_bytes()
        real = server._atomic_json
        def fail_document(path, data):
            if path == server._session_file(self.sid):
                raise OSError("Injected before atomic document replacement")
            return real(path, data)
        with patch.object(server, "_atomic_json", side_effect=fail_document):
            with self.assertRaises(OSError):
                server.activate_request(self.sid, self.a, "B")
        self.assertEqual(before, server._session_file(self.sid).read_bytes())
        server.activate_request(self.sid, self.a, "B")
        self.assertEqual(len(self.raw()["task_history"]), 1)
        self.assertEqual(self.item("B")["status"], "active")

    def test_clear_tombstone_and_unscoped_writes_do_not_resurrect_work(self):
        self.route()
        self.assert_rejected(lambda: server.decompose_task(self.sid, "root", [{"id": "late", "title": "Late", "assignment": MAIN}]))
        server.clear_progress(self.sid)
        new = server.init_task_network("New", session_id=self.sid)["structuredContent"]["progress"]["task_instance_id"]
        self.assert_rejected(lambda: server.route_request(self.sid, new, "B", "  user original\nB  ", "Independent B", "queue", "Continue original A first", ["Deliver B evidence"], ["No publication"]))

    def test_tools_public_handlers_include_instance_contract(self):
        schemas = {tool["name"]: tool["inputSchema"] for tool in server.TOOLS}
        for name in ("route_request", "activate_request", "update_request", "cancel_request", "update_task_context"):
            self.assertIn("task_instance_id", schemas[name]["required"])
        for name in ("get_progress", "get_ready_tasks"):
            self.assertIn("task_instance_id", schemas[name]["properties"])
        self.route(mode="parallel")
        b = self.item("B")["task_instance_id"]
        result = server.TOOL_HANDLERS["get_progress"]({"session_id": self.sid, "task_instance_id": b})
        self.assertEqual(result["structuredContent"]["progress"]["task_instance_id"], b)
        self.assertEqual(result["structuredContent"]["progress"]["session_work_summary"]["foreground_task_instance_id"], self.a)

    def test_explicit_original_and_context_revisions_are_recoverable(self):
        server.update_task_context(self.sid, self.a, "A", ["limit"], ["accepted"], "same task", "Record explicit context", request_text="  Original A user text  ")
        server.update_task_context(self.sid, self.a, "A amended", ["new limit"], ["accepted"], "accept next", "Refined scope")
        self.assertEqual(self.p()["session_work"]["foreground"]["request_text"], "  Original A user text  ")
        self.assertEqual(self.p()["session_work"]["foreground"]["goal"], "A amended")
        self.assertNotIn("context_revisions", self.p())
        self.assertEqual(len(self.p(history=True)["context_revisions"]), 2)
        self.assert_rejected(lambda: server.update_task_context(self.sid, self.a, "A", [], ["accepted"], "same", "Cannot replace original", request_text="Different user text"))
        self.route()
        server.update_request(self.sid, self.a, "B", "Refine B", task_title="Revised B")
        full = next(r for r in self.p(history=True)["session_work"]["requests"] if r["request_id"] == "B")
        self.assertEqual(full["revisions"][0]["reason"], "Refine B")

    def test_parallel_context_keeps_explicit_provided_context_and_retry_history(self):
        self.route(mode="parallel", context_brief={"source_path": "fixture://original-material"})
        b = self.item("B")["task_instance_id"]
        server.update_task_context(self.sid, b, "B narrowed", [], ["check result"], "continue B", "Scope refined")
        self.assertEqual(self.p(b)["task_context"]["provided_context"]["source_path"], "fixture://original-material")
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "B", "assignment": SUB}], task_instance_id=b)
        old = self.start(b, "same", SUB)
        server.update_task(self.sid, "same", "failed", summary="Dispatch failed", attempt_id=old, task_instance_id=b)
        new = self.start(b, "same", SUB, retry=True)
        self.assertNotEqual(new, old)
        self.assert_rejected(lambda: server.bind_task_agent(self.sid, "same", old, "late", task_instance_id=b))
        server.update_task(self.sid, "same", "cancelled", summary="Executor stopped", attempt_id=new, task_instance_id=b)
        server.rebuild_task_network(self.sid, "Replan B", task_instance_id=b)
        self.assertEqual(self.p(b)["task_context"]["goal"], "B narrowed")
        self.assertEqual(len(self.p(b, history=True)["history"][0]["nodes"]["same"]["attempts"]), 2)
        self.assert_rejected(lambda: server.update_task(self.sid, "same", "finished", outcome="resolved", attempt_id=new, task_instance_id=b))

    def test_unstarted_cancelled_parallel_has_no_ready_execution(self):
        self.route(mode="parallel")
        b = self.item("B")["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "B", "assignment": SUB}], task_instance_id=b)
        server.cancel_request(self.sid, self.a, "B", "No dispatch requested")
        ready = server.get_ready_tasks(self.sid, b)["structuredContent"]["ready_tasks"]
        self.assertEqual(ready, [])

    def test_queue_prevents_init_after_a_settles_and_finish_waits_for_all_requests(self):
        self.route()
        self.finish_a()
        self.assertFalse(self.p()["session_work"]["can_finish_session"])
        self.assert_rejected(lambda: server.init_task_network("Bypass queue", session_id=self.sid, new_task_reason="Try replace"))
        server.cancel_request(self.sid, self.a, "B", "User withdrew queued request")
        self.assertTrue(self.p()["session_work"]["can_finish_session"])
        replacement = server.init_task_network("Next goal", session_id=self.sid)
        self.assertFalse(replacement["structuredContent"]["session_work"]["can_finish_session"])

    def test_archived_origin_never_becomes_current_return_target(self):
        self.route("B")
        self.route("P", mode="parallel")
        p = self.item("P")["task_instance_id"]
        self.finish_a()
        server.activate_request(self.sid, self.a, "B")
        b = self.p()["task_instance_id"]
        self.assertIsNone(self.item("B")["return_to"])
        self.assertEqual(self.item("B")["origin_return_to"]["task_instance_id"], self.a)
        self.assertEqual(self.item("P")["return_to"]["task_instance_id"], b)
        self.assertEqual(self.item("P")["origin_return_to"]["task_instance_id"], self.a)
        parallel_work = self.p(p)["session_work"]
        self.assertEqual(parallel_work["return_to"]["task_instance_id"], b)
        self.assertEqual(parallel_work["foreground"]["task_instance_id"], b)
        server.rebuild_task_network(self.sid, "Replan B", task_instance_id=b)
        self.assertEqual(self.item("P")["return_to"]["task_id"], self.p()["root_id"])
        server.update_task(self.sid, self.p()["root_id"], "cancelled", summary="B no longer required", task_instance_id=b)
        self.assertIsNone(self.item("P")["return_to"])
        self.assertIsNone(self.p(p)["session_work"]["return_to"])

    def _late_response_during_activation(self, action):
        """Pause a real worker after its locked snapshot, then switch foreground."""
        reached = threading.Event()
        release = threading.Event()
        result = {}
        original = server._tool_result

        def delayed(*args, **kwargs):
            if threading.current_thread().name == "delayed-response":
                reached.set()
                if not release.wait(5):
                    raise RuntimeError("Fixture failed to release delayed response")
            return original(*args, **kwargs)

        def worker():
            try:
                result["late"] = action()
            except Exception as error:
                result["error"] = error

        with patch.object(server, "_tool_result", side_effect=delayed):
            thread = threading.Thread(target=worker, name="delayed-response")
            thread.start()
            try:
                self.assertTrue(reached.wait(5), "Worker must reach response boundary")
                current = server.activate_request(self.sid, self.a, "B")["structuredContent"]
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        if "error" in result:
            raise result["error"]
        late = result["late"]["structuredContent"]
        self.assertEqual(late["progress"]["task_instance_id"], self.a)
        self.assertEqual(late["session_work"]["foreground"]["task_instance_id"], self.a)
        self.assertEqual(late["progress"]["session_work"], late["session_work"])
        self.assertLess(late["state_revision"], current["state_revision"])
        self.assertLess(late["view_sequence"], current["view_sequence"])
        self.assertEqual(current["progress"]["task_instance_id"], current["session_work"]["foreground"]["task_instance_id"])
        server.register_ui_view(self.sid, "latest-B", current["view_sequence"], current["state_revision"], current["progress"]["task_instance_id"])
        rejected = server.register_ui_view(self.sid, "late-A", late["view_sequence"], late["state_revision"], late["progress"]["task_instance_id"])
        self.assertEqual(rejected["structuredContent"]["status"], "superseded")

    def test_concurrent_get_response_uses_one_document_and_ordering_snapshot(self):
        self.route("B")
        self.route("P", mode="parallel")
        self.finish_a()
        self._late_response_during_activation(lambda: server.get_progress_view(self.sid))

    def test_concurrent_mutation_response_uses_same_snapshot_as_saved_result(self):
        self.route("B")
        for node_id in ("same", "accept"):
            aid = self.start(self.a, node_id)
            self.finish(self.a, node_id, aid)
        self._late_response_during_activation(lambda: server.update_task(self.sid, "root", "finished", outcome="resolved",
            summary="Independent acceptance complete", evidence=["fixture://A-acceptance"], assignment=MAIN, task_instance_id=self.a))

    def test_historical_payload_cannot_acquire_new_foreground_metadata(self):
        self.route("B")
        self.finish_a()
        old = self.p()
        server.activate_request(self.sid, self.a, "B")
        delayed = server._tool_result("Old completion response", old, self.sid)["structuredContent"]
        self.assertNotIn("session_work", delayed)
        self.assertEqual(delayed["progress"]["session_work"]["foreground"]["task_instance_id"], self.a)
        self.assertEqual(delayed["state_revision"], old["state_revision"])

    def test_parallel_roots_are_complete_isolated_and_in_registration_order(self):
        self.route("Q")
        self.route("P1", mode="parallel")
        self.route("P2", mode="parallel")
        ids = [self.item(rid)["task_instance_id"] for rid in ("P1", "P2")]
        for index, iid in enumerate(ids):
            server.decompose_task(self.sid, "root", [{"id": "same", "title": f"Peer {index}", "assignment": SUB}], task_instance_id=iid)
        before = self.raw()
        foreground = self.p()
        peers = foreground["parallel_tasks"]
        self.assertEqual([p["request_id"] for p in peers], ["P1", "P2"])
        self.assertEqual([p["task_instance_id"] for p in peers], ids)
        self.assertEqual([p["root_id"] for p in peers], ["root", "root"])
        self.assertEqual([[n["id"] for n in p["nodes"]] for p in peers], [["root", "same"], ["root", "same"]])
        self.assertEqual([p["nodes"][1]["title"] for p in peers], ["Peer 0", "Peer 1"])
        for peer in peers:
            self.assertEqual(peer["state_revision"], foreground["state_revision"])
            self.assertEqual(peer["total"], 1)
            self.assertEqual(peer["ready_count"], 1)
            self.assertEqual(peer["ready_tasks"], ["same"])
        self.assertEqual(before, self.raw(), "Read projections must not change business nodes or storage")
        self.assertNotIn("parallel_tasks", self.p(ids[0]), "Explicit peer reads keep their existing shape")

    def test_parallel_roots_omit_history_legacy_and_recursive_work_even_in_full_read(self):
        self.route("P", mode="parallel")
        iid = self.item("P")["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "Old P plan", "assignment": SUB}], task_instance_id=iid)
        server.rebuild_task_network(self.sid, "New P plan", task_instance_id=iid)
        server.decompose_task(self.sid, self.p(iid)["root_id"], [{"id": "new", "title": "Current P plan", "assignment": SUB}], task_instance_id=iid)
        with patch.object(server, "_legacy_history", return_value=None) as legacy:
            foreground = self.p(history=True)
        self.assertEqual(legacy.call_count, 1, "Only the foreground may consult the legacy reader")
        self.assertIn("task_history", foreground)
        self.assertIn("history", foreground)
        peer = foreground["parallel_tasks"][0]
        for key in ("task_history", "history", "session_work_state", "session_work", "parallel_tasks", "legacy_history", "history_summary"):
            self.assertNotIn(key, peer)
        self.assertFalse(peer["history_included"])
        self.assertEqual(peer["epoch"], 1)
        self.assertEqual(peer["nodes"][1]["id"], "new")
        self.assertEqual(len(self.p(iid, history=True)["history"]), 1, "Explicit include_history peer reads remain compatible")

    def test_parallel_root_status_refreshes_through_execution_review_and_acceptance(self):
        self.route("P", mode="parallel")
        iid = self.item("P")["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "Peer work", "assignment": SUB}], task_instance_id=iid)
        foreground_nodes = self.raw()["nodes"]
        aid = self.start(iid, "same", SUB)
        running = self.p()["parallel_tasks"][0]
        self.assertEqual(running["executing_count"], 1)
        server.bind_task_agent(self.sid, "same", aid, "fixture-agent-P", task_instance_id=iid)
        self.finish(iid, "same", aid)
        awaiting = self.p()["parallel_tasks"][0]
        self.assertEqual(awaiting["status"], "awaiting_review")
        self.assertEqual(awaiting["request_status"], "pending_review")
        self.assertEqual(awaiting["pending_review_count"], 1)
        self.assertIsNotNone(awaiting["nodes"][1]["submission"])
        server.review_task(self.sid, "same", aid, "accept", "Checked peer result", ["fixture://checked"], MAIN, task_instance_id=iid)
        self.finish(iid, "root")
        self.assertEqual(self.p()["parallel_tasks"], [])
        complete = self.p(history=True)["task_history"][0]
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(complete["archive_kind"], "parallel")
        self.assertEqual(complete["resolved_leaf_count"], 1)
        self.assertGreater(complete["state_revision"], running["state_revision"])
        self.assertEqual(foreground_nodes, self.raw()["nodes"])

    def completed_parallel(self, rid="P"):
        self.route(rid, mode="parallel")
        iid = self.item(rid)["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": rid, "assignment": SUB}], task_instance_id=iid)
        aid = self.start(iid, "same", SUB)
        self.accept_parallel(iid, aid)
        return iid, aid

    def accept_parallel(self, iid, aid):
        server.bind_task_agent(self.sid, "same", aid, "fixture-peer", task_instance_id=iid)
        self.finish(iid, "same", aid)
        self.assertIn(iid, [p["task_instance_id"] for p in self.p()["parallel_tasks"]])
        server.review_task(self.sid, "same", aid, "accept", "Independent result checked", ["fixture://review"], MAIN, task_instance_id=iid)
        self.assertIn(iid, [p["task_instance_id"] for p in self.p()["parallel_tasks"]], "Leaf review alone is not root synthesis")
        self.finish(iid, "root")

    def test_completed_parallel_archives_once_and_retry_preserves_frozen_acceptances(self):
        iid, aid = self.completed_parallel()
        old = deepcopy(self.p(history=True)["task_history"][0])
        self.assertEqual(self.p()["history_summary"]["task_count"], 1)
        self.assertNotIn("task_history", self.p())
        self.assertEqual(self.p()["parallel_tasks"], [])
        self.assertIn(iid, self.raw()["session_work_state"]["instances"])
        self.assert_rejected(lambda: server.update_task_context(self.sid, iid, "Late", [], ["Late"], "Late", "Late"))
        self.assert_rejected(lambda: server.rebuild_task_network(self.sid, "Late rebuild", task_instance_id=iid))
        self.assert_rejected(lambda: self.finish(iid, "same", aid))
        self.assert_rejected(lambda: server.update_task(self.sid, "same", "finished", outcome="resolved", retry=True, attempt_id=aid, task_instance_id=iid))
        records = server.get_progress(self.sid, task_instance_id=iid, view="records", task_id="same")["structuredContent"]["progress"]
        self.assertEqual(records["records"][0]["review_status"], "accepted")
        new = self.start(iid, "same", SUB, retry=True)
        self.assertNotEqual(aid, new)
        self.assertEqual(self.p()["parallel_tasks"][0]["task_instance_id"], iid)
        self.assertEqual(self.node(iid, "root")["execution_status"], "pending")
        self.assertEqual(self.p(history=True)["task_history"], [old])
        self.assert_rejected(lambda: self.finish(iid, "same", aid))
        self.accept_parallel(iid, new)
        history = self.p(history=True)["task_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], old, "Retry cannot update old root evidence or acceptance")
        self.assertNotEqual(history[0]["archive_key"], history[1]["archive_key"])
        self.route("unrelated")
        self.assertEqual(self.p(history=True)["task_history"], history)
        page = server.get_progress(self.sid, view="history", history_kind="task_history", offset=1, limit=1)["structuredContent"]["progress"]
        self.assertEqual(page["pagination"]["total"], 2)
        self.assertEqual(page["records"], [history[1]])
        full = server.get_progress(self.sid, view="full")["structuredContent"]["progress"]
        self.assertEqual(full["task_history"][0]["nodes"][1]["attempts"][0]["review_status"], "accepted")

    def test_old_completed_parallel_projects_without_writes_then_persists_once_on_activation(self):
        iid, _ = self.completed_parallel()
        data = self.raw()
        data["task_history"] = []  # Storage produced before parallel archiving.
        server._atomic_json(server._session_file(self.sid), data)
        before = server._session_file(self.sid).read_bytes()
        revision = data["state_revision"]
        history = self.p(history=True)["task_history"]
        self.assertEqual(history[0]["task_instance_id"], iid)
        self.assertEqual(self.p()["parallel_tasks"], [])
        self.assertEqual(before, server._session_file(self.sid).read_bytes())
        self.assertEqual(self.p()["state_revision"], revision)
        self.route("next")
        self.assertEqual(self.raw()["task_history"], history)
        self.finish_a()
        server.activate_request(self.sid, self.a, "next")
        archives = self.p(history=True)["task_history"]
        self.assertEqual(len(archives), 2)
        self.assertEqual(archives[0], history[0])
        self.assertEqual(len({item.get("archive_key") for item in archives if item.get("archive_kind") == "parallel"}), 1)

    def test_legacy_completed_parallel_retry_freezes_projected_history_before_invalidation(self):
        iid, _ = self.completed_parallel()
        data = self.raw()
        data["task_history"] = []
        server._atomic_json(server._session_file(self.sid), data)
        old = self.p(history=True)["task_history"][0]
        self.start(iid, "same", SUB, retry=True)
        self.assertEqual(self.raw()["task_history"], [old])
        self.assertEqual(self.p()["parallel_tasks"][0]["request_status"], "active")

    def test_inconsistent_legacy_resolved_root_with_unresolved_child_stays_current(self):
        iid, _ = self.completed_parallel()
        data = self.raw()
        data["task_history"] = []
        node = data["session_work_state"]["instances"][iid]["nodes"]["same"]
        node.update(execution_status="pending", outcome=None, review_status=None)
        server._atomic_json(server._session_file(self.sid), data)
        self.assertEqual(self.p()["parallel_tasks"][0]["task_instance_id"], iid)
        self.assertEqual(self.p()["history_summary"]["task_count"], 0)

    def test_unresolved_parallel_terminal_states_never_disappear(self):
        for terminal in ("open", "failed", "cancelled"):
            self.route(terminal, mode="parallel")
            iid = self.item(terminal)["task_instance_id"]
            server.decompose_task(self.sid, "root", [{"id": "same", "title": terminal, "assignment": SUB}], task_instance_id=iid)
            aid = self.start(iid, "same", SUB)
            server.update_task(self.sid, "same", "failed", attempt_id=aid, summary="Unresolved output", task_instance_id=iid)
            server.update_task(self.sid, "root", "finished" if terminal == "open" else terminal,
                outcome="open" if terminal == "open" else None, summary="Still unresolved", assignment=MAIN, task_instance_id=iid)
        self.assertEqual([p["request_status"] for p in self.p()["parallel_tasks"]], ["open", "failed", "cancelled"])
        self.assertEqual(self.p(history=True)["task_history"], [])
        self.route("withdrawn", mode="parallel")
        withdrawn = self.item("withdrawn")["task_instance_id"]
        server.cancel_request(self.sid, self.a, "withdrawn", "User withdrew this request")
        self.assert_rejected(lambda: self.start(withdrawn, "root", SUB, retry=True))

    def test_activation_keeps_parallel_roots_and_archives_only_old_foreground(self):
        self.route("B")
        self.route("P", mode="parallel")
        iid = self.item("P")["task_instance_id"]
        self.finish_a()
        result = server.activate_request(self.sid, self.a, "B")["structuredContent"]["progress"]
        self.assertNotEqual(result["task_instance_id"], self.a)
        self.assertEqual([p["task_instance_id"] for p in result["parallel_tasks"]], [iid])
        self.assertEqual(self.p(history=True)["task_history"][-1]["task_instance_id"], self.a)
        self.assertNotIn(self.a, [p["task_instance_id"] for p in result["parallel_tasks"]])

    def test_unplanned_and_cancelled_parallel_roots_remain_truthful(self):
        self.route("Unplanned", mode="parallel")
        self.route("Cancelled", mode="parallel")
        iid = self.item("Cancelled")["task_instance_id"]
        server.decompose_task(self.sid, "root", [{"id": "same", "title": "Never dispatched", "assignment": SUB}], task_instance_id=iid)
        result = server.cancel_request(self.sid, self.a, "Cancelled", "User withdrew this request")["structuredContent"]["progress"]
        unplanned, cancelled = result["parallel_tasks"]
        self.assertEqual(unplanned["planning_status"], "required")
        self.assertEqual(len(unplanned["nodes"]), 1)
        self.assertEqual(unplanned["request_status"], "active")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["request_cancelled"])
        self.assertEqual(cancelled["request_status"], "cancelled")
        self.assertEqual(cancelled["ready_tasks"], [])
        self.assertEqual(cancelled["ready_count"], 0)
        self.assertTrue(all(not node["ready"] for node in cancelled["nodes"]))
        self.assertNotIn("ready", [node["status"] for node in cancelled["nodes"]])
        child = next(node for node in cancelled["nodes"] if node["id"] == "same")
        self.assertEqual(child["status"], "blocked")
        self.assertTrue(child["request_cancelled"])
        raw_child = self.raw()["session_work_state"]["instances"][iid]["nodes"]["same"]
        self.assertEqual(child["execution_status"], raw_child["execution_status"])
        self.assertEqual(child["summary"], raw_child["summary"])
        self.assertEqual(raw_child["execution_status"], "pending")
        self.assertNotIn("request_cancelled", raw_child)
        self.assertNotIn("status", raw_child)
        before = self.raw()
        self.p()
        self.assertEqual(before, self.raw())

    def test_parallel_roots_use_captured_document_without_storage_reread_or_mutation(self):
        self.route("P", mode="parallel")
        iid = self.item("P")["task_instance_id"]
        with server._session_lock(self.sid):
            snapshot = server._capture_response_snapshot(self.sid)
        captured = deepcopy(snapshot)
        old_payload = server._progress_payload(deepcopy(snapshot["document"]))
        server.decompose_task(self.sid, "root", [{"id": "later", "title": "Later plan", "assignment": SUB}], task_instance_id=iid)
        with patch.object(server, "_read", side_effect=AssertionError("Projection must use captured document")), patch.object(server, "_legacy_history", return_value=None):
            delayed = server._tool_result("Captured response", old_payload, self.sid, response_snapshot=snapshot)["structuredContent"]
        peer = delayed["progress"]["parallel_tasks"][0]
        self.assertEqual(peer["state_revision"], delayed["state_revision"])
        self.assertEqual(peer["planning_status"], "required")
        self.assertEqual([n["id"] for n in peer["nodes"]], ["root"])
        self.assertEqual(snapshot, captured, "Projection must not normalize/mutate captured business nodes")
        current = self.p()
        self.assertGreater(current["state_revision"], delayed["state_revision"])
        self.assertEqual(current["parallel_tasks"][0]["nodes"][1]["id"], "later")


if __name__ == "__main__":
    unittest.main()
