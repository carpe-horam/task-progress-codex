# Regression checks

Run from the repository root. The backend suite needs only Python 3:

```sh
python3 -m unittest discover -s tests -v
```

The browser suite uses Playwright and an installed Chrome, runs the actual `app.html` in an iframe, and connects a simulated MCP Apps parent to the actual Python server:

```sh
node tests/test_app.cjs
node tests/test_interaction.cjs
node tests/test_export.cjs
node tests/test_view_mode_regressions.cjs
node tests/test_app_audit_regressions.cjs
node tests/test_shared_service_app.cjs
node tests/test_responsibility_app.cjs
node tests/test_history_loading_app.cjs
node tests/test_request_queue_app.cjs
node tests/test_parallel_roots_app.cjs
node tests/test_parallel_archive_app.cjs
node tests/test_handoff_closure_app.cjs
node tests/test_selective_view_app.cjs
node tests/test_record_loading_app.cjs
```

Make `playwright` available through normal Node module resolution or set `NODE_PATH` to an existing package directory. `TASK_PROGRESS_BROWSER_CHANNEL` can select another already-installed Playwright channel; it defaults to `chrome`. No dependency or browser is installed by the tests.

Fixtures use temporary session directories and temporary export files. They never read or modify existing task sessions or Downloads. The browser checks exercise real timers, reordered replies, historical iframe mounts, clear/recreate ordering, and the real export click handler. `validate_exports.py` independently checks the generated workbook using ZIP/XML parsing, including synthesis and retry records.

`test_interaction.cjs` also checks focus, text selection and nested scrolling across automatic and changed-state refreshes, keyboard toggles, retry records, theme changes, 280–900 px layouts, transient failures and delayed replacement cards. Set `TASK_PROGRESS_TEST_ARTIFACTS` to an output directory to retain screenshots. `test_export.cjs` uses the real save handler in a child process with an isolated `Path.home()`; it checks leaf task titles and Unicode filename boundaries. Run browser scripts sequentially; each normally takes less than one minute.

The simulated host captures export-save requests; the backend save function is tested separately. This is not a native Codex host or Excel end-to-end test. Keep generated logs, runtime snapshots and manual audit evidence locally; they are not part of the regression suite or release source.

`test_app_audit_regressions.cjs` reads the packaged HTML through `resources/read`, verifies manifest version injection, checks linear and failed/cancelled delivery summaries and root acceptance records, freezes archived elapsed time, and replays a fullscreen registration that executes after the real 15-second RPC timeout. Set `TASK_PROGRESS_BROWSER_ARTIFACTS` to retain its screenshots, exported HTML and protocol trace. The backend `test_state_revision_fixes.py` covers persistent initialization keys, linear attempt isolation, and stale mode registrations; `test_hook_runtime.py` checks both hooks with a configured Python and an otherwise empty PATH.

`test_shared_service.py` uses a real isolated HTTP daemon to verify concurrent clients, identical JSON-RPC IDs, session isolation, initialization idempotency, authentication, duplicate-daemon exclusion, and graceful/crash restart persistence. `test_service_manager.py` uses temporary homes, real loopback health fixtures, and a fake launchctl runner to test install/upgrade rollback and ownership checks without modifying the user's service. `test_shared_service_app.cjs` connects the real browser UI through HTTP, exercises two chats and fullscreen restoration, and confirms every response comes from one daemon. Set `TASK_PROGRESS_BROWSER_ARTIFACTS` to retain its screenshot and result. The browser host is simulated; native Codex transport acceptance is recorded separately in the review evidence.

For native transport acceptance, run `python3 -B tests/check_codex_http_transport.py --codex /absolute/path/to/codex`. It starts two disposable app-server clients and an isolated HTTP daemon, discovers tools, and reads the UI resource without creating chats or sending model turns. Add `--installed` to verify the installed plugin's transport and existing shared service, or `--output result.json` to save the credential-free result.

Responsibility regressions cover planned executors, explicit reassignment, post-spawn agent binding, result submission, separate main-session acceptance/rejection, dependency gates, and retry invalidation. `test_delegation_policy.py` runs the skill's published JSON examples through public JSON-RPC wire responses against isolated backend state, including sequential complex work delegated to subagents. `test_responsibility_app.cjs` verifies the actual app and offline exports distinguish pending review, rejected work, accepted-but-open reports, resolved results, and legacy records without inventing acceptance history. These checks validate workflow declarations and transitions; they do not authenticate Codex callers or control work performed through other tools.

`test_history_loading_app.cjs` verifies compact polling, on-demand full history, exact snapshot cache binding, delayed full-response rejection, complete HTML/XLSX exports, fail-closed export on unavailable current JSON history, warnings on bounded or unavailable SQLite excerpts, and separate JSON/SQLite read-only provenance. All fixtures and exports are isolated. The SQLite display preserves old status values and does not infer current-protocol acceptance.

