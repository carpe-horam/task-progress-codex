"""Response-size and lazy execution-record contracts, never using real sessions."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("tool_response_performance_server", PLUGIN / "server.py")
server = importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as isolated:
    with patch.object(Path, "home", return_value=Path(isolated)):
        spec.loader.exec_module(server)

MAIN = {"execution_mode": "main_session", "self_execution_reason": "Isolated response verification"}
SUB = {"execution_mode": "subagent", "model": "fixture", "reasoning_effort": "high",
       "tool_identifier": "collaboration.spawn_agent", "dispatch_text": "Inspect only this fixture. " * 80}
SUMMARY = "Verified result with complete current evidence. " * 80
EVIDENCE = ["fixture://result/" + "e" * 120]


def progress(result):
    return result["structuredContent"]["progress"]


class ToolResponsePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tool-response-performance-")
        self.addCleanup(self.temp.cleanup)
        server.SESSION_DIR = Path(self.temp.name)
        server.LEGACY_DB_PATH = Path(self.temp.name) / "legacy.sqlite3"
        p = progress(server.init_task_network("Response fixture", tasks=[
            {"id": "work", "title": "Delegated work", "assignment": SUB},
            {"id": "next", "title": "Consumer", "depends_on": ["work"], "dependency_policy": "finished", "assignment": MAIN},
        ]))
        self.sid, self.iid = p["session_id"], p["task_instance_id"]

    def node(self, result, node_id="work"):
        return next(n for n in progress(result)["nodes"] if n["id"] == node_id)

    def submit(self, retry=False, outcome="resolved"):
        result = server.update_task(self.sid, "work", "running", retry=retry, assignment=SUB, task_instance_id=self.iid)
        aid = self.node(result)["attempt_id"]
        server.bind_task_agent(self.sid, "work", aid, "fixture-agent", self.iid)
        return server.update_task(self.sid, "work", "finished", outcome=outcome, summary=SUMMARY,
                                  evidence=EVIDENCE, attempt_id=aid, task_instance_id=self.iid)

    def accept(self, result):
        return server.review_task(self.sid, "work", self.node(result)["attempt_id"], "accept", SUMMARY,
                                  EVIDENCE, MAIN, self.iid)

    def test_current_results_remain_complete_without_repeating_execution_history(self):
        self.accept(self.submit(outcome="open"))
        result = self.accept(self.submit(retry=True))
        compact = self.node(result)
        full = self.node(server.get_progress_view(self.sid, include_history=True))
        self.assertFalse(progress(result)["records_included"])
        self.assertFalse(compact["records_included"])
        self.assertEqual(compact["attempt_count"], 2)
        self.assertEqual(compact["review_record_count"], 2)
        self.assertEqual(compact["review_status"], "accepted")
        self.assertEqual(compact["summary"], SUMMARY)
        self.assertEqual(compact["evidence"], EVIDENCE)
        for key in ("assignment", "dispatch", "submission", "review", "attempt_id"):
            self.assertEqual(compact[key], full[key])
        for key in ("attempts", "invalidated_results", "reassignment_history"):
            self.assertNotIn(key, compact)
        self.assertTrue(full["records_included"])
        self.assertEqual(len(full["attempts"]), 2)
        self.assertEqual(full["attempts"][0]["submission"]["outcome"], "open")
        self.assertEqual(full["attempts"][0]["review"]["evidence"], EVIDENCE)
        self.assertEqual(full["attempts"][1]["assignment"]["dispatch_text"], SUB["dispatch_text"])
        raw = server._read(self.sid)["nodes"]["work"]
        self.assertEqual(full["attempts"], raw["attempts"])
        self.assertNotIn("records_included", raw, "Response projection must not alter persisted evidence")
        self.assertNotIn(SUMMARY, result["content"][0]["text"])
        self.assertLess(len(json.dumps(result)), len(json.dumps(server.get_progress_view(self.sid, include_history=True))))

    def test_ready_dependency_context_keeps_open_results_and_current_evidence(self):
        self.accept(self.submit(outcome="open"))
        result = server.get_ready_tasks(self.sid)
        self.assertEqual([n["id"] for n in result["structuredContent"]["ready_tasks"]], ["next"])
        dependency = result["structuredContent"]["ready_tasks"][0]["dependencies"][0]
        self.assertEqual(dependency["summary"], SUMMARY)
        self.assertEqual(dependency["evidence"], EVIDENCE)
        self.assertEqual(dependency["outcome"], "open")

    def test_parallel_roots_stay_visible_and_load_their_own_records_explicitly(self):
        self.accept(self.submit(outcome="open"))
        server.route_request(self.sid, self.iid, "peer", "Independent request", "Peer", "parallel",
                             "Separate work", ["Preserve evidence"])
        data = server._read(self.sid)
        peer_id = data["session_work_state"]["requests"][-1]["task_instance_id"]
        source = data["nodes"]["work"]
        peer = data["session_work_state"]["instances"][peer_id]
        peer["nodes"][peer["root_id"]].update(deepcopy({key: value for key, value in source.items() if key not in ("id", "parent_id")}))
        server._write(self.sid, data)
        snapshot = deepcopy(server._read(self.sid))
        foreground = progress(server.get_progress_view(self.sid, include_history=True))
        self.assertTrue(foreground["records_included"])
        self.assertEqual(len(foreground["parallel_tasks"]), 1)
        compact = foreground["parallel_tasks"][0]
        self.assertFalse(compact["records_included"])
        self.assertEqual(compact["task_instance_id"], peer_id)
        self.assertEqual(compact["nodes"][0]["attempt_count"], 1)
        self.assertNotIn("attempts", compact["nodes"][0])
        full = progress(server.get_progress_view(self.sid, task_instance_id=peer_id, include_history=True))
        self.assertEqual(full["task_instance_id"], peer_id)
        self.assertNotIn("parallel_tasks", full)
        self.assertTrue(full["records_included"])
        self.assertEqual(full["nodes"][0]["attempts"], source["attempts"])
        self.assertEqual(server._read(self.sid), snapshot)

    def test_sixty_node_tool_text_does_not_expand_into_a_duplicate_result_tree(self):
        data = server._read(self.sid)
        template = data["nodes"]["work"]
        for index in range(57):
            node = deepcopy(template)
            node.update(id=f"extra-{index}", summary=SUMMARY, evidence=EVIDENCE, execution_status="finished", outcome="resolved", review_status="accepted")
            data["nodes"][node["id"]] = node
            data["nodes"]["root"]["children"].append(node["id"])
        server._write(self.sid, data)
        result = server.get_progress_view(self.sid)
        self.assertEqual(len(progress(result)["nodes"]), 60)
        self.assertLess(len(result["content"][0]["text"]), 2000)
        self.assertNotIn(SUMMARY, result["content"][0]["text"])
        self.assertEqual(progress(result)["nodes"][-1]["summary"], SUMMARY)
        self.assertTrue(server.render_progress(self.sid)["structuredContent"]["bootstrap"])
        self.assertNotIn("progress", server.render_progress(self.sid)["structuredContent"])

    def test_linear_attempt_records_remain_available_on_demand(self):
        p = progress(server.init_progress(["Single legacy step"], session_id=None))
        sid = p["session_id"]
        started = progress(server.update_step(sid, 0, "in_progress", assignment=MAIN))["steps"][0]
        result = server.update_step(sid, 0, "completed", note=SUMMARY, attempt_id=started["attempt_id"])
        step = progress(result)["steps"][0]
        self.assertEqual(step["note"], SUMMARY)
        self.assertEqual(step["attempt_count"], 1)
        self.assertNotIn("attempts", step)
        full = progress(server.get_progress_view(sid, include_history=True))["steps"][0]
        self.assertTrue(full["records_included"])
        self.assertEqual(full["attempts"][0]["summary"], SUMMARY)


if __name__ == "__main__":
    unittest.main()
