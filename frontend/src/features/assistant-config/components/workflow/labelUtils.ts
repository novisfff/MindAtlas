import type { Node } from '@xyflow/react'
import type { NodeType } from '../../api/workflow'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import i18n from '@/lib/i18n'

const DEFAULT_LABEL_BY_TYPE: Record<NodeType, string> = {
  start: 'Start',
  llm: 'LLM',
  tool: 'Tool',
  if_else: 'If Else',
  parameter_extractor: 'Parameter Extractor',
  knowledge_retrieval: 'Knowledge Retrieval',
  iteration: 'Iteration',
  loop: 'Loop',
  output: 'Output',
}

const NODE_TYPE_LABEL_KEY: Record<NodeType, string> = {
  start: 'settings.skills.nodeTypes.start',
  llm: 'settings.skills.nodeTypes.llm',
  tool: 'settings.skills.nodeTypes.tool',
  if_else: 'settings.skills.nodeTypes.if_else',
  parameter_extractor: 'settings.skills.nodeTypes.parameter_extractor',
  knowledge_retrieval: 'settings.skills.nodeTypes.knowledge_retrieval',
  iteration: 'settings.skills.nodeTypes.iteration',
  loop: 'settings.skills.nodeTypes.loop',
  output: 'settings.skills.nodeTypes.output',
}

function normalizeCasefold(value: string): string {
  return value.trim().toLocaleLowerCase()
}

export function defaultLabelForNodeType(nodeType: NodeType): string {
  const fallback = DEFAULT_LABEL_BY_TYPE[nodeType] ?? 'Node'
  const key = NODE_TYPE_LABEL_KEY[nodeType]
  if (!key) return fallback
  return i18n.t(key, { defaultValue: fallback })
}

export function normalizeLabel(raw: unknown): string {
  const text = String(raw ?? '')
  return text.replace(/\./g, ' ').replace(/\s+/g, ' ').trim()
}

export function makeUniqueLabel(baseLabel: string, existingLabels: Iterable<string>): string {
  const normalizedBase = normalizeLabel(baseLabel) || 'Node'
  const seen = new Set<string>()
  for (const label of existingLabels) {
    const normalized = normalizeLabel(label)
    if (!normalized) continue
    seen.add(normalizeCasefold(normalized))
  }

  if (!seen.has(normalizeCasefold(normalizedBase))) {
    return normalizedBase
  }

  let index = 2
  while (seen.has(normalizeCasefold(`${normalizedBase}#${index}`))) {
    index += 1
  }
  return `${normalizedBase}#${index}`
}

export function ensureWorkflowUniqueLabels(nodes: Node<WfNodeData>[]): Node<WfNodeData>[] {
  const existing: string[] = []
  return nodes.map((node) => {
    const fallback = defaultLabelForNodeType(node.data.nodeType)
    const base = normalizeLabel(node.data.label) || fallback
    const label = makeUniqueLabel(base, existing)
    existing.push(label)
    return {
      ...node,
      data: {
        ...node.data,
        label,
      },
    }
  })
}

export function buildLabelMaps(nodes: Node<WfNodeData>[]): {
  idToLabel: Map<string, string>
  labelToId: Map<string, string>
  nodeIds: Set<string>
} {
  const idToLabel = new Map<string, string>()
  const labelToId = new Map<string, string>()
  const nodeIds = new Set<string>()

  nodes.forEach((node) => {
    const fallback = defaultLabelForNodeType(node.data.nodeType)
    const label = normalizeLabel(node.data.label) || fallback
    idToLabel.set(node.id, label)
    nodeIds.add(node.id)
    const key = normalizeCasefold(label)
    if (!labelToId.has(key)) {
      labelToId.set(key, node.id)
    }
  })

  return { idToLabel, labelToId, nodeIds }
}

export function resolveLabelToId(
  labelOrId: string,
  labelToId: Map<string, string>,
  nodeIds: Set<string>,
): string | null {
  const raw = labelOrId.trim()
  if (!raw) return null
  if (nodeIds.has(raw)) return raw
  return labelToId.get(normalizeCasefold(raw)) ?? null
}
