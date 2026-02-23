import type { Edge, Node } from '@xyflow/react'
import type { InputParam } from '../../api/tools'
import type { WfNodeData } from '../../stores/workflow-editor-store'
import type { WorkflowToolDefinition } from './types'
import { isValidStartStructuredFieldName, normalizeStartNodeConfig } from './startNodeConfig'

const SYS_REFERENCE_PARAMS: InputParam[] = [
  {
    name: 'sys.date',
    referencePath: 'sys.date',
    itemLabel: 'date',
    groupKey: 'sys',
    groupLabel: 'System Variables',
    paramType: 'string',
    required: false,
    description: 'System date (YYYY-MM-DD)',
  },
  {
    name: 'sys.datetime',
    referencePath: 'sys.datetime',
    itemLabel: 'datetime',
    groupKey: 'sys',
    groupLabel: 'System Variables',
    paramType: 'string',
    required: false,
    description: 'System datetime (ISO8601)',
  },
  {
    name: 'sys.conversation_id',
    referencePath: 'sys.conversation_id',
    itemLabel: 'conversation_id',
    groupKey: 'sys',
    groupLabel: 'System Variables',
    paramType: 'string',
    required: false,
    description: 'Current conversation id',
  },
]

function normalizeOutputMode(raw: unknown): 'text' | 'structured' {
  const mode = String(raw ?? 'text').trim().toLowerCase()
  if (mode === 'structured' || mode === 'json') return 'structured'
  return 'text'
}

function collectUpstreamNodeIds(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  currentNodeId: string,
): string[] {
  const nodeSet = new Set(nodes.map((n) => n.id))
  const reverseAdj = new Map<string, Set<string>>()

  edges.forEach((edge) => {
    if (!nodeSet.has(edge.source) || !nodeSet.has(edge.target)) return
    if (!reverseAdj.has(edge.target)) reverseAdj.set(edge.target, new Set<string>())
    reverseAdj.get(edge.target)!.add(edge.source)
  })

  const visited = new Set<string>()
  const queue: string[] = [currentNodeId]

  while (queue.length > 0) {
    const nodeId = queue.shift()!
    const sources = reverseAdj.get(nodeId)
    if (!sources) continue
    sources.forEach((sourceId) => {
      if (visited.has(sourceId)) return
      visited.add(sourceId)
      queue.push(sourceId)
    })
  }

  const upstream = nodes
    .map((n) => n.id)
    .filter((id) => visited.has(id) && id !== currentNodeId)

  return upstream
}

function buildNodeOutputFields(
  node: Node<WfNodeData>,
  toolByName: Map<string, WorkflowToolDefinition>,
): string[] {
  const cfg = (node.data.config ?? {}) as Record<string, unknown>
  if (node.data.nodeType === 'start') {
    const normalized = normalizeStartNodeConfig(cfg)
    if (normalized.inputMode === 'structured') {
      return normalized.structuredFields
        .map((field) => field.name.trim())
        .filter((name) => isValidStartStructuredFieldName(name))
    }
    return ['user_input']
  }

  if (node.data.nodeType === 'llm') {
    const mode = normalizeOutputMode(cfg.outputMode)
    if (mode === 'text') return ['response']

    const fields = Array.isArray(cfg.outputFields)
      ? cfg.outputFields
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '') : ''))
        .filter((name) => !!name)
      : []
    return ['response', ...fields]
  }

  if (node.data.nodeType === 'tool') {
    const toolName = String(cfg.toolName ?? '').trim()
    const tool = toolByName.get(toolName)
    const outputNames = tool?.outputParams?.map((item) => item.name).filter(Boolean) ?? []
    return ['result', ...outputNames]
  }

  if (node.data.nodeType === 'parameter_extractor') {
    const fields = Array.isArray(cfg.outputFields)
      ? cfg.outputFields
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '') : ''))
        .filter((name) => !!name)
      : []
    return fields
  }

  if (node.data.nodeType === 'knowledge_retrieval') {
    return ['result', 'query', 'mode', 'references', 'references_count']
  }

  if (node.data.nodeType === 'code_executor') {
    const fields = Array.isArray(cfg.outputFields)
      ? cfg.outputFields
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '').trim() : ''))
        .filter((name) => !!name)
      : []
    return fields
  }

  if (node.data.nodeType === 'variable_assign') {
    return []
  }

  if (node.data.nodeType === 'iteration') {
    const outputVariable = String(cfg.outputVariable ?? '').trim() || 'results'
    return [outputVariable, 'count', 'errors']
  }

  if (node.data.nodeType === 'loop') {
    const varNames = Array.isArray(cfg.initialVars)
      ? cfg.initialVars
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '') : ''))
        .filter(Boolean)
      : []
    return ['iterations', 'terminated', 'last_item', ...varNames]
  }

  if (node.data.nodeType === 'output') {
    const mode = normalizeOutputMode(cfg.outputMode)
    if (mode === 'text') return ['response']
    const fields = Array.isArray(cfg.outputFields)
      ? cfg.outputFields
        .map((item) => (item && typeof item === 'object' ? String((item as Record<string, unknown>).name ?? '') : ''))
        .filter((name) => !!name)
      : []
    return ['response', ...fields]
  }

  if (node.data.nodeType === 'if_else') return ['handle']
  return ['text']
}

