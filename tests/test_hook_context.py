"""Actor-scoped hook context receipts and selection ordering."""
import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "task-progress"
spec = importlib.util.spec_from_file_location("task_progress_hook_context_tests", PLUGIN / "hooks" / "hook_state.py")
hook_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook_state)


class HookContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task-progress-hook-context-")
        self.addCleanup(self.temp.cleanup)
        self.home_patch = patch.object(Path, "home", return_value=Path(self.temp.name))
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.event = {"session_id": "codex-session", "agent_id": "main-agent"}

    def receipt(self, task_id, view_sequence):
        return {"receipt_version": 1, "operation": "select_work_context", "context_effect": {
            "kind": "work_context_selected", "session_id": "plugin-session",
            "task_instance_id": "instance-1", "epoch": 4, "task_id": task_id,
            "attempt_id": "attempt-" + task_id, "execution_status": "pending",
            "review_status": None, "selection_purpose": "resume", "state_revision": 7,
            "view_sequence": view_sequence,
        }}

    def dispatch_receipt(self, kind, attempt_id, view_sequence, execution_mode=None, instance_id="instance-1"):
        effect = {
            "kind": kind, "session_id": "plugin-session", "task_instance_id": instance_id,
            "epoch": 4, "task_id": "task-a", "attempt_id": attempt_id,
            "execution_status": "running" if kind in ("dispatch_prepared", "attempt_restarted") else "finished",
            "review_status": None, "state_revision": view_sequence, "view_sequence": view_sequence,
        }
        if execution_mode is not None:
            effect["execution_mode"] = execution_mode
        return {"receipt_version": 1, "context_effect": effect}

    def test_successful_explicit_selection_binds_only_the_observing_actor(self):
        hook_state.observe_receipt(self.event, self.receipt("task-a", 20))
        context = hook_state.read_context(self.event)
        self.assertEqual(context["status"], "bound")
        self.assertEqual(context["foreground"]["task_id"], "task-a")
        self.assertEqual(context["foreground"]["attempt_id"], "attempt-task-a")
        self.assertEqual(context["association_source"], "host_observed_successful_task_progress_receipt")
        other_actor = hook_state.read_context({"session_id": "codex-session", "agent_id": "other-agent"})
        self.assertNotEqual(other_actor.get("status"), "bound")

    def test_late_selection_with_same_state_revision_cannot_replace_newer_view(self):
        hook_state.observe_receipt(self.event, self.receipt("task-b", 21))
        hook_state.observe_receipt(self.event, self.receipt("task-a", 20))
        context = hook_state.read_context(self.event)
        self.assertEqual(context["foreground"]["task_id"], "task-b")

    def _observed_child_map(self, attempt_id="attempt-old"):
        hook_state.observe_receipt(self.event, self.dispatch_receipt("dispatch_prepared", attempt_id, 30))
        self.assertTrue(hook_state.observe_dispatch(self.event, "child-agent"))
        child = {"session_id": "codex-session", "agent_id": "child-agent"}
        self.assertEqual(hook_state.read_context(child)["status"], "bound")
        return child

    def test_terminal_attempt_revokes_observed_child_map(self):
        child = self._observed_child_map()
        hook_state.observe_receipt(self.event, self.dispatch_receipt("attempt_ended", "attempt-old", 31))
        self.assertNotEqual(hook_state.read_context(child)["status"], "bound")

    def test_retry_revokes_prior_attempt_map_for_same_task(self):
        child = self._observed_child_map()
        hook_state.observe_receipt(
            self.event, self.dispatch_receipt("attempt_restarted", "attempt-new", 31, "subagent"))
        self.assertNotEqual(hook_state.read_context(child)["status"], "bound")
        parent = hook_state.read_context(self.event)
        self.assertEqual(parent["status"], "bound")
        self.assertIsNone(parent["active_work"])
        self.assertTrue(hook_state.observe_dispatch(self.event, "child-agent-new"))
        self.assertEqual(hook_state.read_context({"session_id": "codex-session", "agent_id": "child-agent-new"})
                         ["foreground"]["attempt_id"], "attempt-new")

    def test_main_session_retry_becomes_active_work(self):
        hook_state.observe_receipt(
            self.event, self.dispatch_receipt("attempt_restarted", "attempt-new", 31, "main_session"))
        context = hook_state.read_context(self.event)
        self.assertEqual(context["active_work"]["attempt_id"], "attempt-new")

    def test_subagent_retry_preserves_unrelated_main_session_work(self):
        hook_state.observe_receipt(self.event, {"receipt_version": 1, "context_effect": {
            "kind": "work_context_selected", "session_id": "plugin-session",
            "task_instance_id": "instance-1", "epoch": 4, "task_id": "main-task",
            "attempt_id": "main-attempt", "execution_status": "running", "state_revision": 1,
            "view_sequence": 1,
        }})
        hook_state.observe_receipt(self.event, self.dispatch_receipt("dispatch_prepared", "sub-old", 2))
        hook_state.observe_receipt(
            self.event, self.dispatch_receipt("attempt_restarted", "sub-new", 3, "subagent"))
        binding = hook_state._read_json(hook_state._binding_path((self.event["session_id"], self.event["agent_id"])))
        self.assertEqual(binding["active_work"]["task_id"], "main-task")
        self.assertEqual(binding["pending_dispatches"], [{
            "session_id": "plugin-session", "task_instance_id": "instance-1", "epoch": 4,
            "task_id": "task-a", "attempt_id": "sub-new",
        }])

    def test_retry_cleanup_preserves_same_task_id_in_other_instance(self):
        hook_state.observe_receipt(
            self.event, self.dispatch_receipt("dispatch_prepared", "x-old", 1, instance_id="instance-x"))
        hook_state.observe_receipt(
            self.event, self.dispatch_receipt("dispatch_prepared", "y-old", 2, instance_id="instance-y"))
        hook_state.observe_receipt(self.event, self.dispatch_receipt(
            "attempt_restarted", "y-new", 3, "subagent", instance_id="instance-y"))
        binding = hook_state._read_json(hook_state._binding_path((self.event["session_id"], self.event["agent_id"])))
        self.assertEqual({item["attempt_id"] for item in binding["pending_dispatches"]}, {"x-old", "y-new"})

    def test_child_terminal_receipt_revokes_parent_observed_map(self):
        child = self._observed_child_map()
        hook_state.observe_receipt(child, self.dispatch_receipt("attempt_ended", "attempt-old", 31))
        child_map = hook_state._state_dir() / (
            "dispatch-" + hook_state._digest("dispatch", child["session_id"], child["agent_id"]) + ".json")
        self.assertEqual(hook_state._read_json(child_map)["status"], "stale")
        parent_path = hook_state._binding_path((self.event["session_id"], self.event["agent_id"]))
        parent = hook_state._read_json(parent_path)
        self.assertEqual(parent["observed_dispatches"], [])

    def test_child_actor_scope_survives_its_submission_receipt(self):
        child = self._observed_child_map()
        initial = hook_state.read_context(child)
        self.assertEqual(initial["actor_scope"]["kind"], "dispatched_child")
        hook_state.observe_receipt(child, self.dispatch_receipt("submission_pending_review", "attempt-old", 31))
        after_receipt = hook_state.read_context(child)
        self.assertEqual(after_receipt["association_source"], "host_observed_successful_task_progress_receipt")
        self.assertEqual(after_receipt["actor_scope"]["kind"], "dispatched_child")
        self.assertEqual(after_receipt["pending_submission"]["task_id"], "task-a")

    def test_subagent_start_only_receives_plugin_ids_for_running_attempt(self):
        hooks_dir = str(PLUGIN / "hooks")
        sys.path.insert(0, hooks_dir)
        self.addCleanup(lambda: sys.path.remove(hooks_dir) if hooks_dir in sys.path else None)
        hook_path = PLUGIN / "hooks" / "task_progress_hook.py"
        hook_spec = importlib.util.spec_from_file_location("task_progress_hook_context_tests", hook_path)
        hook = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(hook)
        target = {"session_id": "plugin-session", "task_instance_id": "instance-1", "epoch": 4,
                  "task_id": "task-a", "attempt_id": "attempt-a"}
        event = {"hook_event_name": "SubagentStart", "session_id": "codex-session", "agent_id": "child"}
        binding = {"status": "bound", "foreground": target, "active_work": target}
        with patch.dict(sys.modules, {"hook_state": hook_state}), \
                patch.object(hook, "event_replay_seen", return_value=False), \
                patch.object(hook_state, "read_context", return_value=binding), \
                patch.object(hook_state, "read_snapshot", return_value={"status": "current", "target": {"execution_status": "finished"}}):
            context = hook.context_for(event)
        self.assertIn("ask the parent session to clarify", context)
        self.assertNotIn("Use task_instance_id=instance-1", context)

    def test_pending_review_is_reminded_at_user_prompt_and_after_wait(self):
        hooks_dir = str(PLUGIN / "hooks")
        sys.path.insert(0, hooks_dir)
        self.addCleanup(lambda: sys.path.remove(hooks_dir) if hooks_dir in sys.path else None)
        hook_spec = importlib.util.spec_from_file_location(
            "task_progress_hook_review_tests", PLUGIN / "hooks" / "task_progress_hook.py")
        hook = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(hook)
        binding = {"status": "bound", "foreground": {"session_id": "plugin-session",
                    "task_instance_id": "instance-1", "epoch": 4},
                   "association_source": "host_observed_successful_task_progress_receipt"}
        snapshot = {"status": "current", "review_queue": {"pending_reviews": [
            {"task_id": "child-a", "attempt_id": "attempt-a"}], "pending_review_count": 1,
            "pending_reviews_has_more": False, "synthesis_ready": [], "synthesis_ready_count": 0}}
        with patch.dict(sys.modules, {"hook_state": hook_state}), \
                patch.object(hook_state, "read_context", return_value=binding), \
                patch.object(hook_state, "read_snapshot", return_value=snapshot), \
                patch.object(hook, "reminder_budget", return_value=True):
            prompt = hook.context_for({"hook_event_name": "UserPromptSubmit", "session_id": "codex-session",
                                       "agent_id": "main-agent", "turn_id": "turn-user"})
            with patch.object(hook, "event_replay_seen", return_value=False):
                after_wait = hook.context_for({"hook_event_name": "PostToolUse", "tool_name": "wait_agent",
                                               "session_id": "codex-session", "agent_id": "main-agent",
                                               "turn_id": "turn-wait", "tool_use_id": "wait-1"})
        for context in (prompt, after_wait):
            self.assertIn("等待主会话 review_task", context)
            self.assertIn("submission/evidence", context)
            self.assertLess(len(context.encode("utf-8")), 2700)
        self.assertIn("Task Progress: 1 个子任务结果", after_wait)

    def test_dispatched_child_does_not_receive_parent_wide_review_reminder_after_receipt(self):
        hooks_dir = str(PLUGIN / "hooks")
        sys.path.insert(0, hooks_dir)
        self.addCleanup(lambda: sys.path.remove(hooks_dir) if hooks_dir in sys.path else None)
        hook_spec = importlib.util.spec_from_file_location(
            "task_progress_hook_child_review_tests", PLUGIN / "hooks" / "task_progress_hook.py")
        hook = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(hook)
        foreground = {"session_id": "plugin-session", "task_instance_id": "instance-1", "epoch": 4,
                      "task_id": "task-a", "attempt_id": "attempt-a"}
        binding = {"status": "bound", "foreground": foreground,
                   "pending_submission": foreground,
                   "association_source": "host_observed_successful_task_progress_receipt",
                   "actor_scope": {"kind": "dispatched_child", "session_id": "plugin-session",
                                   "task_instance_id": "instance-1", "epoch": 4, "task_id": "task-a"}}
        event = {"hook_event_name": "UserPromptSubmit", "session_id": "codex-session",
                 "agent_id": "child-agent", "turn_id": "turn-child"}
        with patch.dict(sys.modules, {"hook_state": hook_state}), \
                patch.object(hook_state, "read_context", return_value=binding), \
                patch.object(hook_state, "read_snapshot") as read_snapshot:
            context = hook.context_for(event)
        self.assertNotIn("parent review", context.lower())
        self.assertNotIn("汇总节点", context)
        read_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
