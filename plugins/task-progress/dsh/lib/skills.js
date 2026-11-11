/**
 * Skill registration for the DeepSeekRuntime bundle.
 *
 * Codex learns these skills from `.codex-plugin/plugin.json` (`"skills": "./skills/"`).
 * DeepSeekRuntime has no equivalent manifest field, so the bundle registers the
 * same bodies at mount time through `ctx.skills.register`. The Markdown files stay
 * the single source of truth; nothing is duplicated into this package.
 */
import { readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'

import { skillsDir } from './paths.js'

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/

/** Split a `key: value` frontmatter block without pulling in a YAML parser. */
function parseFrontmatter(text) {
  const match = FRONTMATTER.exec(text)
  if (!match) return { attributes: {}, body: text }
  const attributes = {}
  let key = null
  for (const rawLine of match[1].split(/\r?\n/)) {
    if (!rawLine.trim()) continue
    const entry = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(rawLine)
    if (entry) {
      key = entry[1]
      attributes[key] = entry[2].trim()
      continue
    }
    // Continuation lines of a folded scalar (`description: >` or wrapped text).
    if (key && /^\s+/.test(rawLine)) attributes[key] = `${attributes[key]} ${rawLine.trim()}`.trim()
  }
  for (const [name, value] of Object.entries(attributes)) {
    attributes[name] = value.replace(/^['"]|['"]$/g, '')
  }
  return { attributes, body: text.slice(match[0].length) }
}

async function readSkill(directory) {
  const path = join(skillsDir, directory, 'SKILL.md')
  const text = await readFile(path, 'utf8')
  const { attributes, body } = parseFrontmatter(text)
  if (!attributes.name) return null
  return {
    name: attributes.name,
    description: attributes.description ?? '',
    content: body.trimStart(),
    // References such as `references/htn-planning.md` resolve against this base.
    resourceBase: { kind: 'directory', path: join(skillsDir, directory) },
  }
}

/**
 * Register every skill directory shipped next to the plugin.
 *
 * @returns {Promise<{ registered: string[], skipped: string[] }>}
 */
export async function registerSkills(ctx, logger) {
  const skills = ctx.get?.('skills')
  if (!skills || typeof skills.register !== 'function') {
    logger?.warn?.('dsh-task-progress: skills service is unavailable; track-progress stays unregistered')
    return { registered: [], skipped: ['skills-service-unavailable'] }
  }

  let directories
  try {
    directories = await readdir(skillsDir, { withFileTypes: true })
  } catch (error) {
    logger?.warn?.(`dsh-task-progress: cannot read ${skillsDir}: ${String(error)}`)
    return { registered: [], skipped: ['skills-directory-unreadable'] }
  }

  const registered = []
  const skipped = []
  for (const entry of directories) {
    if (!entry.isDirectory()) continue
    let skill
    try {
      skill = await readSkill(entry.name)
    } catch (error) {
      logger?.warn?.(`dsh-task-progress: cannot read skill ${entry.name}: ${String(error)}`)
      skipped.push(entry.name)
      continue
    }
    if (!skill) {
      skipped.push(entry.name)
      continue
    }
    try {
      ctx.effect(() => skills.register({
        ...skill,
        source: 'custom',
        invocation: { modelInvocable: true, userInvocable: true },
      }))
      registered.push(skill.name)
    } catch (error) {
      logger?.warn?.(`dsh-task-progress: cannot register skill ${skill.name}: ${String(error)}`)
      skipped.push(skill.name)
    }
  }
  return { registered, skipped }
}