function inferFieldType(field: string): InputParam['paramType'] {
  if (field === 'result' || field === 'references') return 'object'
  if (field === 'before' || field === 'after') return 'object'
  if (field === 'references_count') return 'number'
  if (field === 'count' || field === 'iterations') return 'number'
  if (field === 'errors') return 'array'
  return 'string'
}

function inferCodeExecutorFieldType(
  node: Node<WfNodeData>,
  field: string,
): InputParam['paramType'] {
  const cfg = (node.data.config ?? {}) as Record<string, unknown>
  const fields = Array.isArray(cfg.outputFields) ? cfg.outputFields : []
  const matched = fields.find((item) => {
    if (!item || typeof item !== 'object') return false
    return String((item as Record<string, unknown>).name ?? '').trim() === field
  }) as Record<string, unknown> | undefined
  const rawType = String(matched?.type ?? 'string').trim().toLowerCase()
  if (rawType === 'number' || rawType === 'integer') return 'number'
  if (rawType === 'boolean') return 'boolean'
  if (rawType === 'object') return 'object'
  if (rawType === 'array') return 'array'
  return 'string'
}

function inferEnvFieldType(rawType: unknown): InputParam['paramType'] {
  const envType = String(rawType ?? 'string').trim().toLowerCase()
  if (envType === 'number' || envType === 'integer') return 'number'
  if (envType === 'boolean') return 'boolean'
  if (envType === 'object') return 'object'
  if (envType === 'array') return 'array'
  return 'string'
}

function buildEnvironmentReferenceParams(nodes: Node<WfNodeData>[]): InputParam[] {
  const startNode = nodes.find((item) => item.data.nodeType === 'start')
  if (!startNode) return []
  const startConfig = (startNode.data.config ?? {}) as Record<string, unknown>
  const rawSessionVars = Array.isArray(startConfig.sessionVars)
    ? startConfig.sessionVars
    : (Array.isArray(startConfig.session_vars) ? startConfig.session_vars : [])

  return rawSessionVars
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => {
      const name = String(item.name ?? '').trim()
      if (!name) return null
      const path = `env.${name}`
      return {
        name: path,
        referencePath: path,
        groupKey: 'env',
        groupLabel: 'Environment Variables',
        itemLabel: name,
        paramType: inferEnvFieldType(item.type),
        required: false,
        description: String(item.description ?? `Environment variable: ${name}`),
      } as InputParam
    })
    .filter((item): item is InputParam => item !== null)
}

function nodeDisplayLabel(node: Node<WfNodeData>): string {
  const label = String(node.data.label ?? '').trim()
  return label || node.id
}

export function buildWorkflowReferenceParams(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  currentNodeId: string,
  tools: WorkflowToolDefinition[],
): InputParam[] {
  const upstreamIds = collectUpstreamNodeIds(nodes, edges, currentNodeId)
  const upstreamSet = new Set(upstreamIds)
  const toolByName = new Map(tools.map((tool) => [tool.name, tool]))
  const params: InputParam[] = []
  const seen = new Set<string>()

  nodes.forEach((node) => {
    if (!upstreamSet.has(node.id) && node.data.nodeType !== 'start') return
    const fields = buildNodeOutputFields(node, toolByName)
    const groupLabel = nodeDisplayLabel(node)
    const groupKey = `node:${node.id}`
    fields.forEach((field) => {
      const path = `${groupLabel}.${field}`
      if (seen.has(path)) return
      seen.add(path)
      params.push({
        name: path,
        referencePath: path,
        groupKey,
        groupLabel,
        itemLabel: field,
        paramType: node.data.nodeType === 'code_executor'
          ? inferCodeExecutorFieldType(node, field)
          : inferFieldType(field),
        required: false,
        description: `${groupLabel}.${field}`,
      })
    })
  })

  SYS_REFERENCE_PARAMS.forEach((param) => {
    const path = param.referencePath || param.name
    if (seen.has(path)) return
    seen.add(path)
    params.push(param)
  })

  buildEnvironmentReferenceParams(nodes).forEach((param) => {
    const path = param.referencePath || param.name
    if (seen.has(path)) return
    seen.add(path)
    params.push(param)
  })

  return params
}
