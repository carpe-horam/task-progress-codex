/**
 * Task Progress — DeepSeekRuntime (dsh) host half.
 *
 * One plugin row owns everything the Codex packaging splits across manifest files:
 *
 *   1. the MCP tools (`.mcp.json` in Codex) — registered through `ctx.tools`;
 *   2. the shipped `track-progress` skills (`"skills"` in the Codex manifest) —
 *      registered through `ctx.skills`;
 *   3. the MCP Apps panel (`app.html`) — served over `ctx.webServer` with the
 *      JSON-RPC relay its `postMessage` traffic needs, because DeepSeekRuntime has
 *      no MCP Apps host.
 *
 * All three share one stdio bridge to `mcp_bridge.py`, so the panel and the tools
 * observe the same background service the Codex plugin uses.
 */
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'

import { StdioMcpBridge } from './mcp-stdio.js'
import { PLUGIN_VERSION, appHtmlPath, launcherCommand, mcpBridgeScript, pluginDir } from './paths.js'
import { registerSkills } from './skills.js'
import { registerTools } from './tools.js'

export const name = 'dsh-task-progress-plugin'

/**
 * Where the conversation -> progress-session bindings live. They are the only
 * state this plugin owns: without a file, a restart erased every binding and each
 * conversation's tab fell back to "当前会话暂无任务" until the agent next called a
 * progress tool. `TASK_PROGRESS_DSH_BINDINGS` overrides the path for tests.
 */
export function bindingFilePath() {
  const override = process.env.TASK_PROGRESS_DSH_BINDINGS
  if (typeof override === 'string' && override) return override
  return join(homedir(), '.cache', 'task-progress', 'dsh-bindings.json')
}

const UUID_LIKE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Read the stored bindings; a missing or malformed file is simply empty state. */
export async function loadBindings(file = bindingFilePath()) {
  const state = { bindings: new Map(), last: null }
  let raw
  try {
    raw = JSON.parse(await readFile(file, 'utf8'))
  } catch {
    return state
  }
  const saved = raw?.bindings
  if (saved && typeof saved === 'object') {
    for (const [hostSession, taskSession] of Object.entries(saved)) {
      if (typeof hostSession !== 'string' || !hostSession) continue
      if (typeof taskSession !== 'string' || !UUID_LIKE.test(taskSession)) continue
      state.bindings.set(hostSession, taskSession)
    }
  }
  if (typeof raw?.last === 'string' && UUID_LIKE.test(raw.last)) state.last = raw.last
  return state
}

/** Write the bindings atomically so a crash cannot leave a half-written file. */
export async function saveBindings(state, file = bindingFilePath()) {
  const payload = {
    version: 1,
    bindings: Object.fromEntries(state.bindings ?? []),
    last: state.last ?? null,
  }
  const temporary = `${file}.tmp`
  await mkdir(dirname(file), { recursive: true })
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, 'utf8')
  try {
    await rename(temporary, file)
  } catch (error) {
    try { await unlink(temporary) } catch {}
    throw error
  }
}

/** No hard service dependency: each concern degrades on its own. */
export const inject = []

const PANEL_PATH = '/task-progress'
const PANEL_HTML_PATH = '/task-progress/app.html'
const RPC_PATH = '/task-progress/rpc'
const DIAG_PATH = '/task-progress/diag'
const DIAG_LIMIT = 40
const PANEL_VERSION = `dsh-task-progress@${PLUGIN_VERSION}`
const TOOL_REGISTRATION_DEADLINE_MS = 20000
/** Tools whose call carries the Task Progress session id the panel should show. */
const SESSION_TOOLS = new Set(['render_progress', 'get_progress', 'get_progress_view', 'init_task_network', 'init_progress'])
/**
 * Calls that only read. Everything else can change task state, and a change is
 * what an open tab must notice; counting reads here would make the tab's own
 * snapshot push bump the counter and re-trigger itself.
 */
const READ_TOOLS = new Set([
  'get_progress', 'get_progress_view', 'check_ui_view',
  // View-lease bookkeeping: a fullscreen view renews its lease every few seconds,
  // and counting that as progress made the counter drift without any data change.
  'register_ui_view', 'unregister_ui_view',
])

