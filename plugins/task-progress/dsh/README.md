# Task Progress for DeepSeekRuntime (dsh)

The same plugin, packaged for DeepSeekRuntime. Codex installs it from the
repository root (`.codex-plugin/plugin.json` + `.mcp.json` + `skills/`); this
directory adds the DeepSeekRuntime bundle so one checkout serves both hosts.

| Concern | Codex | DeepSeekRuntime |
| --- | --- | --- |
| Package manifest | `.codex-plugin/plugin.json` | `dsh/package.json` (`dsh.bundle.patch`, `dsh.client`) |
| MCP tools | `.mcp.json` → `python mcp_bridge.py` | `ctx.tools.register` for every tool the server advertises (`dsh/lib/tools.js`) |
| Skill discovery | `"skills": "./skills/"` in the manifest | `ctx.skills.register` at mount (`dsh/lib/skills.js`) |
| Interactive card | MCP Apps resource `ui://task-progress/...` | `tool.call.toolview` + a local MCP Apps proxy (`dsh/lib/client.js`) |
| Hooks / reminders | `hooks/hooks.json` | not adapted yet (see below) |

Everything else is shared and unchanged: `server.py`, `service_manager.py`,
`mcp_bridge.py`, `app.html`, the skill bodies and `~/.cache/task-progress`. Both
hosts talk to the *same* background service, so a task started in Codex and a
task started in DeepSeekRuntime live in one store, keyed by their own session id.

## Why tools are registered instead of using an MCP row

DSH can mount an MCP server through `@deepseek-ai/dsh-mcp-client`, but that row
takes a static `command` in YAML. A bundle installed from a checkout cannot know
its own absolute install path at authoring time, and the workaround — publishing
the path from a sibling row and reading `process.env` in a `!!js` expression —
makes the MCP row depend on mount order and fails closed when the first row has
any problem.

Registering the tools here keeps one row and one failure mode. Tool names are
copied verbatim from `tools/list` (`render_progress`, `get_progress`, …), so the
skill bodies and their copyable examples stay valid in both hosts. The cost is
that this bundle re-implements the small part of the MCP client it needs
(`dsh/lib/mcp-stdio.js`): initialize, `tools/list`, `tools/call`.

## Layout

```
plugins/task-progress/
├── .codex-plugin/plugin.json   Codex manifest
├── .mcp.json                   Codex MCP wiring
├── app.html                    MCP Apps panel (shared)
├── mcp_bridge.py               stdio → loopback HTTP bridge (shared)
├── server.py                   MCP server / task engine (shared)
└── dsh/                        DeepSeekRuntime bundle
    ├── package.json            bundle manifest + client platform
    ├── icon.svg                bundle icon (must live inside this directory)
    ├── cordis.patch.yml        inserts the single plugin row
    ├── lib/index.js            host: one bridge, skills, panel route, RPC relay
    ├── lib/client.js           client: the in-conversation progress card
    ├── lib/tools.js            JSON Schema → DSH parameters, tool registration
    ├── lib/mcp-stdio.js        stdio JSON-RPC client (tools + panel)
    ├── lib/paths.js            derives every path from this package's location
    └── lib/skills.js           registers the shipped skills with DSH
```

Nothing runtime-relevant is copied: `lib/paths.js` resolves `<plugin>` from the
package location, so a linked profile install keeps reading this checkout and the
panel, the MCP server and the skills cannot drift from the Codex copy. The one
duplicated file is `icon.svg`: dsh validates plugin metadata against the manifest
directory, so `icon: ../assets/...` fails to load the bundle
(“icon must remain inside its manifest directory”).

## Install

Point the profile plugin manager at this directory:

- Desktop UI: **Plugins → Add plugin → Local directory** →
  `plugins/task-progress/dsh`
- CLI/agent: `plugin_manager` `action: install_bundle`, `target:` the absolute
  path of `plugins/task-progress/dsh`

The result is one row, `task-progress` (`dsh-task-progress-plugin`), enabled for
every session in that profile and kept across restarts. Reload the page (or
restart the app) once so the client bundle is loaded.

Uninstall with `plugin_manager` `action: remove_bundle`
`target: dsh-task-progress-plugin`, or disable the `task-progress` row. Removing
the bundle deletes neither task data nor the background service.

## Requirements

- Python 3 reachable as `python3`. The launcher `../scripts/launch_progress_mcp`
  prefers `TASK_PROGRESS_PYTHON`, then `python3`, then `python`. Set
  `TASK_PROGRESS_DSH_LAUNCHER` to replace the launcher entirely.
- The same platform support as the Codex plugin. The first MCP call installs or
  updates the shared background service through the native service manager; see
  `docs/installation.md`.
- No `@deepseek-ai/*` import: a linked bundle cannot resolve those packages in
  this DSH version (`ERR_MODULE_NOT_FOUND`), so the bundle stays dependency-free.

## Verify

1. `GET /task-progress/status` on the Web origin returns the resolved paths, the
   registered tool names and the registered skill names. This is the fastest way
   to tell a mount problem from a Python problem:

   ```sh
   curl -s http://127.0.0.1:<port>/task-progress/status
   ```

