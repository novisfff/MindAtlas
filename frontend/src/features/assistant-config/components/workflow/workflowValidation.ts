import type { WorkflowInput, WorkflowValidationError } from '../../api/workflow'

export type WorkflowValidationIssueSeverity = 'error' | 'warning'

export interface WorkflowValidationIssue {
  id: string
  severity: WorkflowValidationIssueSeverity
  nodeId: string | null
  subflowNodeId?: string | null
  message: string
  source: 'backend' | 'reachability'
}

type JsonLike = null | boolean | number | string | JsonLike[] | { [key: string]: JsonLike }

function toStableJsonLike(value: unknown): JsonLike {
  if (value === null || value === undefined) return null
  if (
    typeof value === 'string'
    || typeof value === 'number'
    || typeof value === 'boolean'
  ) {
    return value
  }
  if (Array.isArray(value)) {
    return value.map((item) => toStableJsonLike(item))
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
    const obj: Record<string, JsonLike> = {}
    entries.forEach(([key, item]) => {
      obj[key] = toStableJsonLike(item)
    })
    return obj
  }
  return String(value)
}

function stableStringify(value: unknown): string {
  return JSON.stringify(toStableJsonLike(value))
}

function buildIssueId(issue: Omit<WorkflowValidationIssue, 'id'>): string {
  return [
    issue.severity,
    issue.source,
    issue.nodeId ?? 'global',
    issue.subflowNodeId ?? 'none',
    issue.message,
  ].join('::')
}

export function buildValidationSignature(workflow: WorkflowInput): string {
  const nodesSig = [...workflow.nodes]
    .map((node) => ({
      nodeId: node.nodeId,
      nodeType: node.nodeType,
      label: node.label ?? '',
      config: toStableJsonLike(node.config ?? null),
    }))
    .sort((a, b) => a.nodeId.localeCompare(b.nodeId))

  const edgesSig = [...workflow.edges]
    .map((edge) => ({
      sourceNodeId: edge.sourceNodeId,
      targetNodeId: edge.targetNodeId,
      sourceHandle: edge.sourceHandle ?? 'output',
      targetHandle: edge.targetHandle ?? 'input',
      conditionType: edge.conditionType ?? null,
      conditionExpr: toStableJsonLike(edge.conditionExpr ?? null),
      label: edge.label ?? null,
    }))
    .sort((a, b) => {
      const keyA = `${a.sourceNodeId}|${a.sourceHandle}|${a.targetNodeId}|${a.targetHandle}`
      const keyB = `${b.sourceNodeId}|${b.sourceHandle}|${b.targetNodeId}|${b.targetHandle}`
      return keyA.localeCompare(keyB)
    })

  return stableStringify({
    nodes: nodesSig,
    edges: edgesSig,
  })
}

export function computeDeadEndWarnings(
  workflow: WorkflowInput,
  warningMessage: string,
): WorkflowValidationIssue[] {
  const outputNodes = workflow.nodes.filter((node) => node.nodeType === 'output')
  if (outputNodes.length < 1) return []

  const nodeIds = new Set(workflow.nodes.map((node) => node.nodeId))
  const reverseAdj = new Map<string, string[]>()

  workflow.edges.forEach((edge) => {
    if (!nodeIds.has(edge.sourceNodeId) || !nodeIds.has(edge.targetNodeId)) return
    const current = reverseAdj.get(edge.targetNodeId) ?? []
    current.push(edge.sourceNodeId)
    reverseAdj.set(edge.targetNodeId, current)
  })

  const reachable = new Set<string>()
  const queue: string[] = outputNodes.map((node) => node.nodeId)

  while (queue.length > 0) {
    const current = queue.shift()
    if (!current || reachable.has(current)) continue
    reachable.add(current)
    const upstream = reverseAdj.get(current) ?? []
    upstream.forEach((sourceNodeId) => {
      if (!reachable.has(sourceNodeId)) queue.push(sourceNodeId)
    })
  }

  return workflow.nodes
    .filter((node) => node.nodeType !== 'output' && !reachable.has(node.nodeId))
    .sort((a, b) => a.nodeId.localeCompare(b.nodeId))
    .map((node) => {
      const issueBase: Omit<WorkflowValidationIssue, 'id'> = {
        severity: 'warning',
        nodeId: node.nodeId,
        subflowNodeId: null,
        message: warningMessage,
        source: 'reachability',
      }
      return {
        ...issueBase,
        id: buildIssueId(issueBase),
      }
    })
}

export function parseSubflowIssue(message: string): { subflowNodeId: string | null } {
  const matched = message.match(/body node ['"]([^'"]+)['"]/i)
  return { subflowNodeId: matched?.[1] ?? null }
}

export function normalizeValidationIssues(
  backendErrors: WorkflowValidationError[],
  reachabilityWarnings: WorkflowValidationIssue[],
): {
  errors: WorkflowValidationIssue[]
  warnings: WorkflowValidationIssue[]
} {
  const seen = new Set<string>()
  const errors: WorkflowValidationIssue[] = []
  const warnings: WorkflowValidationIssue[] = []

  backendErrors.forEach((error) => {
    const parsed = parseSubflowIssue(error.message)
    const issueBase: Omit<WorkflowValidationIssue, 'id'> = {
      severity: 'error',
      nodeId: error.nodeId,
      subflowNodeId: parsed.subflowNodeId,
      message: error.message,
      source: 'backend',
    }
    const id = buildIssueId(issueBase)
    if (seen.has(id)) return
    seen.add(id)
    errors.push({ ...issueBase, id })
  })

  reachabilityWarnings.forEach((warning) => {
    const issueBase: Omit<WorkflowValidationIssue, 'id'> = {
      severity: 'warning',
      nodeId: warning.nodeId,
      subflowNodeId: warning.subflowNodeId ?? null,
      message: warning.message,
      source: 'reachability',
    }
    const id = buildIssueId(issueBase)
    if (seen.has(id)) return
    seen.add(id)
    warnings.push({ ...issueBase, id })
  })

  return {
    errors,
    warnings,
  }
}