function json(res, status, value) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  })
  res.end(JSON.stringify(value))
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', chunk => chunks.push(Buffer.from(chunk)))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

function createBridge(ctx) {
  const bridge = new StdioMcpBridge({
    command: launcherCommand(),
    args: [mcpBridgeScript],
    cwd: pluginDir,
    version: PLUGIN_VERSION,
    logger: ctx.logger,
  })
  return {
    raw: bridge,
    async listTools() {
      const response = await bridge.request({ jsonrpc: '2.0', id: 0, method: 'tools/list', params: {} })
      return response?.result?.tools ?? []
    },
    async call(toolName, args) {
      const response = await bridge.request({
        jsonrpc: '2.0',
        id: 0,
        method: 'tools/call',
        params: { name: toolName, arguments: args },
      })
      if (response?.error) throw new Error(response.error.message || 'Task Progress tool call failed')
      return response?.result
    },
    dispose: () => bridge.dispose(),
  }
}

export async function createPanelRoute(
  ctx,
  bridge,
  status = {},
  panelSession = () => null,
  recordSession = () => {},
  bindSession = () => false,
  clearSession = () => {},
  sessionRevision = () => 0,
) {
  // `panelSession.bindings` is filled by apply() with the current map size, so the
  // status answer stays a read-only projection.
  const bindingCount = () => (typeof panelSession.bindings === 'number' ? panelSession.bindings : null)
  const webServer = ctx.get?.('webServer')
  if (!webServer || typeof webServer.register !== 'function') {
    ctx.logger?.warn?.('dsh-task-progress: webServer is unavailable; the progress panel stays unserved')
    return null
  }

  const html = await readFile(appHtmlPath, 'utf8')
  const page = html.replaceAll('__TASK_PROGRESS_VERSION__', PANEL_VERSION)
  // Diagnostics: a mounted panel necessarily loads this page. A zero count after
  // opening the panel means the iframe never mounted, which no amount of CSS fixes.
  const diagnostics = {
    pageLoads: 0, rpcCalls: 0, lastPath: null, events: [], rpcMethods: [],
    /** What the frame claims to be showing: it registers with its current source. */
    registrations: [],
  }

  const handler = async (req, res) => {
    const url = new URL(req.url || PANEL_PATH, 'http://task-progress.local')

    if (url.pathname === PANEL_HTML_PATH || url.pathname === `${PANEL_PATH}/`) {
      if (req.method !== 'GET') return json(res, 405, { error: 'GET required' })
      diagnostics.pageLoads += 1
      diagnostics.lastPath = url.pathname
      res.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      })
      return res.end(page)
    }

    if (url.pathname === DIAG_PATH) {
      // A view can only report from the browser, so this is the one honest way to
      // learn whether a surface mounted and what it measured.
      if (req.method !== 'POST') return json(res, 405, { error: 'POST required' })
      try {
        const payload = JSON.parse(await readBody(req))
        diagnostics.events.push({
          at: new Date().toISOString(),
          kind: typeof payload?.kind === 'string' ? payload.kind.slice(0, 40) : 'unknown',
          data: payload?.data ?? null,
        })
        if (diagnostics.events.length > DIAG_LIMIT) diagnostics.events.splice(0, diagnostics.events.length - DIAG_LIMIT)
      } catch {
        return json(res, 400, { error: 'Invalid JSON body' })
      }
      return json(res, 200, { ok: true })
    }

    if (url.pathname === `${PANEL_PATH}/session`) {
      // The tool view cannot depend on how the Chat node exposes call arguments,
      // so the host answers from the calls it actually executed. `dsh` scopes the
      // answer to one conversation.
      const hostSession = url.searchParams.get('dsh')
      const bound = panelSession(hostSession)
      if (bound) {
        return json(res, 200, {
          session: bound,
          bound: true,
          revision: sessionRevision(bound),
        })
      }
      return json(res, 200, { session: bound ?? null, bound: false, revision: 0 })
    }

    if (url.pathname === `${PANEL_PATH}/bind`) {
      if (req.method !== 'POST') return json(res, 405, { error: 'POST required' })
      try {
        const payload = JSON.parse(await readBody(req))
        if (!bindSession(payload?.dsh, payload?.session)) {
          return json(res, 400, { error: 'dsh and session are required' })
        }
      } catch {
        return json(res, 400, { error: 'Invalid JSON body' })
      }
      return json(res, 200, { ok: true })
    }

    if (url.pathname === `${PANEL_PATH}/status`) {
      return json(res, 200, {
        ...status,
        pluginDir,
        launcher: launcherCommand(),
        bridgeAlive: Boolean(bridge.raw.proc),
        bindings: bindingCount(),
        panelSession: panelSession.last ?? null,
        diagnostics,
      })
    }

    if (url.pathname !== RPC_PATH) return json(res, 404, { error: 'not found' })
    if (req.method !== 'POST') return json(res, 405, { error: 'POST required' })
    diagnostics.rpcCalls += 1

    let message
    try {
      message = JSON.parse(await readBody(req))
    } catch {
      return json(res, 400, { error: 'Invalid JSON body' })
    }
    if (!message || message.jsonrpc !== '2.0' || typeof message.method !== 'string') {
      return json(res, 400, { error: 'Invalid JSON-RPC request' })
    }
    try {
      const label = `${message.method}${message.params?.name ? `:${message.params.name}` : ''}`
      diagnostics.rpcMethods.push(label)
      if (diagnostics.rpcMethods.length > 24) diagnostics.rpcMethods.shift()
      // A view that applied a new snapshot re-registers with that snapshot's
      // sequence, so this list shows whether pushed data actually landed.
      if (message.params?.name === 'register_ui_view') {
        diagnostics.registrations.push({
          at: new Date().toISOString(),
          viewSequence: message.params?.arguments?.view_sequence ?? null,
          stateRevision: message.params?.arguments?.state_revision ?? null,
          mode: message.params?.arguments?.display_mode ?? null,
        })
        if (diagnostics.registrations.length > 24) diagnostics.registrations.shift()
      }
    } catch {}

    // The panel's own tool calls carry the session too, so the host can re-bind
    // after a restart even before the agent has called anything.
    try {
      if (message.method === 'tools/call') {
        const name = message.params?.name
        const session = message.params?.arguments?.session_id
        if (name === 'clear_progress') {
          clearSession(session)
        } else if (typeof session === 'string' && session) {
          recordSession(session, !READ_TOOLS.has(name))
        }
      }
    } catch {}

    if (!Object.prototype.hasOwnProperty.call(message, 'id')) {
      try {
        bridge.raw.notifyRaw(message)
      } catch (error) {
        ctx.logger?.debug?.(`dsh-task-progress: notification dropped: ${String(error)}`)
      }
      res.writeHead(202)
      return res.end()
    }

    try {
      return json(res, 200, await bridge.raw.request(message))
    } catch (error) {
      return json(res, 502, {
        jsonrpc: '2.0',
        id: message.id ?? null,
        error: { code: -32000, message: String(error?.message || error) },
      })
    }
  }

  return webServer.register({ kind: 'prefix', path: PANEL_PATH, handler })
}

