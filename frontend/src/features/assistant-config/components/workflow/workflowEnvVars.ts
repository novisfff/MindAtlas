import type { Node } from '@xyflow/react'
import type { WorkflowEnvVarType, WorkflowSessionVar } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'

const ENV_VAR_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/
const ENV_VAR_TYPES = new Set<WorkflowEnvVarType>([
  'string',
  'number',
  'integer',
  'boolean',
  'object',
  'array',
])

const DEFAULT_VALUE_BY_TYPE: Record<WorkflowEnvVarType, unknown> = {
  string: '',
  number: 0,
  integer: 0,
  boolean: false,
  object: {},
  array: [],
}

function normalizeSingleEnvVar(raw: unknown): WorkflowSessionVar | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const value = raw as Record<string, unknown>
  const name = String(value.name ?? '').trim()
  if (!name) return null
  const rawType = String(value.type ?? 'string').trim().toLowerCase() as WorkflowEnvVarType
  const type = ENV_VAR_TYPES.has(rawType) ? rawType : 'string'
  const hasDefault = Object.prototype.hasOwnProperty.call(value, 'defaultValue') ||
    Object.prototype.hasOwnProperty.call(value, 'default_value')
  const defaultValue = hasDefault
    ? (value.defaultValue ?? value.default_value)
    : DEFAULT_VALUE_BY_TYPE[type]
  const description = String(value.description ?? '').trim()

  return {
    name,
    type,
    defaultValue,
    description: description || undefined,
  }
}

export function normalizeWorkflowEnvVars(raw: unknown): WorkflowSessionVar[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => normalizeSingleEnvVar(item))
    .filter((item): item is WorkflowSessionVar => item !== null)
}

export function getStartNodeFromNodes(nodes: Node<WfNodeData>[]): Node<WfNodeData> | null {
  return nodes.find((node) => node.data.nodeType === 'start') ?? null
}

export function getWorkflowEnvVarsFromStartConfig(config: unknown): WorkflowSessionVar[] {
  if (!config || typeof config !== 'object' || Array.isArray(config)) return []
  const record = config as Record<string, unknown>
  const raw = Array.isArray(record.sessionVars)
    ? record.sessionVars
    : (Array.isArray(record.session_vars) ? record.session_vars : [])
  return normalizeWorkflowEnvVars(raw)
}

export function getWorkflowEnvVarsFromNodes(nodes: Node<WfNodeData>[]): WorkflowSessionVar[] {
  const startNode = getStartNodeFromNodes(nodes)
  if (!startNode) return []
  return getWorkflowEnvVarsFromStartConfig(startNode.data.config)
}

export function toStartConfigWithEnvVars(
  config: Record<string, unknown> | null | undefined,
  envVars: WorkflowSessionVar[],
): Record<string, unknown> {
  const next = {
    ...(config ?? {}),
    sessionVars: envVars.map((item) => ({
      name: item.name,
      type: item.type,
      defaultValue: item.defaultValue,
      description: item.description ?? '',
    })),
  }
  if ('session_vars' in next) {
    delete (next as Record<string, unknown>).session_vars
  }
  return next
}

export function parseEnvVarDefaultValue(text: string, type: WorkflowEnvVarType): unknown {
  const trimmed = text.trim()
  if (!trimmed) return DEFAULT_VALUE_BY_TYPE[type]

  if (type === 'string') return text
  if (type === 'number') {
    const parsed = Number(trimmed)
    if (!Number.isFinite(parsed)) {
      throw new Error('default value must be a valid number')
    }
    return parsed
  }
  if (type === 'integer') {
    const parsed = Number(trimmed)
    if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
      throw new Error('default value must be a valid integer')
    }
    return parsed
  }
  if (type === 'boolean') {
    const lowered = trimmed.toLowerCase()
    if (['true', '1', 'yes', 'on'].includes(lowered)) return true
    if (['false', '0', 'no', 'off'].includes(lowered)) return false
    throw new Error('default value must be true/false')
  }

  try {
    const parsed = JSON.parse(trimmed)
    if (type === 'object') {
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('default value must be a JSON object')
      }
      return parsed
    }
    if (type === 'array') {
      if (!Array.isArray(parsed)) {
        throw new Error('default value must be a JSON array')
      }
      return parsed
    }
    return parsed
  } catch (error) {
    if (error instanceof Error) throw error
    throw new Error('default value is invalid JSON')
  }
}

export function isValidEnvVarName(name: string): boolean {
  return ENV_VAR_NAME_RE.test(name)
}

export function normalizeEnvVarName(name: string): string {
  return String(name ?? '').trim()
}

export function buildDefaultValueText(value: unknown, type: WorkflowEnvVarType): string {
  if (value === null || value === undefined) {
    if (type === 'string') return ''
    if (type === 'boolean') return 'false'
    if (type === 'number' || type === 'integer') return '0'
    if (type === 'object') return '{}'
    if (type === 'array') return '[]'
    return ''
  }
  if (type === 'string') return String(value)
  if (type === 'boolean') return String(Boolean(value))
  if (type === 'number' || type === 'integer') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return ''
  }
}
