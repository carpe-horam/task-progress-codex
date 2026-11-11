/**
 * Minimal stdio JSON-RPC client for the Task Progress MCP server.
 *
 * The Web GUI panel is not the agent: it talks to the same MCP server the
 * `@deepseek-ai/dsh-mcp-client` row exposes as tools, but it needs the raw
 * protocol (`tools/call`, notifications) rather than tool registration. Keeping
 * this bridge separate means the panel still works when the tool row is
 * disabled, and the tool row still works when nobody opens the panel.
 */
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'

export class StdioMcpBridge {
  /**
   * @param {{ command: string, args?: string[], cwd?: string, version?: string, logger?: { debug?: Function, warn?: Function } }} options
   */
  constructor(options) {
    this.command = options.command
    this.args = options.args ?? []
    this.cwd = options.cwd
    this.version = options.version ?? '0.0.0'
    this.logger = options.logger
    this.proc = null
    this.lines = null
    this.pending = new Map()
    this.nextId = 1
    this.ready = null
    this.disposed = false
  }

  start() {
    if (this.proc || this.disposed) return
    const proc = spawn(this.command, this.args, {
      cwd: this.cwd,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    })
    this.proc = proc
    this.lines = createInterface({ input: proc.stdout })
    this.lines.on('line', line => {
      if (!line.trim()) return
      let message
      try {
        message = JSON.parse(line)
      } catch {
        return
      }
      // Server-initiated notifications have no pending entry and are dropped:
      // the panel polls the server instead of subscribing to pushes.
      if (!message || !Object.prototype.hasOwnProperty.call(message, 'id')) return
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id)
      if (message.error) {
        pending.reject(Object.assign(new Error(message.error.message || 'MCP request failed'), {
          rpc: message.error,
        }))
      } else {
        pending.resolve(message)
      }
    })
    proc.stderr.on('data', chunk => {
      const text = String(chunk).trim()
      if (text) this.logger?.debug?.(`dsh-task-progress: ${text}`)
    })
    proc.once('error', error => this.#fail(error))
    proc.once('exit', code => {
      if (this.proc === proc) this.#fail(new Error(`Task Progress MCP exited (${code ?? 'unknown'})`))
    })
  }

  #fail(error) {
    for (const pending of this.pending.values()) pending.reject(error)
    this.pending.clear()
    this.ready = null
    this.proc = null
    this.lines?.close()
    this.lines = null
  }

  notifyRaw(message) {
    this.start()
    this.proc.stdin.write(`${JSON.stringify(message)}\n`)
  }

  requestRaw(message) {
    this.start()
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      try {
        this.proc.stdin.write(`${JSON.stringify({ ...message, id })}\n`)
      } catch (error) {
        this.pending.delete(id)
        reject(error)
      }
    })
  }

  async ensureReady() {
    if (this.ready) return this.ready
    this.ready = this.requestRaw({
      jsonrpc: '2.0',
      method: 'initialize',
      params: {
        protocolVersion: '2025-11-25',
        capabilities: {},
        clientInfo: { name: 'dsh-task-progress', version: this.version },
      },
    }).then(() => {
      this.notifyRaw({ jsonrpc: '2.0', method: 'notifications/initialized' })
    })
    try {
      await this.ready
    } catch (error) {
      this.ready = null
      throw error
    }
  }

  /** Forward one panel request, preserving the caller's JSON-RPC id. */
  async request(message) {
    await this.ensureReady()
    const callerId = message.id
    const response = await this.requestRaw(message)
    return { ...response, id: callerId }
  }

  dispose() {
    this.disposed = true
    this.lines?.close()
    this.proc?.kill()
    this.proc = null
    this.lines = null
    const error = new Error('Task Progress MCP bridge disposed')
    for (const pending of this.pending.values()) pending.reject(error)
    this.pending.clear()
    this.ready = null
  }
}