export async function apply(ctx) {
  const bridge = createBridge(ctx)
  ctx.effect(() => () => bridge.dispose())

  const status = { version: PANEL_VERSION, tools: { state: 'registering', registered: [] } }
  /**
   * Host session -> Task Progress session. A single "latest" value made every
   * conversation show the same progress, so each binding is stored per host
   * SessionId and the panel asks for its own.
   */
  const bindings = new Map()
  /** Bumped on every progress tool call so a view can tell stale from fresh. */
  const revisions = new Map()
  let lastPanelSession = null
  let saveTimer = null

  const persistSoon = () => {
    if (saveTimer !== null) return
    saveTimer = setTimeout(() => {
      saveTimer = null
      void saveBindings({ bindings, last: lastPanelSession }).catch((error) => {
        ctx.logger?.debug?.(`dsh-task-progress: could not store bindings: ${String(error)}`)
      })
    }, 250)
    saveTimer.unref?.()
  }

  const remember = (hostSession, taskSession, bump = true) => {
    if (typeof taskSession !== 'string' || !taskSession) return
    lastPanelSession = taskSession
    if (typeof hostSession === 'string' && hostSession) bindings.set(hostSession, taskSession)
    if (bump) revisions.set(taskSession, (revisions.get(taskSession) ?? 0) + 1)
    persistSoon()
  }

  // Restore what the previous process knew: the task data outlives us, and a tab
  // reopened in the same conversation should show it without waiting for a tool call.
  try {
    const stored = await loadBindings()
    for (const [hostSession, taskSession] of stored.bindings) bindings.set(hostSession, taskSession)
    if (stored.last) lastPanelSession = stored.last
  } catch (error) {
    ctx.logger?.debug?.(`dsh-task-progress: could not restore bindings: ${String(error)}`)
  }

  const panelSession = (hostSession) => {
    if (typeof hostSession === 'string' && hostSession && bindings.has(hostSession)) {
      return bindings.get(hostSession)
    }
    // No host id (older shell) or nothing bound yet: fall back to the last call.
    if (typeof hostSession === 'string' && hostSession) return null
    return lastPanelSession
  }

  const onCall = (toolName, args, result, exec) => {
    // `clear_progress` is the archive path: once a session's progress is gone,
    // every conversation bound to it must fall back to the empty state instead of
    // pointing at data that no longer exists.
    if (toolName === 'clear_progress') {
      forget(args?.session_id ?? result?.structuredContent?.session_id)
      return
    }
    // Any progress tool that names a session binds it; only mutating calls bump
    // the revision an open tab watches.
    const session = args?.session_id
      ?? result?.structuredContent?.session_id
      ?? result?.structuredContent?.progress?.session_id
    remember(exec?.agent?.id, session, !READ_TOOLS.has(toolName))
  }

  status.skills = await registerSkills(ctx, ctx.logger)

  // Mounting must not hinge on the interpreter: the MCP service install and
  // health check can be slow on a cold machine, and a hung child would otherwise
  // stall every plugin behind this row. Registration finishes in the background
  // when the deadline passes, and `/task-progress/status` reports the outcome.
  const registration = registerTools(ctx, bridge, { onCall }).then(result => {
    status.tools = result
    ctx.logger?.info?.(
      `dsh-task-progress: ${result.registered.length} tools, ${status.skills.registered.length} skills`,
    )
    return result
  })
  status.tools = await Promise.race([
    registration,
    new Promise(resolve => {
      const timer = setTimeout(
        () => resolve({ state: 'registering', registered: [], deferred: true }),
        TOOL_REGISTRATION_DEADLINE_MS,
      )
      timer.unref?.()
    }),
  ])

  /** Drop every binding that points at a session which no longer has progress. */
  const forget = (taskSession) => {
    if (typeof taskSession !== 'string' || !taskSession) return
    let changed = false
    for (const [hostSession, bound] of bindings) {
      if (bound !== taskSession) continue
      bindings.delete(hostSession)
      changed = true
    }
    if (lastPanelSession === taskSession) lastPanelSession = null
    revisions.delete(taskSession)
    if (changed || lastPanelSession === null) persistSoon()
  }

  const recordSession = (session, bump = true) => { remember(undefined, session, bump) }
  const bindSession = (hostSession, taskSession) => {
    if (typeof hostSession !== 'string' || !hostSession) return false
    if (typeof taskSession !== 'string' || !taskSession) return false
    // Re-declaring a binding is not progress: bumping here would let a card that
    // re-posts its binding (or a tab that re-reads) drive the refresh loop.
    remember(hostSession, taskSession, false)
    return true
  }
  // Status reads these projections; the map itself stays inside apply().
  Object.defineProperty(panelSession, 'bindings', { get: () => bindings.size })
  Object.defineProperty(panelSession, 'last', { get: () => lastPanelSession })
  const sessionRevision = (taskSession) => revisions.get(taskSession) ?? 0
  const disposeRoute = await createPanelRoute(
    ctx, bridge, status, panelSession, recordSession, bindSession, forget, sessionRevision,
  )
  if (disposeRoute) ctx.effect(() => disposeRoute)
}
