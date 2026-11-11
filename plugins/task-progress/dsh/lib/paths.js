/**
 * Path resolution for the DeepSeekRuntime (dsh) bundle.
 *
 * The bundle ships inside the Task Progress plugin directory
 * (`plugins/task-progress/dsh`), so the MCP server, the `app.html` panel and the
 * skill bodies all live one level up. A profile install links this package, but
 * a copied install also works because every path is derived from this module's
 * own location instead of a hard-coded checkout path.
 */
import { readFileSync, realpathSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** `<plugin>/dsh/lib` — the directory holding this module. */
const libDir = dirname(fileURLToPath(import.meta.url))

function resolveDir(path) {
  try {
    return realpathSync(path)
  } catch {
    return path
  }
}

/** `<plugin>/dsh` */
export const packageDir = resolveDir(join(libDir, '..'))
/** `<plugin>` — the Task Progress plugin root shared with the Codex plugin. */
export const pluginDir = resolveDir(join(packageDir, '..'))

/** The MCP Apps panel served to the Web GUI. */
export const appHtmlPath = join(pluginDir, 'app.html')
/** Skill bodies, read at registration time. */
export const skillsDir = join(pluginDir, 'skills')
/** The stdio MCP transport adapter. */
export const mcpBridgeScript = join(pluginDir, 'mcp_bridge.py')

/**
 * Plugin version, read from the Codex manifest so the two packagings cannot
 * drift: `.codex-plugin/plugin.json` is the release's single source of truth and
 * `tests/test_plugin_version.py` already pins the panel and MCP handshake to it.
 */
export function pluginVersion() {
  try {
    const manifest = JSON.parse(readFileSync(join(pluginDir, '.codex-plugin', 'plugin.json'), 'utf8'))
    if (typeof manifest.version === 'string' && manifest.version) return manifest.version
  } catch {}
  return '0.0.0'
}

export const PLUGIN_VERSION = pluginVersion()

const isWindows = process.platform === 'win32'

/**
 * Launcher used to start the stdio MCP bridge.
 *
 * `scripts/launch_progress_mcp` is the same launcher the Codex plugin uses: it
 * finds a usable Python 3 and runs the script it is given. `TASK_PROGRESS_PYTHON`
 * pins an interpreter; `TASK_PROGRESS_DSH_LAUNCHER` replaces the launcher itself.
 */
export function launcherCommand() {
  const configured = process.env.TASK_PROGRESS_DSH_LAUNCHER
  if (configured) return configured
  return join(pluginDir, 'scripts', isWindows ? 'launch_progress_mcp.cmd' : 'launch_progress_mcp')
}
