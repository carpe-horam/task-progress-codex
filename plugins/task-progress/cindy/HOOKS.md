# Cindy Hook coverage (0.1.140)

This directory is the Task Progress Cindy plugin source, with an additive Hook adaptation. `prepare.py` generates `core/` from the shared Task Progress implementation and Cindy patches. Do not edit `core/` as an independent source.

The manifest declares `will-compose-context`. It runs only for App initiated new turns in tasks whose owner selected this plugin as a context provider. The Hook attaches a short, read-only reference block based on a fresh `get_progress(view=overview)` call for the exact Task Progress instance last returned by a successful tool call in that Cindy session. It can mention missing planning, pending review, stage inventory, or root closure. It does not change the user's visible message or Task Progress state.

The Hook returns `allow` without a block if no exact session and instance binding exists, the instance was cleared, the service fails, or the overview identity/schema does not match. A resumed or failed turn does not imply task completion. Repeated composition may attach the same advisory again; no turn ID is available for a stronger replay key. The Hook never gates execution.

The manifest also subscribes to the `task-state`, `message`, and `session` topics. On `did-task-state-changed`, it asks the open panel to refresh only when the changed Cindy session is the active, mapped session. On `did-message-created`, only a saved assistant message with a message ID can trigger the same refresh; message content is ignored. The panel coalesces nearby events and rereads Task Progress through its existing read path. Neither event attests that a Task Progress attempt succeeded or ended. Missing or mismatched identity is ignored.

On `did-session-switched`, the existing handler reads the stored Cindy-to-Task Progress mapping, updates the active session, and tells the panel to bind to it. This session topic has narrower host coverage than the saved-message and task-state topics; it is not a turn-start or tool callback. No event is treated as a progress result.

The `message` topic grants the plugin access to saved message content even though this handler ignores it. Cindy 0.1.140 has no plugin layer equivalents of Codex `PreToolUse`, `PostToolUse`, or `SubagentStart`; their reminders, receipt association, and dispatch checks remain Codex only. `will-start-turn` also covers only App initiated new turns and adds no useful guidance beyond `will-compose-context` here.

## Diagnostics

Call `get_hook_diagnostics` from the target Cindy conversation with no arguments. The host-provided `session_context` restricts the result to that conversation. Each of the four event names has a delivery count and last receipt time; `did-*` receipts also retain the host event sequence and any `dropped` count. `will-compose-context` records the sent `allow` or `context` verdict. The diagnostic stores no message body, message ID, prompt text, or tool arguments.

For panel refreshes, compare `refresh_requested`, `refresh_ok`, `refresh_failed`, and `refresh_skipped`. A `read_ok` receipt includes the Task Progress state revision returned by the panel's `get_progress_view` call. Nearby message and task-state events may share one read but receive separate acknowledgements. `pending_refresh_count` means a refresh was requested but no panel acknowledgement arrived, for example because the panel was closed. A same-session switch reports `skipped_same_session` because it does not reread. `will-compose-context` adds model context and does not request a panel refresh.

These receipts prove plugin callback delivery and panel read completion, not that the task result was accepted or that a particular pixel was painted. Updating the plugin retains its KV diagnostics; counts start with the first callback observed by this version.

Build and install through Cindy Forge. Packaging alone does not activate the Hook. After installation, select Task Progress under the target task's context provider settings; enable `will-compose-context` for that task. Existing and other tasks are unaffected until selected.
