#!/usr/bin/env python3
"""Static and behavioural checks for the DeepSeekRuntime (dsh) bundle.

The bundle is packaging, not a second implementation: it must keep pointing at
the Codex plugin's own `server.py`, `mcp_bridge.py`, `app.html` and skill bodies.
These checks fail loudly when the two packagings drift, when the bundle acquires
a dependency a linked profile cannot resolve, or when the panel stops answering
the MCP Apps handshake the shared `app.html` expects.

Behavioural cases run the real ES modules through Node; the panel is exercised by
importing the host module with a stand-in cordis context, so no dsh install and no
running profile are required.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "task-progress"
BUNDLE = PLUGIN / "dsh"
MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"

NODE = shutil.which("node")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class DshBundlePackagingTest(unittest.TestCase):
    def test_manifest_matches_codex_release(self):
        plugin_manifest = read_json(MANIFEST)
        bundle = read_json(BUNDLE / "package.json")
        self.assertEqual(
            bundle["version"],
            plugin_manifest["version"],
            "the bundle version must follow the Codex manifest, which is the release source of truth",
        )
        self.assertEqual(bundle["name"], "dsh-task-progress-plugin")

    def test_bundle_declares_patch_and_client(self):
        bundle = read_json(BUNDLE / "package.json")
        self.assertEqual(bundle["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertEqual(bundle["dsh"]["client"]["platform"], "web")
        self.assertEqual(bundle["exports"]["."], "./lib/index.js")
        self.assertEqual(bundle["exports"]["./client"], "./lib/client.js")
        self.assertEqual(bundle["type"], "module")

    def test_manifest_metadata_stays_inside_the_bundle(self):
        """dsh rejects a plugin whose metadata points outside its manifest directory.

        Observed failure: `icon: ../assets/task-progress-icon.svg` produced
        "icon must remain inside its manifest directory" and the whole bundle failed
        to load, so every manifest path is checked here and the icon must be a real
        file next to package.json.
        """
        bundle = read_json(BUNDLE / "package.json")
        checked = {}

        def record(field, value):
            if isinstance(value, str):
                checked[field] = value

        record("icon", bundle.get("icon"))
        record("main", bundle.get("main"))
        for key, value in (bundle.get("exports") or {}).items():
            record(f"exports[{key}]", value)
        record("dsh.bundle.patch", (bundle.get("dsh") or {}).get("bundle", {}).get("patch"))

        for field, value in checked.items():
            self.assertFalse(
                value.startswith("/") or value.startswith("../") or ".." in Path(value).parts,
                f"{field} must stay inside the bundle directory, got {value!r}",
            )
            self.assertTrue((BUNDLE / value).exists(), f"{field} points at a missing file: {value}")

        icon = bundle.get("icon")
        self.assertIsNotNone(icon, "the bundle must declare its own icon")
        self.assertTrue((BUNDLE / icon).is_file(), "the icon file must exist inside the bundle")
        self.assertIn(icon.lstrip("./"), bundle.get("files") or [], "the icon must be shipped in `files`")

    def test_patch_inserts_one_row_for_this_package(self):
        patch = (BUNDLE / "cordis.patch.yml").read_text(encoding="utf-8")
        package_name = read_json(BUNDLE / "package.json")["name"]
        self.assertIn("insert:", patch)
        self.assertIn(f"name: '{package_name}'", patch)
        # An MCP-client row would need a static absolute command; the bundle
        # registers tools itself instead.
        self.assertNotIn("dsh-mcp-client", patch)
        self.assertEqual(patch.count("id: task-progress"), 1)

    def test_bundle_has_no_unresolvable_dsh_dependency(self):
        """A linked profile bundle cannot resolve @deepseek-ai/* in this dsh version."""
        for module in (BUNDLE / "lib").glob("*.js"):
            source = module.read_text(encoding="utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") and "@deepseek-ai/" in stripped:
                    self.fail(f"{module.name} imports a dsh package: {stripped}")
        bundle = read_json(BUNDLE / "package.json")
        self.assertNotIn("dependencies", bundle)
        self.assertNotIn("peerDependencies", bundle)

    def test_shared_assets_are_referenced_not_copied(self):
        """No duplicated server, panel or skill copy inside the bundle."""
        self.assertFalse((BUNDLE / "app.html").exists())
        self.assertFalse((BUNDLE / "server.py").exists())
        self.assertFalse((BUNDLE / "skills").exists())
        paths_module = (BUNDLE / "lib" / "paths.js").read_text(encoding="utf-8")
        self.assertIn("appHtmlPath", paths_module)
        self.assertIn("skillsDir", paths_module)
        self.assertIn("mcpBridgeScript", paths_module)

    def test_skills_stay_the_codex_bodies(self):
        skill_dirs = sorted(path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md"))
        self.assertEqual(skill_dirs, ["cleanup-task-progress-history", "track-progress"])
        for name in skill_dirs:
            frontmatter = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(frontmatter.startswith("---\nname: "), f"{name} must keep YAML frontmatter")

    def test_panel_placeholder_is_replaced_at_serve_time(self):
        host = (BUNDLE / "lib" / "index.js").read_text(encoding="utf-8")
        self.assertIn("__TASK_PROGRESS_VERSION__", host)
        self.assertIn("replaceAll", host)

    def test_card_is_keyed_by_the_real_tool_name(self):
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn('const RENDER_TOOL = "render_progress"', client)
        self.assertIn('name: "tool.call.toolview"', client)
        # DeepSeekRuntime keys tool views by wire name; an mcp__ prefix would never render.
        self.assertNotIn("mcp__task_progress__", client)

    def test_panel_is_a_right_sidebar_tab_not_a_main_panel(self):
        """Follows how dsh-context places itself in the right column.

        The shell owns the right-sidebar tab list through `sidebarRightTabs`; the
        pane body and its title are keyed slots dispatched with the same id. A main
        panel or left-list entry would cover the conversation instead.
        """
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn('ctx.inject(["sidebarRightTabs", "slots"]', client)
        self.assertIn("sidebarRightTabs", client)
        self.assertIn('scope.slots.inject("sidebar.right.pane.tab"', client)
        self.assertIn('scope.slots.inject("sidebar.right.pane.tab.title"', client)
        self.assertIn('const TAB_ID = "task-progress"', client)
        self.assertNotIn('ctx.slots.inject("main"', client)
        self.assertNotIn('ctx.slots.inject("sidebar.panellist"', client)
        self.assertNotIn("dsh-task-progress-dock", client)
        # Both surfaces share one session, published when the card learns it.
        self.assertIn("publishSession", client)
        self.assertIn("/task-progress/session", client)

    def test_tab_body_is_the_embedded_ui_only(self):
        """The tab must not draw the same facts twice.

        An earlier revision also rendered a native title, progress bar and task
        tree beside the embedded app.html, so the panel showed plain text and the
        real UI at once.
        """
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn('src: PANEL_SRC', client)
        self.assertNotIn("dsh-task-progress-summary", client)
        self.assertNotIn("dsh-task-progress-list", client)
        self.assertNotIn("dsh-task-progress-node", client)
        self.assertNotIn("在新标签页打开", client)
        # No placeholder sentence either: the app draws its own empty state.
        self.assertNotIn("还没有可显示的进度", client)

    def test_both_surfaces_read_the_shell_colour_scheme(self):
        """The scheme comes from the theme service, not from a DOM attribute.

        Reading `documentElement.dataset.theme` produced `light` for a dark shell,
        so the embedded app.html painted a white panel inside a dark window.
        """
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn("function hostTheme(themeService)", client)
        self.assertIn("active?.colorScheme", client)
        self.assertIn('ctx?.get?.("theme")', client)
        self.assertNotIn("document.documentElement.dataset.theme", client)
        # Both surfaces forward later changes through the documented notification.
        self.assertIn("function watchHostTheme(ctx, onChange)", client)
        self.assertIn('ctx?.on?.("theme/change"', client)
        self.assertEqual(client.count('method: "ui/notifications/host-context-changed"'), 2)

    def test_frame_is_remounted_when_the_bound_session_changes(self):
        """app.html adopts the first session it sees and drops every other one.

        `receiveResult` returns early when an incoming session differs from its own
        adopted session, so a tab bound to a previous task session could never show
        a newer one: it stayed on the old task until the tab was closed and reopened.
        """
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn('key: `${frameSrc}#${session ?? "none"}`', client)
        # The pinned view id survives the remount, so the lease is renewed not duplicated.
        self.assertIn("view_id=tab-", client)

    def test_open_tab_notices_new_work_without_a_remount(self):
        """The tab looked frozen until it was closed and reopened.

        app.html only re-read on mount, so records written while it stayed open
        never appeared. The host now counts a revision per task session, reports it
        with the binding, and the tab pushes a fresh full snapshot into the frame
        whenever that revision moves.
        """
        host = (BUNDLE / "lib" / "index.js").read_text(encoding="utf-8")
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")

        # Host: a counter that moves on every mutating call and is reported.
        self.assertIn("const revisions = new Map()", host)
        self.assertIn("revisions.set(taskSession,", host)
        # Reads must not move it: the tab's own snapshot push is a read, and a bump
        # there would re-trigger the push forever.
        self.assertIn("const READ_TOOLS = new Set(", host)
        # A fullscreen view renews its lease constantly; that is bookkeeping, not
        # progress, so it must not move the counter either.
        self.assertIn("'register_ui_view', 'unregister_ui_view'", host)
        self.assertIn("diagnostics.rpcMethods", host)
        self.assertIn("!READ_TOOLS.has(toolName)", host)
        # Re-declaring a binding must not move the counter either.
        self.assertIn("remember(hostSession, taskSession, false)", host)
        self.assertIn("!READ_TOOLS.has(name)", host)
        self.assertIn("revision: sessionRevision(bound)", host)
        self.assertIn("revisions.delete(taskSession)", host)

        # Client: the tab watches the revision and pushes a `full` snapshot.
        self.assertIn("payload.revision", client)
        self.assertIn("const revision = binding.revision;", client)
        self.assertIn("[pushSnapshot, revision]", client)
        self.assertIn('view: "full"', client)
        self.assertIn('method: "ui/notifications/tool-result"', client)
        # A timer keeps the frame in sync even if the revision signal is missed,
        # and each attempt is reported so the host can see it happened.
        self.assertIn("void pushSnapshot(); }, 5000)", client)
        self.assertIn('reportDiag("panel-sync"', client)

    def test_tab_pins_its_view_identity(self):
        """A remount must renew its own lease, not register a second fullscreen view.

        app.html generated a random view id per frame, so switching conversations
        and coming back left the old lease in place and the new frame was answered
        with `duplicate_fullscreen` ("本会话已有完整进度页打开…").
        """
        panel = (BUNDLE.parent / "app.html").read_text(encoding="utf-8")
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn("pinnedViewId", panel)
        self.assertIn("get('view_id')", panel)
        self.assertIn("viewId = pinnedViewId ||", panel)
        self.assertIn("?view_id=tab-${encodeURIComponent(conversationId)}", client)
        self.assertIn("src: frameSrc", client)

    def test_progress_is_scoped_per_conversation(self):
        """One global "latest session" made every conversation show the same task.

        The binding is keyed by the host SessionId: the tool run context carries
        `agent.id`, the card publishes the pair it knows, and the tab asks for its
        own conversation instead of taking whatever ran last.
        """
        host = (BUNDLE / "lib" / "index.js").read_text(encoding="utf-8")
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")

        # Host: per-session map fed by the tool run context.
        self.assertIn("const bindings = new Map()", host)
        self.assertIn("exec?.agent?.id", host)
        self.assertIn("remember(exec?.agent?.id, session, !READ_TOOLS.has(toolName))", host)
        self.assertIn("bindings.set(hostSession, taskSession)", host)
        # Lookup honours ?dsh= and reports whether the answer was bound.
        self.assertIn("url.searchParams.get('dsh')", host)
        self.assertIn("bound: false", host)

        # Client: the tab scopes its lookup, and the card publishes the pair.
        self.assertIn('useHostBinding(true, conversationId)', client)
        self.assertIn("?dsh=${encodeURIComponent(conversationId)}", client)
        self.assertIn('"/task-progress/bind"', client)
        self.assertIn("JSON.stringify({ dsh: props.sessionId, session: activeSession })", client)
        # The shared store must not decide the tab's session: the tab reads the
        # scoped binding (with its revision) and nothing else.
        self.assertIn("const binding = useHostBinding(true, conversationId);", client)
        self.assertIn("const session = binding.session;", client)

    def test_fullscreen_request_surfaces_the_right_column(self):
        """"打开进度页" used to cover the window with an overlay.

        The panel now lives in the right column, so a fullscreen request opens that
        column and keeps the card inline; the overlay remains only as a fallback
        for a shell without a layout service.
        """
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn('layout.openRightbar(true, false)', client)
        self.assertIn('let granted = "inline"', client)
        # The overlay survives as the fallback branch, not as the default path.
        self.assertIn("setFullscreen(true)", client)

    def test_frame_paints_no_border_of_its_own(self):
        """A light-token border drew a white ring around the dark panel."""
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn(".dsh-task-progress-frame{display:block;width:100%;border:0;background:transparent", client)
        self.assertNotIn("border:1px solid var(--dsw-alias-border-subtle", client)
        self.assertNotIn("background:var(--dsw-alias-bg-base,#fff)", client)

    def test_card_mounts_the_frame_before_a_session_is_known(self):
        """A missing session must not suppress the iframe, or no view is ever registered."""
        client = (BUNDLE / "lib" / "client.js").read_text(encoding="utf-8")
        self.assertIn("activeSession", client)
        self.assertIn("const frameRef = React.useRef(null);", client)
        self.assertNotIn("if (!progressSession) return;", client)


@unittest.skipUnless(NODE, "Node.js is required for the bundle behaviour checks")
class DshBundleBehaviourTest(unittest.TestCase):
    def run_node(self, script: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as handle:
            handle.write(script)
            path = handle.name
        try:
            result = subprocess.run(
                [NODE, path],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=180,
            )
        finally:
            os.unlink(path)
        self.assertEqual(result.returncode, 0, f"node failed:\n{result.stdout}\n{result.stderr}")
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_bindings_survive_a_restart(self):
        """The map was memory-only, so a restart emptied every conversation's tab.

        Task data lives on disk in the shared service; the only plugin-owned state
        is the conversation -> session binding, so it is written atomically next to
        that data and read back on mount.
        """
        script = f"""
        import {{ mkdirSync, writeFileSync }} from 'node:fs'
        import {{ join }} from 'node:path'
        const module = await import({json.dumps(str(BUNDLE / "lib" / "index.js"))})
        const dir = '/tmp/dsh-bindings-test-' + process.pid
        mkdirSync(dir, {{ recursive: true }})
        const file = join(dir, 'bindings.json')
        const state = {{
          bindings: new Map([
            ['session-a', '11111111-1111-4111-8111-111111111111'],
            ['session-b', '22222222-2222-4222-8222-222222222222'],
          ]),
          last: '22222222-2222-4222-8222-222222222222',
        }}
        await module.saveBindings(state, file)
        const restored = await module.loadBindings(file)
        const junk = join(dir, 'junk.json')
        writeFileSync(junk, '{{ not json')
        const empty = await module.loadBindings(junk)
        const invalid = join(dir, 'invalid.json')
        writeFileSync(invalid, JSON.stringify({{ bindings: {{ keep: 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', drop: 'nope' }} }}))
        const filtered = await module.loadBindings(invalid)
        console.log(JSON.stringify({{
          restored: [...restored.bindings],
          last: restored.last,
          corruptEmpty: empty.bindings.size === 0 && empty.last === null,
          filtered: [...filtered.bindings],
        }}))
        """
        data = self.run_node(script)
        self.assertEqual(sorted(data["restored"]), [
            ["session-a", "11111111-1111-4111-8111-111111111111"],
            ["session-b", "22222222-2222-4222-8222-222222222222"],
        ])
        self.assertEqual(data["last"], "22222222-2222-4222-8222-222222222222")
        self.assertTrue(data["corruptEmpty"])
        # A malformed mapping is dropped instead of being served to a panel.
        self.assertEqual(data["filtered"], [["keep", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"]])

        host = (BUNDLE / "lib" / "index.js").read_text(encoding="utf-8")
        self.assertIn("await loadBindings()", host)
        self.assertIn("persistSoon()", host)
        self.assertIn("rename(temporary, file)", host)

    def test_parameters_follow_the_dsh_dialect(self):
        script = f"""
        const {{ toParameters, resultText }} = await import({json.dumps((BUNDLE / 'lib' / 'tools.js').as_uri())})
        const converted = toParameters({{
          type: 'object',
          properties: {{
            session_id: {{ type: 'string', description: 'sid' }},
            view: {{ type: 'string', enum: ['overview', 'full'], default: 'overview' }},
            task_ids: {{ type: 'array', minItems: 1, items: {{ type: 'string' }} }},
            limit: {{ type: 'integer', minimum: 1, maximum: 20 }},
            nested: {{ anyOf: [{{ type: 'object', properties: {{ a: {{ type: 'string' }} }} }}] }},
          }},
          required: ['session_id'],
        }})
        const text = resultText({{ content: [{{ type: 'text', text: 'a' }}, {{ type: 'text', text: 'b' }}] }})
        const errorText = resultText({{ isError: true, content: [{{ type: 'text', text: 'boom' }}] }})
        console.log(JSON.stringify({{ converted, text, errorText }}))
        """
        data = self.run_node(script)
        converted = data["converted"]
        # `required: true` on the property replaces JSON Schema's `required` array.
        self.assertTrue(converted["session_id"]["required"])
        self.assertNotIn("required", converted["view"])
        self.assertEqual(converted["view"]["enum"], ["overview", "full"])
        self.assertEqual(converted["view"]["default"], "overview")
        self.assertEqual(converted["task_ids"]["type"], "array")
        self.assertEqual(converted["task_ids"]["items"]["type"], "string")
        self.assertEqual(converted["task_ids"]["minItems"], 1)
        self.assertEqual(converted["limit"]["minimum"], 1)
        self.assertEqual(converted["nested"]["type"], "object")
        self.assertEqual(converted["nested"]["properties"]["a"]["type"], "string")
        self.assertEqual(data["text"], "a\n\nb")
        self.assertEqual(data["errorText"], "boom")

    def test_skills_register_from_the_shared_bodies_into_a_resource_directory(self):
        script = f"""
        const {{ registerSkills }} = await import({json.dumps((BUNDLE / 'lib' / 'skills.js').as_uri())})
        const captured = []
        const ctx = {{
          get: name => name === 'skills' ? {{ register: skill => {{ captured.push(skill); return () => {{}} }} }} : undefined,
          effect: fn => fn(),
        }}
        const result = await registerSkills(ctx, {{ warn: () => {{}} }})
        console.log(JSON.stringify({{
          result,
          skills: captured.map(skill => ({{
            name: skill.name,
            descriptionLength: skill.description.length,
            content: skill.content.slice(0, 40),
            resourceBase: skill.resourceBase,
            invocation: skill.invocation,
          }})),
        }}))
        """
        data = self.run_node(script)
        names = sorted(data["result"]["registered"])
        self.assertEqual(names, ["cleanup-task-progress-history", "track-progress"])
        self.assertEqual(data["result"]["skipped"], [])
        for skill in data["skills"]:
            self.assertGreater(skill["descriptionLength"], 0)
            # Frontmatter is stripped: the body starts at the first Markdown heading.
            self.assertTrue(skill["content"].startswith("# "), skill["content"])
            self.assertEqual(skill["resourceBase"]["kind"], "directory")
            self.assertTrue(skill["resourceBase"]["path"].endswith(skill["name"]))
            self.assertTrue(skill["invocation"]["modelInvocable"])

    def test_tool_execute_forwards_to_the_server_and_renders_its_text(self):
        """Registering is not enough: the definition must relay and render."""
        script = f"""
        const {{ registerTools }} = await import({json.dumps((BUNDLE / 'lib' / 'tools.js').as_uri())})
        const registered = []
        const calls = []
        const ctx = {{
          logger: {{ debug() {{}}, info() {{}}, warn() {{}}, error() {{}} }},
          get: name => name === 'tools' ? {{ register: tool => {{ registered.push(tool); return () => {{}} }} }} : undefined,
          effect: fn => fn(),
        }}
        const bridge = {{
          listTools: async () => [
            {{ name: 'render_progress', description: 'show', inputSchema: {{ type: 'object', properties: {{ session_id: {{ type: 'string' }} }}, required: ['session_id'] }} }},
            {{ name: 'clear_progress', description: 'clear', inputSchema: {{ type: 'object', properties: {{}} }} }},
          ],
          call: async (name, args) => {{
            calls.push({{ name, args }})
            if (name === 'clear_progress') return {{ isError: true, content: [{{ type: 'text', text: 'refused' }}] }}
            return {{ content: [{{ type: 'text', text: '已记录。' }}], structuredContent: {{ session_id: args.session_id, task_instance_id: 't-1', epoch: 0 }} }}
          }},
        }}
        const result = await registerTools(ctx, bridge)
        const tool = registered.find(entry => entry.name === 'render_progress')
        const cleared = registered.find(entry => entry.name === 'clear_progress')
        const value = await tool.execute({{ session_id: 'abc' }})
        const rendered = tool.output.render({{ session_id: 'abc' }}, value)
        let failure = null
        try {{ await cleared.execute({{}}) }} catch (error) {{ failure = String(error.message) }}
        console.log(JSON.stringify({{
          registered: result.registered,
          failures: result.failures,
          value,
          rendered,
          hasOutputContract: Boolean(tool.output && tool.output.schema && tool.output.render),
          sessionRequired: tool.parameters.session_id.required,
          calls,
          failure,
          clearOutput: cleared.output.schema.type,
        }}))
        """
        data = self.run_node(script)
        self.assertEqual(sorted(data["registered"]), ["clear_progress", "render_progress"])
        self.assertEqual(data["failures"], [])
        # Both halves of an MCP result reach the model: the terse receipt text
        # alone leaves the agent without the ids it needs for the next call.
        self.assertTrue(data["value"].startswith("已记录。\n\n{"), data["value"])
        forwarded = json.loads(data["value"].split("\n\n", 1)[1])
        self.assertEqual(forwarded["session_id"], "abc")
        self.assertEqual(forwarded["task_instance_id"], "t-1")
        self.assertEqual(data["rendered"], [{"type": "text", "text": data["value"]}])
        self.assertTrue(data["hasOutputContract"])
        self.assertTrue(data["sessionRequired"])
        self.assertEqual(data["calls"][0], {"name": "render_progress", "args": {"session_id": "abc"}})
        # Server-side refusals surface as tool errors, not silent successes.
        self.assertEqual(data["failure"], "refused")
        self.assertEqual(data["clearOutput"], "string")

    def test_every_registered_component_actually_renders(self):
        """Runs the client bundle and calls each component once.

        The first sidebar panel threw "React is not defined" because two hooks
        referenced React from module scope, where only the createX(React)
        factories have it. A static check cannot see that, so the bundle is
        executed with a React stand-in and every registered component is invoked.
        """
        harness = f"""
        import {{ readFileSync }} from 'node:fs'
        import vm from 'node:vm'

        const source = readFileSync({json.dumps(str(BUNDLE / 'lib' / 'client.js'))}, 'utf8')
        let loaded = null

        const React = {{
          createElement: (type, props, ...children) => ({{ type, props, children }}),
          useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {{}}],
          useEffect: () => {{}},
          useMemo: (fn) => fn(),
          useCallback: (fn) => fn,
          useRef: (value) => ({{ current: value }}),
          Component: class {{ constructor(props) {{ this.props = props }} }},
        }}
        React.Component.prototype.setState = () => {{}}

        const sandbox = {{
          console,
          setInterval: () => 0, clearInterval: () => {{}},
          setTimeout: () => 0, clearTimeout: () => {{}},
          fetch: async () => ({{ ok: false, json: async () => null }}),
          document: {{ documentElement: {{ dataset: {{}} }}, createElement: () => ({{ style: {{}} }}), head: {{ appendChild() {{}} }} }},
          __ModuleLoader__: {{ load: (registration) => {{ loaded = registration }} }},
        }}
        sandbox.window = {{
          location: {{ origin: 'http://local' }},
          innerHeight: 900,
          addEventListener() {{}}, removeEventListener() {{}},
          __ModuleLoader__: sandbox.__ModuleLoader__,
        }}
        vm.createContext(sandbox)
        vm.runInContext(source, sandbox)

        const mod = loaded.factory((moduleName) => {{
          if (moduleName === 'react') return React
          throw new Error('unexpected require: ' + moduleName)
        }})

        const registrations = []
        const tabRegistrations = []
        const slots = {{
          inject: (slot, fn) => fn(),
          register: (declaration, component) => {{
            registrations.push({{ slot: declaration.name || slot, component }})
            return () => {{}}
          }},
        }}
        const ctx = {{
          logger: {{ debug() {{}}, info() {{}}, warn() {{}}, error() {{}} }},
          get: (service) => service === 'styles' ? {{ insert() {{}} }} : undefined,
          effect: (fn) => {{ const disposer = fn(); return () => {{ if (typeof disposer === 'function') disposer() }} }},
          inject: (dependencies, callback) => {{
            // Same shape as the shell: the callback receives a scoped context.
            const scoped = {{ ...ctx, slots, sidebarRightTabs: {{ register: (spec) => {{ tabRegistrations.push(spec); return () => {{}} }} }} }}
            const disposer = callback(scoped)
            return () => {{ if (typeof disposer === 'function') disposer() }}
          }},
          slots,
        }}
        mod.apply(ctx)

        const results = registrations.map((entry) => {{
          try {{
            const element = entry.component({{
              size: 16, active: true, sessionId: 'session-x',
              block: {{ args: {{ session_id: '11111111-2222-3333-4444-555555555555' }} }},
            }})
            return {{ slot: entry.slot, ok: element !== null && element !== undefined }}
          }} catch (error) {{
            return {{ slot: entry.slot, ok: false, error: String(error && error.message || error) }}
          }}
        }})

        console.log(JSON.stringify({{
          slots: registrations.map(r => r.slot),
          tabs: tabRegistrations.map(spec => ({{
            id: spec.id,
            kind: spec.kind,
            title: typeof spec.title === 'function' ? spec.title() : spec.title,
            guide: (spec.guide || []).map(entry => ({{ id: entry.id, order: entry.order }})),
          }})),
          results,
        }}))
        """
        data = self.run_node(harness)
        self.assertEqual(sorted(data["slots"]), [
            "sidebar.right.pane.tab",
            "sidebar.right.pane.tab.title",
            "tool.call.toolview",
        ])
        # The right-sidebar tab itself must be declared to the shell's own list.
        self.assertEqual(data["tabs"], [{
            "id": "task-progress",
            "kind": "task-progress",
            "title": "任务进度",
            "guide": [{"id": "task-progress", "order": 20}],
        }])
        for result in data["results"]:
            self.assertTrue(result["ok"], f"{result['slot']} failed to render: {result.get('error')}")

    def test_panel_route_serves_html_and_relays_rpc(self):
        """Uses the exported route factory with a stub bridge: no interpreter needed."""
        script = f"""
        const plugin = await import({json.dumps((BUNDLE / 'lib' / 'index.js').as_uri())})
        const routes = []
        const notified = []
        const calls = []
        const ctx = {{
          logger: {{ debug() {{}}, info() {{}}, warn() {{}}, error() {{}} }},
          get: name => name === 'webServer'
            ? {{ register: route => {{ routes.push(route); return () => {{}} }} }}
            : undefined,
          effect: fn => fn(),
        }}
        const bridge = {{
          raw: {{
            proc: {{}},
            notifyRaw: message => notified.push(message),
            request: async message => {{
              calls.push(message)
              if (message.method === 'nope') throw new Error('relay down')
              return {{ jsonrpc: '2.0', id: message.id, result: {{ ok: true }} }}
            }},
          }},
        }}
        let recorded = null
        const bound = []
        const cleared = []
        await plugin.createPanelRoute(
          ctx, bridge,
          {{ tools: {{ registered: ['render_progress'] }} }},
          () => '11111111-2222-3333-4444-555555555555',
          (session) => {{ recorded = session }},
          (dsh, task) => {{ bound.push([dsh, task]); return typeof dsh === 'string' && typeof task === 'string' }},
          (session) => {{ cleared.push(session); }},
        )
        const route = routes[0]

        function response() {{
          return {{ status: 0, headers: null, body: '',
            writeHead(status, headers) {{ this.status = status; this.headers = headers }},
            end(body) {{ this.body = body ?? '' }} }}
        }}
        function request(method, url, body) {{
          const chunks = body === undefined ? [] : [Buffer.from(JSON.stringify(body))]
          const req = {{ method, url, on: (event, handler) => {{
            if (event === 'data') for (const chunk of chunks) handler(chunk)
            if (event === 'end') handler()
            return req
          }} }}
          return req
        }}

        const page = response()
        await route.handler(request('GET', '/task-progress/app.html'), page)

        const status = response()
        await route.handler(request('GET', '/task-progress/status'), status)

        const session = response()
        await route.handler(request('GET', '/task-progress/session'), session)

        const bind = response()
        await route.handler(request('POST', '/task-progress/bind',
          {{ dsh: 'session-a', session: 'task-a' }}), bind)

        const badBind = response()
        await route.handler(request('POST', '/task-progress/bind', {{ dsh: 'only-dsh' }}), badBind)

        const clearedCall = response()
        await route.handler(request('POST', '/task-progress/rpc',
          {{ jsonrpc: '2.0', id: 12, method: 'tools/call',
             params: {{ name: 'clear_progress', arguments: {{ session_id: 'gone-session' }} }} }}), clearedCall)

        const diag = response()
        await route.handler(request('POST', '/task-progress/diag',
          {{ kind: 'panel-mounted', data: {{ frameHeight: 640 }} }}), diag)

        const ok = response()
        await route.handler(request('POST', '/task-progress/rpc',
          {{ jsonrpc: '2.0', id: 11, method: 'tools/call',
             params: {{ name: 'render_progress', arguments: {{ session_id: 'abc-session' }} }} }}), ok)

        const notification = response()
        await route.handler(request('POST', '/task-progress/rpc',
          {{ jsonrpc: '2.0', method: 'notifications/initialized' }}), notification)

        const failure = response()
        await route.handler(request('POST', '/task-progress/rpc',
          {{ jsonrpc: '2.0', id: 12, method: 'nope' }}), failure)

        const malformed = response()
        await route.handler(request('POST', '/task-progress/rpc', {{ method: 3 }}), malformed)

        const missing = response()
        await route.handler(request('GET', '/task-progress/nope'), missing)

        console.log(JSON.stringify({{
          name: plugin.name,
          inject: plugin.inject,
          routeKind: route.kind,
          routePath: route.path,
          pageStatus: page.status,
          pageType: page.headers['Content-Type'],
          pageBytes: page.body.length,
          placeholderLeft: page.body.includes('__TASK_PROGRESS_VERSION__'),
          versionInjected: /dsh-task-progress@\\d+\\.\\d+\\.\\d+/.test(page.body),
          statusBody: JSON.parse(status.body),
          sessionBody: JSON.parse(session.body),
          diagStatus: diag.status,
          bindStatus: bind.status,
          badBindStatus: badBind.status,
          bound,
          cleared,
          okStatus: ok.status,
          okBody: JSON.parse(ok.body),
          notificationStatus: notification.status,
          notifiedMethods: notified.map(message => message.method),
          failureStatus: failure.status,
          failureBody: JSON.parse(failure.body),
          malformedStatus: malformed.status,
          missingStatus: missing.status,
          relayedMethods: calls.map(message => message.method),
          recorded,
        }}))
        """
        data = self.run_node(script)
        self.assertEqual(data["name"], "dsh-task-progress-plugin")
        self.assertEqual(data["inject"], [])
        self.assertEqual(data["routeKind"], "prefix")
        self.assertEqual(data["routePath"], "/task-progress")
        self.assertEqual(data["pageStatus"], 200)
        self.assertEqual(data["pageType"], "text/html; charset=utf-8")
        self.assertGreater(data["pageBytes"], 100000)
        self.assertFalse(data["placeholderLeft"])
        self.assertTrue(data["versionInjected"])
        self.assertEqual(data["statusBody"]["tools"]["registered"], ["render_progress"])
        # The Chat node does not reliably expose call arguments, so the card asks
        # the host which session the last progress call belonged to.
        self.assertEqual(data["sessionBody"], {
            "session": "11111111-2222-3333-4444-555555555555",
            "bound": True,
            # A revision lets an open tab notice new work without a remount.
            "revision": 0,
        })
        # Client reports are the only browser-side evidence the host can retain.
        self.assertEqual(data["diagStatus"], 200)
        self.assertEqual(data["bindStatus"], 200)
        self.assertEqual(data["badBindStatus"], 400)
        self.assertEqual(data["bound"], [["session-a", "task-a"], ["only-dsh", None]])
        # Archiving a session must drop the bindings that pointed at it.
        self.assertEqual(data["cleared"], ["gone-session"])
        self.assertEqual(data["statusBody"]["diagnostics"]["events"], [])
        self.assertEqual(data["statusBody"]["diagnostics"]["pageLoads"], 1)
        self.assertEqual(data["okStatus"], 200)
        self.assertEqual(data["okBody"]["result"], {"ok": True})
        # JSON-RPC ids survive the hop: the panel matches replies by them.
        self.assertEqual(data["okBody"]["id"], 11)
        self.assertEqual(data["notificationStatus"], 202)
        self.assertEqual(data["notifiedMethods"], ["notifications/initialized"])
        self.assertEqual(data["failureStatus"], 502)
        self.assertEqual(data["failureBody"]["id"], 12)
        self.assertIn("relay down", data["failureBody"]["error"]["message"])
        self.assertEqual(data["malformedStatus"], 400)
        self.assertEqual(data["missingStatus"], 404)
        self.assertNotIn("notifications/initialized", data["relayedMethods"])
        # The panel's own tool calls carry the session, so the host can re-bind
        # after a restart even before the agent calls anything.
        self.assertEqual(data["recorded"], "abc-session")


if __name__ == "__main__":
    unittest.main()