Historical running-duration checks install a pre-protocol snapshot into a separate private fixture session and verify task bytes stay unchanged while browsing. Live task replacement fixtures finish their old goal first; they do not use replacement reasons to bypass running-attempt guards. Browser fixture processes isolate both JSON sessions and the legacy SQLite path (or HOME for the HTTP daemon). SQLite-only exports are checked to contain no invented current plan, round, pending status or 0/0 progress.

`test_request_queue.py` covers independent instances, FIFO activation, registration and activation retries, original request/context preservation, cancellation, stale writes, atomic activation failure, and controlled thread interleavings with delayed responses. `test_request_queue_app.cjs` verifies real pending submissions and dependency blocking, accepted progress versus root synthesis, queue visibility, parallel roots, foreground protection, and archived-origin versus current-return guidance. These checks do not prove host-side automatic scheduling; request queues and recovery context are currently outside the HTML/XLSX export scope.

Parallel roots now render directly in the task network. `test_parallel_roots_app.cjs` verifies sibling placement, instance-scoped duplicate node IDs, stable expansion, independent review states, foreground activation, cached payload placeholders and cancelled requests. The request-queue browser test uses the same inline roots instead of a preview switch. Backend projection tests ensure all roots share one captured revision and that reads do not change stored nodes or include recursive history.

Resolved parallel roots move into task history after every node is resolved and root synthesis is complete. Open, failed and cancelled work stays visible until an explicit parent closure review or accepted handoff closes it; original outcomes stay unchanged. `test_request_queue.py` covers frozen acceptance snapshots, explicit retry/reappearance, stale writes, read-only projection of old completed instances, history pagination and deduplication across foreground activation. `test_parallel_archive_app.cjs` verifies lazy history, independent disclosure/export keys for repeated acceptances of the same instance, compact refresh and complete HTML/XLSX evidence. Instances remain addressable by their original ID; only an explicit retry may reopen a completed instance. These are isolated backend and Chrome checks, not native Codex host acceptance.

`test_handoff_closure.py` covers pending-to-accepted handoff reconciliation, a target in an archived instance's old epoch, a cancelled source leaf with no attempt, rejected/stale/self/cyclic/incomplete mappings, source result bindings, cancellation closure continue/accept decisions, unchanged nodes, frozen retry archives, unresolved dependency protection and selective audit receipts/queries. Run it with `python3 -m unittest discover -s tests -p test_handoff_closure.py -v`. `test_handoff_closure_app.cjs` runs the real app and isolated server in Chrome, checks two resolved plus one cancelled parent waiting for synthesis, then handoff/closure history and complete HTML/XLSX acceptance evidence. No live session or shared service is involved.

The request-queue browser test also checks collapsed context with a visible queue count, concise task rows retaining role, bound agent, model and reasoning effort, a result summary on task expansion with separately collapsed execution/binding/review/evidence details, full records on demand, refresh persistence, and disclosure isolation when the task instance changes.

Selective-query checks use raw wire responses, not a helper that expands receipts into snapshots. `test_selective_query_wire.py` covers overview/tree/tasks/records/context/history/full, pagination and revision mismatch, complete selected-instance records, compact peers, and paginated ready dependencies. `test_tool_response_performance.py` measures serialized UTF-8 response bytes on fixed synthetic fixtures; it does not measure model tokens or native Codex message-expansion latency. Mutation and render receipts must not contain task trees, summaries, evidence, or request bodies.

`test_selective_view_app.cjs` checks the app-only read path, bootstrap loading, receipt refresh and identity rows. `test_record_loading_app.cjs` checks lazy record cache identity, concurrent requests, late peer replies and retry after failure. The app can consume old snapshots, but an already-mounted old iframe must be reopened for new JavaScript; saved old tool messages cannot shrink retroactively. `test_hook_runtime.py` also checks selective-query reminders on startup, resume, compaction, forks and new prompts. These remain isolated source/Chrome checks; installation and native host behavior require separate evidence.

`test_auto_display.py` and `test_auto_display_app.cjs` cover one-time latest render intent consumption, concurrent mounts, existing fullscreen leases, teardown, close/poll/replay, explicit reopening, initialize/bootstrap ordering, capability refusal and actual-mode replies. The app test uses real Chrome and a simulated host connected to the actual isolated backend; it does not establish native Codex tab visibility or prevent a host from briefly creating historical surfaces before JavaScript starts.

`test_dynamic_numbering_app.cjs` verifies multi-level numbering, reversed prerequisites, current child-order/dependency changes, inserted prerequisites, inherited group and cross-branch constraints, valid interleaving branches, same-ID parallel isolation, hierarchy changes, and actual backend replanning/history. Its incoming reorder snapshots are explicitly synthetic because the backend has no in-place reorder command. It uses real Chrome and the isolated backend; node identity, expansion, focus and selection survive reordering, including the older-host DOM-move fallback. No native host or actual scheduling claim is made.
