import { apiClient } from '@/lib/api/client'
import { SSEParser } from '@/lib/sse/SSEParser'

// ==================== Node Types ====================

export type NodeType =
  | 'start'
  | 'llm'
  | 'tool'
  | 'if_else'
  | 'parameter_extractor'
  | 'knowledge_retrieval'
  | 'iteration'
  | 'loop'
  | 'output'

export type ConditionOperator =
  | 'contains'
  | 'not_contains'
  | 'starts_with'
  | 'ends_with'
  | 'is'
  | 'is_not'
  | 'is_empty'
  | 'is_not_empty'
  // legacy operators (read compatibility only)
  | 'equals'
  | 'not_equals'
  | 'gt'
  | 'lt'
  | 'gte'
  | 'lte'

// ==================== Node Configs ====================

export interface ConditionExpression {
  id: string
  variable: string
  operator: ConditionOperator
  value: string | null
  handle: string
}

export interface ConditionClause {
  id: string
  variable: string
  operator: ConditionOperator
  value: string | null
}

export interface IfElseBranch {
  id: string
  label: string
  logic: 'and' | 'or'
  conditions: ConditionClause[]
}

export interface LLMNodeConfig {
  systemPrompt?: string
  outputMode?: 'text' | 'structured' | 'json'
  outputFields?: Array<{
    name: string
    type?: string
    nullable?: boolean
    itemsType?: string
    enum?: string[]
  }>
  temperature?: number
  userInput?: string
  knowledgeEnabled?: boolean
  knowledgeSourceNodeIds?: string[]
  knowledgeInjectMode?: 'references_only' | 'full_payload'
  knowledgeMaxRefs?: number
  modelSource?: 'default' | 'custom'
  modelId?: string
}

export interface OutputNodeConfig {
  outputMode?: 'text' | 'structured' | 'json'
  textTemplate?: string
  outputFields?: Array<{
    name: string
    type?: string
    nullable?: boolean
    itemsType?: string
    enum?: string[]
    value: string
  }>
}

export interface ToolNodeConfig {
  toolName?: string
  inputBindings?: Record<string, string>
}

export interface IfElseNodeConfig {
  branches?: IfElseBranch[]
  elseHandle?: string
  // legacy field, kept for read compatibility
  conditions?: ConditionExpression[]
}

export interface ParameterExtractorNodeConfig {
  inputContent?: string
  instruction?: string
  outputFields?: Array<{
    name: string
    type?: string
    nullable?: boolean
    itemsType?: string
    enum?: string[]
  }>
  modelSource?: 'default' | 'custom'
  modelId?: string
}

export interface KnowledgeRetrievalNodeConfig {
  query?: string
  mode?: string
  topK?: number
}

export type ContainerBodyNodeType =
  | 'start'
  | 'llm'
  | 'tool'
  | 'if_else'
  | 'parameter_extractor'
  | 'knowledge_retrieval'

export interface ContainerBodyNode {
  nodeId: string
  nodeType: ContainerBodyNodeType
  label: string
  positionX?: number
  positionY?: number
  config?: Record<string, unknown> | null
}

export interface ContainerBodyEdge {
  edgeId: string
  sourceNodeId: string
  targetNodeId: string
  sourceHandle?: string
  targetHandle?: string
  conditionType?: 'expression' | 'default' | null
  conditionExpr?: ConditionExpression | null
  label?: string | null
}

export interface IterationNodeConfig {
  inputSource?: string
  outputVariable?: string
  outputSelector?: string
  parallelMode?: boolean
  errorStrategy?: 'fail_fast' | 'skip_item'
  flattenOutput?: boolean
  bodyNodes?: ContainerBodyNode[]
  bodyEdges?: ContainerBodyEdge[]
}

export interface LoopNodeConfig {
  initialVars?: Array<{ name: string; value?: string }>
  updateMappings?: Array<{ name: string; value: string }>
  terminationLogic?: 'and' | 'or'
  terminationConditions?: ConditionClause[]
  maxIterations?: number
  bodyNodes?: ContainerBodyNode[]
  bodyEdges?: ContainerBodyEdge[]
}

export type NodeConfig =
  | LLMNodeConfig
  | OutputNodeConfig
  | ToolNodeConfig
  | IfElseNodeConfig
  | ParameterExtractorNodeConfig
  | KnowledgeRetrievalNodeConfig
  | IterationNodeConfig
  | LoopNodeConfig
  | Record<string, unknown>

// ==================== Workflow Data ====================

export interface WorkflowNode {
  id: string
  nodeId: string
  nodeType: NodeType
  label: string
  positionX: number
  positionY: number
  config: NodeConfig | null
  createdAt: string
  updatedAt: string
}

export interface WorkflowEdge {
  id: string
  edgeId: string
  sourceNodeId: string
  targetNodeId: string
  sourceHandle: string
  targetHandle: string
  conditionType: string | null
  conditionExpr: ConditionExpression | null
  label: string | null
  createdAt: string
  updatedAt: string
}