2. Ask for a multi-step task, or have the agent call `render_progress` with the
   session id returned by `init_progress` / `init_task_network`. The card renders
   in the conversation: it loads `app.html`, answers the MCP Apps handshake, and
   relays `tools/call` to the same MCP server the agent uses.
3. The `track-progress` skill is listed and loadable.

The card resolves its task session from the `render_progress` call — the
arguments first, then the tool result — because Task Progress keys data by its own
UUID, not by the host session id. A call without a usable id renders a short
hint instead of an empty panel.

## Surfaces

The bundle exposes the same state through three surfaces:

| Surface | Where | Content |
| --- | --- | --- |
| `render_progress` card | in the conversation, with the tool call | `app.html` (full interactive panel) |
| Right-sidebar tab 「任务进度」 | the shell's right column, beside the conversation | native task tree, progress bar and counts, polled every 5s, plus the embedded `app.html` below |
| `/task-progress/app.html` | a browser tab | `app.html` alone |

### How the right-sidebar tab is registered

The shell owns the right column's tab list through a `sidebarRightTabs` service;
a plugin declares a tab type there and supplies its body and title as keyed slots
under the same id. `dsh-context` does exactly this, and this bundle follows it:

```js
ctx.inject(["sidebarRightTabs", "slots"], (scope) => {
  scope.sidebarRightTabs.register({ id: TAB, kind: TAB, title: () => "任务进度", guide: [...] })
  scope.slots.register({ name: "sidebar.right.pane.tab", key: TAB }, Panel)
  scope.slots.register({ name: "sidebar.right.pane.tab.title", key: TAB }, TabTitle)
})
```

Notes that cost a debugging round each:

- The body slot is dispatched by **`tab.kind`**, and the type is looked up by its
  definition `id`; using two different strings yields the owner's "nothing can
  view this" notice. Keep `id` and `kind` identical.
- Confirm registration from the host instead of guessing: the occupant list of
  `sidebar.right.pane.tab` (through `cordis_inspect_query`) shows
  `dsh-task-progress-plugin / key: task-progress` once it worked.
- A `main` panel entry or a `sidebar.panellist` icon is the wrong place here: both
  replace the conversation area. `shell.overlay` floats over the window edge
  instead of joining the column, so it is not used either.
- If `sidebarRightTabs` is absent, the plugin skips the tab and keeps working
  (only the conversation card remains).

Both surfaces resolve their session the same way: the tool call's argument, then
the tool result, then `GET /task-progress/session`, which reports the session of
the last progress tool call the host executed (the Chat node does not reliably
expose call arguments to a tool view).

## Live sync

The tab body is the real `app.html`, and an embedded `app.html` only re-reads on
mount: nothing in it polls this shell (its own poll path reads via
`get_progress_view`, which reports no `view`, so `receiveResult` cannot treat the
answer as a panel snapshot). The panel therefore drives the frame:

- every 5 seconds it fetches `get_progress(view:"full")` over the same RPC route
  and pushes the result as `ui/notifications/tool-result`;
- it also pushes immediately whenever the host's per-session revision changes,
  which is how a step recorded while the tab stays open shows up at once.

Two constraints are easy to break and were both found the hard way:

- **reads must not count as changes.** The push *is* a `get_progress` call; if a
  read bumped the revision, the push would re-trigger itself forever. Only
  mutating tools move the counter, and `register_ui_view` / `unregister_ui_view`
  are excluded too (a fullscreen lease renews every few seconds).
- **a frame is bound to the first session it adopts.** `receiveResult` drops every
  result whose session differs from the frame's own, so switching tasks needs a
  new frame: the iframe is keyed by conversation *and* session. The pinned
  `view_id` travels with the source, so the remount renews its own lease instead
  of registering a second fullscreen view.

## Diagnostics

| Endpoint | Purpose |
| --- | --- |
| `GET /task-progress/status` | resolved paths, registered tool and skill names, bridge state, recorded panel session, and `diagnostics` |
| `GET /task-progress/session` | the session the panel should show |
| `POST /task-progress/diag` | client reports; the host keeps the last 40 as `diagnostics.events` |

`diagnostics` also counts `pageLoads` (how often `app.html` was served) and
`rpcCalls`. The card and the panel POST `card-frame-loaded`, `panel-mounted`
(with the frame height it computed and the session it resolved) and
`panel-frame-loaded`, so a mounted-but-empty surface and a never-mounted one are
distinguishable from the host without asking the user to describe the screen.

## Not yet adapted

- `hooks/hooks.json` (Codex lifecycle reminders: SessionStart, UserPromptSubmit,
  PreToolUse, PostToolUse, SubagentStart). DSH has different extension points, so
  planning reminders reach the model only through the `track-progress` skill text.
- Codex `interface.*` metadata (composer icon, default prompts, marketplace
  category). The bundle carries `icon` and the plugin manager's own page instead.
- MCP Apps `openLinks` / file download: export still writes to
  `~/Downloads/Task Progress Exports` on the Host, as in Codex.
