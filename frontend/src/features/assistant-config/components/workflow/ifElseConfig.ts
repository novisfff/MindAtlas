import type {
  ConditionClause,
  ConditionExpression,
  ConditionOperator,
  IfElseBranch,
  IfElseNodeConfig,
} from '../../api/workflow'

const HANDLE_RE = /^[a-zA-Z0-9_]+$/
const LEGACY_OPERATOR_MAP: Record<string, ConditionOperator> = {
  equals: 'is',
  not_equals: 'is_not',
}

export const IF_ELSE_OPERATOR_OPTIONS: Array<{
  value: ConditionOperator
  labelKey: string
  requiresValue: boolean
}> = [
  { value: 'contains', labelKey: 'contains', requiresValue: true },
  { value: 'not_contains', labelKey: 'not_contains', requiresValue: true },
  { value: 'starts_with', labelKey: 'starts_with', requiresValue: true },
  { value: 'ends_with', labelKey: 'ends_with', requiresValue: true },
  { value: 'is', labelKey: 'is', requiresValue: true },
  { value: 'is_not', labelKey: 'is_not', requiresValue: true },
  { value: 'is_empty', labelKey: 'is_empty', requiresValue: false },
  { value: 'is_not_empty', labelKey: 'is_not_empty', requiresValue: false },
]

export function ifElseOperatorRequiresValue(operator: string): boolean {
  return operator !== 'is_empty' && operator !== 'is_not_empty'
}

export function normalizeConditionOperator(raw: unknown): ConditionOperator {
  const op = String(raw ?? 'is').trim().toLowerCase()
  if (!op) return 'is'
  return LEGACY_OPERATOR_MAP[op] ?? (op as ConditionOperator)
}

export function createConditionId(prefix = 'cond'): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export function createBranchId(prefix = 'if'): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export function createDefaultCondition(): ConditionClause {
  return {
    id: createConditionId(),
    variable: '',
    operator: 'is',
    value: '',
  }
}

export function createBranchLabel(index: number): string {
  if (index <= 0) return 'IF'
  return `ELIF ${index}`
}

export function createDefaultIfElseConfig(): IfElseNodeConfig {
  return {
    branches: [
      {
        id: createBranchId('if'),
        label: 'IF',
        logic: 'and',
        conditions: [createDefaultCondition()],
      },
    ],
    elseHandle: 'else',
  }
}

type NormalizedIfElseConfig = {
  branches: IfElseBranch[]
  elseHandle: string
}

function sanitizeHandle(raw: unknown, fallback: string): string {
  const handle = String(raw ?? '').trim()
  if (handle === 'default') return 'else'
  if (HANDLE_RE.test(handle)) return handle
  return fallback
}

function normalizeCondition(raw: unknown, fallbackIdPrefix: string): ConditionClause | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  return {
    id: String(value.id ?? createConditionId(fallbackIdPrefix)),
    variable: String(value.variable ?? '').trim(),
    operator: normalizeConditionOperator(value.operator),
    value: value.value == null ? null : String(value.value),
  }
}

function normalizeBranch(raw: unknown, index: number): IfElseBranch | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const branchId = sanitizeHandle(value.id, `if_${index + 1}`)
  const logic = String(value.logic ?? 'and').trim().toLowerCase() === 'or' ? 'or' : 'and'
  const label = String(value.label ?? createBranchLabel(index)).trim() || createBranchLabel(index)
  const conditionsRaw = Array.isArray(value.conditions) ? value.conditions : []
  const conditions = conditionsRaw
    .map((item, condIndex) => normalizeCondition(item, `${branchId}_${condIndex + 1}`))
    .filter((item): item is ConditionClause => Boolean(item))

  return {
    id: branchId,
    label,
    logic,
    conditions: conditions.length > 0 ? conditions : [createDefaultCondition()],
  }
}

function normalizeFromLegacyConditions(conditions: ConditionExpression[]): IfElseBranch[] {
  const grouped = new Map<string, ConditionClause[]>()
  const order: string[] = []

  conditions.forEach((condition, index) => {
    const handle = sanitizeHandle(condition.handle, '')
    if (!handle || handle === 'else') return
    if (!grouped.has(handle)) {
      grouped.set(handle, [])
      order.push(handle)
    }
    const normalized = normalizeCondition(condition, `${handle}_${index + 1}`)
    if (normalized) grouped.get(handle)!.push(normalized)
  })

  return order.map((handle, index) => ({
    id: handle,
    label: createBranchLabel(index),
    logic: 'and',
    conditions: grouped.get(handle) && grouped.get(handle)!.length > 0
      ? grouped.get(handle)!
      : [createDefaultCondition()],
  }))
}

export function normalizeIfElseConfig(rawConfig: unknown): NormalizedIfElseConfig {
  const cfg = (rawConfig && typeof rawConfig === 'object' ? rawConfig : {}) as Record<string, unknown>
  const elseHandle = sanitizeHandle(cfg.elseHandle, 'else')

  const branchesRaw = Array.isArray(cfg.branches) ? cfg.branches : []
  const branches = branchesRaw
    .map((branch, index) => normalizeBranch(branch, index))
    .filter((branch): branch is IfElseBranch => Boolean(branch))

  if (branches.length > 0) {
    return { branches, elseHandle }
  }

  const legacyConditions = Array.isArray(cfg.conditions)
    ? (cfg.conditions as ConditionExpression[])
    : []
  const legacyBranches = normalizeFromLegacyConditions(legacyConditions)
  if (legacyBranches.length > 0) {
    return { branches: legacyBranches, elseHandle }
  }

  return normalizeIfElseConfig(createDefaultIfElseConfig())
}
