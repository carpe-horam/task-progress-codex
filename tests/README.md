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
```

Make `playwright` available through normal Node module resolution or set `NODE_PATH` to an existing package directory. `TASK_PROGRESS_BROWSER_CHANNEL` can select another already-installed Playwright channel; it defaults to `chrome`. No dependency or browser is installed by the tests.

Fixtures use temporary session directories and temporary export files. They never read or modify existing task sessions or Downloads. The browser checks exercise real timers, reordered replies, historical iframe mounts, clear/recreate ordering, and the real export click handler. `validate_exports.py` independently checks the generated workbook using ZIP/XML parsing, including synthesis and retry records.

`test_interaction.cjs` also checks focus, text selection and nested scrolling across automatic and changed-state refreshes, keyboard toggles, retry records, theme changes, 280–900 px layouts, transient failures and delayed replacement cards. Set `TASK_PROGRESS_TEST_ARTIFACTS` to an output directory to retain screenshots. `test_export.cjs` uses the real save handler in a child process with an isolated `Path.home()`; it checks leaf task titles and Unicode filename boundaries. Run browser scripts sequentially; each normally takes less than one minute.

The simulated host captures export-save requests; the backend save function is tested separately. This is not a native Codex host or Excel end-to-end test. The observational scripts under `docs/reviews/repro_2026_10_26*` describe the pre-fix revision and intentionally are not the regression suite.

`test_app_audit_regressions.cjs` reads the packaged HTML through `resources/read`, verifies manifest version injection, checks linear and failed/cancelled delivery summaries and root acceptance records, freezes archived elapsed time, and replays a fullscreen registration that executes after the real 15-second RPC timeout. Set `TASK_PROGRESS_BROWSER_ARTIFACTS` to retain its screenshots, exported HTML and protocol trace. The backend `test_state_revision_fixes.py` covers persistent initialization keys, linear attempt isolation, and stale mode registrations; `test_hook_runtime.py` checks both hooks with a configured Python and an otherwise empty PATH.

`test_shared_service.py` uses a real isolated HTTP daemon to verify concurrent clients, identical JSON-RPC IDs, session isolation, initialization idempotency, authentication, duplicate-daemon exclusion, and graceful/crash restart persistence. `test_service_manager.py` uses temporary homes, real loopback health fixtures, and a fake launchctl runner to test install/upgrade rollback and ownership checks without modifying the user's service. `test_shared_service_app.cjs` connects the real browser UI through HTTP, exercises two chats and fullscreen restoration, and confirms every response comes from one daemon. Set `TASK_PROGRESS_BROWSER_ARTIFACTS` to retain its screenshot and result. The browser host is simulated; native Codex transport acceptance is recorded separately in the review evidence.

For native transport acceptance, run `python3 -B tests/check_codex_http_transport.py --codex /absolute/path/to/codex`. It starts two disposable app-server clients and an isolated HTTP daemon, discovers tools, and reads the UI resource without creating chats or sending model turns. Add `--installed` to verify the installed plugin's transport and existing shared service, or `--output result.json` to save the credential-free result.

Responsibility regressions cover planned executors, explicit reassignment, post-spawn agent binding, result submission, separate main-session acceptance/rejection, dependency gates, and retry invalidation. `test_delegation_policy.py` runs the skill's published JSON examples against isolated backend state, including sequential complex work delegated to subagents. `test_responsibility_app.cjs` verifies the actual app and offline exports distinguish pending review, rejected work, accepted-but-open reports, resolved results, and legacy records without inventing acceptance history. These checks validate workflow declarations and transitions; they do not authenticate Codex callers or control work performed through other tools.

`test_history_loading_app.cjs` verifies compact polling, on-demand full history, exact snapshot cache binding, delayed full-response rejection, complete HTML/XLSX exports, fail-closed export on unavailable current JSON history, warnings on bounded or unavailable SQLite excerpts, and separate JSON/SQLite read-only provenance. All fixtures and exports are isolated. The SQLite display preserves old status values and does not infer current-protocol acceptance.

Historical running-duration checks install a pre-protocol snapshot into a separate private fixture session and verify task bytes stay unchanged while browsing. Live task replacement fixtures finish their old goal first; they do not use replacement reasons to bypass running-attempt guards. Browser fixture processes isolate both JSON sessions and the legacy SQLite path (or HOME for the HTTP daemon). SQLite-only exports are checked to contain no invented current plan, round, pending status or 0/0 progress.

`test_request_queue.py` covers independent instances, FIFO activation, registration and activation retries, original request/context preservation, cancellation, stale writes, atomic activation failure, and controlled thread interleavings with delayed responses. `test_request_queue_app.cjs` verifies real pending submissions and dependency blocking, accepted progress versus root synthesis, queue visibility, parallel roots, foreground protection, and archived-origin versus current-return guidance. These checks do not prove host-side automatic scheduling; request queues and recovery context are currently outside the HTML/XLSX export scope.

Parallel roots now render directly in the task network. `test_parallel_roots_app.cjs` verifies sibling placement, instance-scoped duplicate node IDs, stable expansion, independent review states, foreground activation, cached payload placeholders and cancelled requests. The request-queue browser test uses the same inline roots instead of a preview switch. Backend projection tests ensure all roots share one captured revision and that reads do not change stored nodes or include recursive history.
