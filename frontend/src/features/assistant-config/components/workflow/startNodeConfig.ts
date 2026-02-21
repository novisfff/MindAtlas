import type { StartStructuredField, WorkflowInput } from '../../api/workflow'

export type StartInputMode = 'text' | 'structured'

const FIELD_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/
const ALLOWED_FIELD_TYPES = new Set(['string', 'number', 'integer', 'boolean'])

export const START_FIELD_TYPE_OPTIONS: Array<{ label: string; value: StartStructuredField['type'] }> = [
  { label: 'String', value: 'string' },
  { label: 'Number', value: 'number' },
  { label: 'Integer', value: 'integer' },
  { label: 'Boolean', value: 'boolean' },
]

export interface NormalizedStartNodeConfig {
  inputMode: StartInputMode
  structuredFields: StartStructuredField[]
}

export function buildDefaultStartNodeConfig(): NormalizedStartNodeConfig {
  return {
    inputMode: 'text',
    structuredFields: [],
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

export function isValidStartStructuredFieldName(name: string): boolean {
  return FIELD_NAME_RE.test(name) && name !== 'user_input'
}

export function normalizeStartNodeConfig(rawConfig: unknown): NormalizedStartNodeConfig {
  if (!rawConfig || typeof rawConfig !== 'object' || Array.isArray(rawConfig)) {
    return buildDefaultStartNodeConfig()
  }
  const cfg = rawConfig as Record<string, unknown>
  const rawMode = String(cfg.inputMode ?? cfg.input_mode ?? 'text').trim().toLowerCase()
  const inputMode: StartInputMode = rawMode === 'structured' ? 'structured' : 'text'
  const rawFields = (cfg.structuredFields ?? cfg.structured_fields) as unknown
  const structuredFields = Array.isArray(rawFields)
    ? rawFields
      .map((item) => normalizeStructuredField(item))
      .filter((item): item is StartStructuredField => Boolean(item))
    : []
  return {
    inputMode,
    structuredFields,
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
