/**
 * Tool registration for the DeepSeekRuntime bundle.
 *
 * The Codex plugin gets its tools from `.mcp.json`; DeepSeekRuntime would get them
 * from the shipped `@deepseek-ai/dsh-mcp-client` row. This bundle registers them
 * itself instead, because an MCP row cannot know where this checkout lives: its
 * config is static YAML, and an environment value published by a sibling row makes
 * the MCP row depend on mount order. One row, one failure mode.
 *
 * Tool names are kept exactly as the MCP server declares them (`render_progress`,
 * `get_progress`, …) so the skill bodies and copyable examples stay valid in both
 * hosts.
 */
const NUMERIC_KEYS = ['minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum']
const LENGTH_KEYS = ['minLength', 'maxLength', 'minItems', 'maxItems', 'uniqueItems', 'pattern']

/**
 * Convert one JSON Schema property into the compact parameter dialect DSH tools
 * use (a property map where `required: true` replaces JSON Schema's `required[]`).
 */
function convertProperty(schema, isRequired) {
  if (!schema || typeof schema !== 'object') return isRequired ? { required: true } : {}
  const out = {}
  if (isRequired) out.required = true

  // `anyOf` can only be expressed loosely here; keep the branch type when the
  // branches agree, and rely on the server's own validation otherwise.
  const branches = Array.isArray(schema.anyOf) ? schema.anyOf : []
  const declaredType = typeof schema.type === 'string'
    ? schema.type
    : branches.map(branch => branch?.type).find(type => typeof type === 'string')
  if (declaredType) out.type = declaredType

  if (schema.description) out.description = schema.description
  if (Array.isArray(schema.enum)) out.enum = schema.enum
  if (schema.default !== undefined) out.default = schema.default
  for (const key of NUMERIC_KEYS) if (typeof schema[key] === 'number') out[key] = schema[key]
  for (const key of LENGTH_KEYS) if (schema[key] !== undefined) out[key] = schema[key]

  // One-level branch merge: a union of objects contributes each branch's properties
  // so the model still sees every documented field.
  const properties = schema.properties ?? branches.find(branch => branch?.properties)?.properties
  if (properties) out.properties = convertObject(properties, schema.required ?? [])
  if (schema.items && typeof schema.items === 'object') {
    out.items = convertProperty(schema.items, false)
  }
  if (schema.additionalProperties !== undefined) out.additionalProperties = schema.additionalProperties
  return out
}

function convertObject(properties, required = []) {
  const requiredNames = new Set(Array.isArray(required) ? required : [])
  const converted = {}
  for (const [name, schema] of Object.entries(properties)) {
    converted[name] = convertProperty(schema, requiredNames.has(name))
  }
  return converted
}

/** The MCP `inputSchema` uses the same property-map shape one level up. */
export function toParameters(inputSchema) {
  if (!inputSchema || typeof inputSchema !== 'object') return {}
  return convertObject(inputSchema.properties ?? {}, inputSchema.required ?? [])
}

/**
 * Flatten an MCP tool result into the text the model reads.
 *
 * Both halves of an MCP result are model input. The server writes deliberate
 * terse receipts (`已记录。`) into `content` and puts the identities — session_id,
 * task_instance_id, epoch, revision — into `structuredContent`; without that
 * second half the agent cannot make its next call, because Task Progress keys
 * everything by those ids. Codex passes both. Forwarding only `content` here
 * produced a dead end in the first live run.
 */
export function resultText(result) {
  const blocks = Array.isArray(result?.content) ? result.content : []
  const texts = blocks
    .filter(block => block && block.type === 'text' && typeof block.text === 'string')
    .map(block => block.text)
  const structured = result?.structuredContent
  const hasStructured = structured !== undefined && structured !== null
  const json = hasStructured ? JSON.stringify(structured) : ''
  if (texts.length) return hasStructured ? `${texts.join('\n\n')}\n\n${json}` : texts.join('\n\n')
  if (hasStructured) return json
  return JSON.stringify(result ?? null)
}

/**
 * Register every tool the MCP server advertises.
 *
 * @param {object} ctx cordis context
 * @param {{ call: (name: string, args: unknown) => Promise<any>, listTools: () => Promise<any[]> }} bridge
 * @returns {Promise<{ registered: string[] }>}
 */
export async function registerTools(ctx, bridge, options = {}) {
  const tools = ctx.get?.('tools')
  if (!tools || typeof tools.register !== 'function') {
    ctx.logger?.warn?.('dsh-task-progress: tools service is unavailable; Task Progress tools stay unregistered')
    return { registered: [] }
  }

  let advertised
  try {
    advertised = await bridge.listTools()
  } catch (error) {
    ctx.logger?.warn?.(`dsh-task-progress: cannot list Task Progress tools: ${String(error)}`)
    return { registered: [], error: `listTools: ${String(error)}` }
  }

  const registered = []
  const failures = []
  for (const tool of advertised) {
    if (!tool?.name) continue
    try {
      // A plain definition object: `@deepseek-ai/dsh-tools` is not resolvable from
      // a profile-linked bundle in this DSH version (ERR_MODULE_NOT_FOUND), and
      // `defineTool` is only a typing helper over this exact shape.
      ctx.effect(() => tools.register({
        name: tool.name,
        description: tool.description ?? '',
        parameters: toParameters(tool.inputSchema),
        // DSH requires an output contract for every tool. The MCP server already
        // renders its own human-readable text, so the tool returns that string and
        // the renderer forwards it unchanged instead of re-summarising it.
        output: {
          schema: { type: 'string' },
          render: (args, value) => [{ type: 'text', text: String(value ?? '') }],
        },
        // `exec` is the ToolRunContext: its `agent.id` is the host SessionId, which
        // is the only reliable way to bind a progress session to one conversation.
        async execute(args, exec) {
          const result = await bridge.call(tool.name, args ?? {})
          if (result?.isError) throw new Error(resultText(result))
          try {
            options.onCall?.(tool.name, args ?? {}, result, exec)
          } catch {}
          return resultText(result)
        },
      }))
      registered.push(tool.name)
    } catch (error) {
      ctx.logger?.warn?.(`dsh-task-progress: cannot register tool ${tool.name}: ${String(error)}`)
      failures.push({ name: tool.name, error: String(error) })
    }
  }
  return { registered, failures }
}