// ==================== API Input Types ====================

export interface WorkflowNodeInput {
  nodeId: string
  nodeType: NodeType
  label?: string
  positionX?: number
  positionY?: number
  config?: NodeConfig | null
}

export interface WorkflowEdgeInput {
  edgeId: string
  sourceNodeId: string
  targetNodeId: string
  sourceHandle?: string
  targetHandle?: string
  conditionType?: 'expression' | 'default' | null
  conditionExpr?: ConditionExpression | null
  label?: string | null
}

export interface WorkflowInput {
  nodes: WorkflowNodeInput[]
  edges: WorkflowEdgeInput[]
  viewport?: { x: number; y: number; zoom: number } | null
}

export interface WorkflowValidationError {
  nodeId: string | null
  message: string
}

export interface WorkflowValidationResponse {
  valid: boolean
  errors: WorkflowValidationError[]
}

export interface WorkflowTestRunRequest {
  workflow: WorkflowInput
  userInput: string
  streamOutput?: boolean
}

export type WorkflowRunEvent =
  | {
      event: 'run_start'
      data: {
        runId: string
        skillId: string
        streamOutput: boolean
        startedAt: string
      }
    }
  | {
      event: 'node_start'
      data: {
        runId: string
        nodeId: string
        nodeType: string
        ts: string
      }
    }
  | {
      event: 'node_output_delta'
      data: {
        runId: string
        nodeId: string
        // merged delta payload (not guaranteed one token per event)
        delta: string
        ts: string
      }
    }
  | {
      event: 'branch_decision'
      data: {
        runId: string
        nodeId: string
        handle: string
        ts: string
      }
    }
  | {
      event: 'tool_call_start'
      data: {
        runId: string
        toolCallId: string
        name: string
        args: Record<string, unknown>
        ts: string
      }
    }
  | {
      event: 'tool_call_end'
      data: {
        runId: string
        toolCallId: string
        status: string
        result: string
        ts: string
      }
    }
  | {
      event: 'content_delta'
      data: {
        runId: string
        // merged delta payload (not guaranteed one token per event)
        delta: string
        ts: string
      }
    }
  | {
      event: 'node_end'
      data: {
        runId: string
        nodeId: string
        status: string
        ts: string
      }
    }
  | {
      event: 'node_snapshot'
      data: {
        runId: string
        nodeId: string
        nodeType: string
        status: 'ok' | 'error'
        input: unknown
        output: unknown | null
        errorMessage: string | null
        hardTruncated?: boolean
        ts: string
      }
    }
  | {
      event: 'run_end'
      data: {
        runId: string
        status: 'completed' | 'error' | 'cancelled'
        durationMs: number
        finalText: string
        finalJson: Record<string, unknown> | Array<unknown> | null
        streamOutput: boolean
      }
    }
  | {
      event: 'run_error'
      data: {
        runId: string
        message: string
        stage: 'bootstrap' | 'runtime' | 'unknown'
        ts: string
      }
    }

export interface WorkflowTestStreamOptions {
  signal?: AbortSignal
  onEvent?: (event: WorkflowRunEvent) => void
}

export interface NodeTypeDefinition {
  type: NodeType
  label: string
  description: string
}

// ==================== API Functions ====================

export const getNodeTypes = () =>
  apiClient.get<NodeTypeDefinition[]>('/api/assistant-config/workflow/node-types')

export const saveWorkflow = (skillId: string, data: WorkflowInput) =>
  apiClient.put(`/api/assistant-config/skills/${skillId}/workflow`, { body: data })

export const validateWorkflow = (skillId: string, data: WorkflowInput) =>
  apiClient.post<WorkflowValidationResponse>(
    `/api/assistant-config/skills/${skillId}/validate-workflow`,
    { body: data },
  )

export const runWorkflowTestStream = async (
  skillId: string,
  payload: WorkflowTestRunRequest,
  options: WorkflowTestStreamOptions = {},
) => {
  const response = await fetch(`/api/assistant-config/skills/${skillId}/workflow/test-run`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
    signal: options.signal,
  })

  if (!response.ok || !response.body) {
    const text = await response.text()
    let message = `HTTP ${response.status}: ${response.statusText}`
    try {
      const payloadError = text ? JSON.parse(text) : null
      if (payloadError && typeof payloadError === 'object') {
        const maybeMsg = (payloadError as { message?: unknown }).message
        if (typeof maybeMsg === 'string' && maybeMsg.trim()) {
          message = maybeMsg
        }
      }
    } catch {
      if (text.trim()) message = text
    }
    throw new Error(message)
  }

  const parser = new SSEParser()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      const events = parser.parse(chunk)
      for (const evt of events) {
        options.onEvent?.(evt as WorkflowRunEvent)
      }
    }
  } finally {
    reader.releaseLock()
  }
}
