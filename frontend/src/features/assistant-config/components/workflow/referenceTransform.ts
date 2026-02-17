import { resolveLabelToId } from './labelUtils'

const TEMPLATE_KEYS = [
  'system_prompt',
  'systemPrompt',
  'user_input',
  'userInput',
  'input_content',
  'inputContent',
  'template',
  'instruction',
  'query',
  'input_source',
  'inputSource',
  'output_selector',
  'outputSelector',
  'args_template',
  'argsTemplate',
] as const

const TEMPLATE_REF_RE = /\{\{\s*([^{}]+?)\s*\}\}/g

function parsePath(path: string): { head: string; field: string } | null {
  const value = path.trim()
  const dotIndex = value.indexOf('.')
  if (dotIndex <= 0 || dotIndex >= value.length - 1) return null
  const head = value.slice(0, dotIndex).trim()
  const field = value.slice(dotIndex + 1).trim()
  if (!head || !field) return null
  return { head, field }
}

function rewriteTemplateRefs(
  text: string,
  resolver: (head: string, field: string) => string | null,
): string {
  return text.replace(TEMPLATE_REF_RE, (full, inner: string) => {
    const parsed = parsePath(inner)
    if (!parsed) return full
    const nextHead = resolver(parsed.head, parsed.field)
    if (!nextHead) return full
    return `{{${nextHead}.${parsed.field}}}`
  })
}

function rewriteConditionPath(
  value: unknown,
  resolver: (head: string, field: string) => string | null,
): unknown {
  if (typeof value !== 'string') return value
  const parsed = parsePath(value)
  if (!parsed) return value
  const nextHead = resolver(parsed.head, parsed.field)
  if (!nextHead) return value
  return `${nextHead}.${parsed.field}`
}

function transformBodyNodes(
  rawNodes: unknown[],
  resolver: (head: string, field: string) => string | null,
): unknown[] {
  const bodyNodes = rawNodes.filter(
    (item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item),
  )
  const localLabelToId = new Map<string, string>()
  const localIds = new Set<string>()
  bodyNodes.forEach((item) => {
    const nodeId = String(item.nodeId ?? item.node_id ?? '').trim()
    if (!nodeId) return
    localIds.add(nodeId)
    const label = String(item.label ?? '').trim()
    if (label) {
      localLabelToId.set(label.toLocaleLowerCase(), nodeId)
    }
  })

  return rawNodes.map((node) => {
    if (!node || typeof node !== 'object' || Array.isArray(node)) return node
    const nodeRecord = { ...(node as Record<string, unknown>) }
    nodeRecord.config = transformConfig(nodeRecord.config, (head, field) => {
      if (head === 'sys' || head === 'container') return head
      if (localIds.has(head)) return head
      const local = localLabelToId.get(head.trim().toLocaleLowerCase())
      if (local) return local
      return resolver(head, field)
    })
    return nodeRecord
  })
}

function transformConfig(
  config: unknown,
  resolver: (head: string, field: string) => string | null,
): unknown {
  if (!config || typeof config !== 'object' || Array.isArray(config)) {
    return config
  }

  const next = { ...(config as Record<string, unknown>) }

  TEMPLATE_KEYS.forEach((key) => {
    const value = next[key]
    if (typeof value === 'string') {
      next[key] = rewriteTemplateRefs(value, resolver)
    }
  })

  const rawBindings = next.inputBindings ?? next.input_bindings
  if (rawBindings && typeof rawBindings === 'object' && !Array.isArray(rawBindings)) {
    const bindings = rawBindings as Record<string, unknown>
    const mapped: Record<string, unknown> = {}
    Object.entries(bindings).forEach(([key, value]) => {
      mapped[key] = typeof value === 'string' ? rewriteTemplateRefs(value, resolver) : value
    })
    if ('inputBindings' in next) {
      next.inputBindings = mapped
    } else {
      next.input_bindings = mapped
    }
  }

  const rewriteBranchCondition = (condition: unknown): unknown => {
    if (!condition || typeof condition !== 'object' || Array.isArray(condition)) return condition
    const conditionRecord = { ...(condition as Record<string, unknown>) }
    conditionRecord.variable = rewriteConditionPath(conditionRecord.variable, resolver)
    if (typeof conditionRecord.value === 'string') {
      conditionRecord.value = rewriteTemplateRefs(conditionRecord.value, resolver)
    }
    return conditionRecord
  }

  if (Array.isArray(next.branches)) {
    next.branches = next.branches.map((branch) => {
      if (!branch || typeof branch !== 'object' || Array.isArray(branch)) return branch
      const branchRecord = { ...(branch as Record<string, unknown>) }
      if (Array.isArray(branchRecord.conditions)) {
        branchRecord.conditions = branchRecord.conditions.map(rewriteBranchCondition)
      }
      return branchRecord
    })
  }

  if (Array.isArray(next.conditions)) {
    next.conditions = next.conditions.map(rewriteBranchCondition)
  }

  if (Array.isArray(next.terminationConditions)) {
    next.terminationConditions = next.terminationConditions.map(rewriteBranchCondition)
  }

  if (Array.isArray(next.initialVars)) {
    next.initialVars = next.initialVars.map((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return item
      const record = { ...(item as Record<string, unknown>) }
      if (typeof record.value === 'string') {
        record.value = rewriteTemplateRefs(record.value, resolver)
      }
      return record
    })
  }

  if (Array.isArray(next.updateMappings)) {
    next.updateMappings = next.updateMappings.map((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return item
      const record = { ...(item as Record<string, unknown>) }
      if (typeof record.value === 'string') {
        record.value = rewriteTemplateRefs(record.value, resolver)
      }
      return record
    })
  }

  if (Array.isArray(next.bodyNodes)) {
    next.bodyNodes = transformBodyNodes(next.bodyNodes, resolver)
  }

  if (Array.isArray(next.body_nodes)) {
    next.body_nodes = transformBodyNodes(next.body_nodes, resolver)
  }

  return next
}

export function toDisplayReferencesFromStored(
  config: unknown,
  idToLabel: Map<string, string>,
): unknown {
  return transformConfig(config, (head) => {
    if (head === 'sys' || head === 'container') return head
    return idToLabel.get(head) ?? null
  })
}

export function toStoredReferencesFromDisplay(
  config: unknown,
  labelToId: Map<string, string>,
  nodeIds: Set<string>,
): unknown {
  return transformConfig(config, (head) => {
    if (head === 'sys' || head === 'container') return head
    return resolveLabelToId(head, labelToId, nodeIds)
  })
}

export function renameDisplayLabelInReferences(
  config: unknown,
  previousLabel: string,
  nextLabel: string,
): unknown {
  const prevKey = previousLabel.trim().toLocaleLowerCase()
  if (!prevKey || previousLabel === nextLabel) return config
  return transformConfig(config, (head) => {
    if (head.trim().toLocaleLowerCase() === prevKey) return nextLabel
    return head
  })
}
