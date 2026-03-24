import type { StartStructuredField, WorkflowInput, WorkflowSessionVar, WorkflowEnvVarType } from '../../api/workflow'

export type StartInputMode = 'text' | 'structured'
export type StartMemoryMode = 'auto' | 'off' | 'structured'

const FIELD_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/
const ALLOWED_FIELD_TYPES = new Set(['string', 'number', 'integer', 'boolean'])
const ALLOWED_MEMORY_MODES = new Set<StartMemoryMode>(['auto', 'off', 'structured'])
export const START_MEMORY_STRUCTURED_FIELD_NAMES = new Set([
  'memory_recent_dialogue',
  'memory_conversation_summary',
  'memory_skill_facts',
])
const LEGACY_START_MEMORY_FIELD_NAMES = new Set([
  'memory_l0',
  'memory_l1',
  'memory_l2',
])
const ALLOWED_ENV_TYPES = new Set<WorkflowEnvVarType>([
  'string',
  'number',
  'integer',
  'boolean',
  'object',
  'array',
])

export const START_FIELD_TYPE_OPTIONS: Array<{ label: string; value: StartStructuredField['type'] }> = [
  { label: 'String', value: 'string' },
  { label: 'Number', value: 'number' },
  { label: 'Integer', value: 'integer' },
  { label: 'Boolean', value: 'boolean' },
]

export interface NormalizedStartNodeConfig {
  inputMode: StartInputMode
  memoryMode: StartMemoryMode
  structuredFields: StartStructuredField[]
  sessionVars: WorkflowSessionVar[]
}

export function buildDefaultStartNodeConfig(): NormalizedStartNodeConfig {
  return {
    inputMode: 'text',
    memoryMode: 'auto',
    structuredFields: [],
    sessionVars: [],
  }
}

function normalizeStructuredField(raw: unknown): StartStructuredField | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const value = raw as Record<string, unknown>
  const name = String(value.name ?? '').trim()
  const rawType = String(value.type ?? 'string').trim().toLowerCase()
  const fieldType = ALLOWED_FIELD_TYPES.has(rawType) ? rawType : 'string'
  const required = Boolean(value.required)
  const description = String(value.description ?? '').trim()
  return {
    name,
    type: fieldType as StartStructuredField['type'],
    required,
    description: description || undefined,
  }
}

function normalizeSessionVar(raw: unknown): WorkflowSessionVar | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const value = raw as Record<string, unknown>
  const name = String(value.name ?? '').trim()
  if (!name) return null
  const rawType = String(value.type ?? 'string').trim().toLowerCase()
  const type = ALLOWED_ENV_TYPES.has(rawType as WorkflowEnvVarType)
    ? (rawType as WorkflowEnvVarType)
    : 'string'
  const description = String(value.description ?? '').trim()
  const hasDefault = Object.prototype.hasOwnProperty.call(value, 'defaultValue') ||
    Object.prototype.hasOwnProperty.call(value, 'default_value')
  const defaultValue = hasDefault
    ? (value.defaultValue ?? value.default_value)
    : undefined
  return {
    name,
    type,
    defaultValue,
    description: description || undefined,
  }
}

export function isValidStartStructuredFieldName(name: string): boolean {
  return (
    FIELD_NAME_RE.test(name)
    && name !== 'user_input'
    && !START_MEMORY_STRUCTURED_FIELD_NAMES.has(name)
    && !LEGACY_START_MEMORY_FIELD_NAMES.has(name)
  )
}

export function normalizeStartNodeConfig(rawConfig: unknown): NormalizedStartNodeConfig {
  if (!rawConfig || typeof rawConfig !== 'object' || Array.isArray(rawConfig)) {
    return buildDefaultStartNodeConfig()
  }
  const cfg = rawConfig as Record<string, unknown>
  const rawMode = String(cfg.inputMode ?? cfg.input_mode ?? 'text').trim().toLowerCase()
  const inputMode: StartInputMode = rawMode === 'structured' ? 'structured' : 'text'
  const rawMemoryMode = String(cfg.memoryMode ?? cfg.memory_mode ?? 'auto').trim().toLowerCase()
  const memoryMode: StartMemoryMode = ALLOWED_MEMORY_MODES.has(rawMemoryMode as StartMemoryMode)
    ? (rawMemoryMode as StartMemoryMode)
    : 'auto'
  const rawFields = (cfg.structuredFields ?? cfg.structured_fields) as unknown
  const structuredFields = Array.isArray(rawFields)
    ? rawFields
      .map((item) => normalizeStructuredField(item))
      .filter((item): item is StartStructuredField => Boolean(item))
    : []
  const rawSessionVars = (cfg.sessionVars ?? cfg.session_vars) as unknown
  const sessionVars = Array.isArray(rawSessionVars)
    ? rawSessionVars
      .map((item) => normalizeSessionVar(item))
      .filter((item): item is WorkflowSessionVar => Boolean(item))
    : []
  return {
    inputMode,
    memoryMode,
    structuredFields,
    sessionVars,
  }
}

export function getStartInputModeFromWorkflowInput(workflow: WorkflowInput): StartInputMode {
  const startNode = workflow.nodes.find((node) => node.nodeType === 'start')
  if (!startNode) return 'text'
  return normalizeStartNodeConfig(startNode.config).inputMode
}

type WorkflowNodeLike = {
  nodeType?: string
  config?: unknown
}

export function getStartInputModeFromWorkflowNodes(nodes: WorkflowNodeLike[] = []): StartInputMode {
  const startNode = nodes.find((node) => node?.nodeType === 'start')
  if (!startNode) return 'text'
  return normalizeStartNodeConfig(startNode.config).inputMode
}

export function isStructuredStartWorkflowFromNodes(nodes: WorkflowNodeLike[] = []): boolean {
  return getStartInputModeFromWorkflowNodes(nodes) === 'structured'
}
