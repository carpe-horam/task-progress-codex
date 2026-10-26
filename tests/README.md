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
```

Make `playwright` available through normal Node module resolution or set `NODE_PATH` to an existing package directory. `TASK_PROGRESS_BROWSER_CHANNEL` can select another already-installed Playwright channel; it defaults to `chrome`. No dependency or browser is installed by the tests.

Fixtures use temporary session directories and temporary export files. They never read or modify existing task sessions or Downloads. The browser checks exercise real timers, reordered replies, historical iframe mounts, clear/recreate ordering, and the real export click handler. `validate_exports.py` independently checks the generated workbook using ZIP/XML parsing, including synthesis and retry records.

`test_interaction.cjs` also checks focus, text selection and nested scrolling across automatic and changed-state refreshes, keyboard toggles, retry records, theme changes, 280–900 px layouts, transient failures and delayed replacement cards. Set `TASK_PROGRESS_TEST_ARTIFACTS` to an output directory to retain screenshots. `test_export.cjs` uses the real save handler in a child process with an isolated `Path.home()`; it checks leaf task titles and Unicode filename boundaries. Run browser scripts sequentially; each normally takes less than one minute.

The simulated host captures export-save requests; the backend save function is tested separately. This is not a native Codex host or Excel end-to-end test. The observational scripts under `docs/reviews/repro_2026_10_26*` describe the pre-fix revision and intentionally are not the regression suite.
