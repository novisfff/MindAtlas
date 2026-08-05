import { apiClient, browserFetchInit, reportBrowserSessionExpired } from '@/lib/api/client'
import { withMindAtlasLocale } from '@/lib/api/locale'
import { SSEParser } from '@/lib/sse/SSEParser'

// ==================== Node Types ====================

export type NodeType =
  | 'start'
  | 'llm'
  | 'agent'
  | 'tool'
  | 'if_else'
  | 'parameter_extractor'
  | 'knowledge_retrieval'
  | 'iteration'
  | 'loop'
  | 'code_executor'
  | 'http_request'
  | 'variable_assign'
  | 'human_in_loop'
  | 'workflow_call'
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

export interface StartStructuredField {
  name: string
  type: 'string' | 'number' | 'integer' | 'boolean'
  required?: boolean
  description?: string
}

export type WorkflowEnvVarType = 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array'

export interface WorkflowSessionVar {
  name: string
  type: WorkflowEnvVarType
  defaultValue?: unknown
  description?: string
}

export interface StartNodeConfig {
  inputMode?: 'text' | 'structured'
  memoryMode?: 'auto' | 'off' | 'structured'
  structuredFields?: StartStructuredField[]
  sessionVars?: WorkflowSessionVar[]
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

export interface AgentNodeConfig {
  systemPrompt?: string
  userInput?: string
  toolNames?: string[]
  maxIterations?: number
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

export type WorkflowCallBindingMode = 'pinned' | 'latest'

export interface WorkflowCallNodeConfig {
  targetWorkflowId?: string
  bindingMode?: WorkflowCallBindingMode
  targetPublishedVersionId?: string | null
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

export interface CodeExecutorNodeConfig {
  language?: 'python' | 'javascript'
  code?: string
  entrypoint?: string
  timeoutMs?: number
  inputBindings?: Record<string, string>
  outputFields?: Array<{
    name: string
    type?: string
    nullable?: boolean
    itemsType?: string
    enum?: string[]
  }>
}

export interface HttpRequestKeyValueRow {
  key: string
  value: string
  type?: 'text' | 'file'
  enabled?: boolean
}

export interface HttpRequestNodeConfig {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  url?: string
  headers?: HttpRequestKeyValueRow[]
  queryParams?: HttpRequestKeyValueRow[]
  bodyType?: 'none' | 'json' | 'raw' | 'x-www-form-urlencoded' | 'form-data'
  jsonBodyTemplate?: string
  rawBodyTemplate?: string
  formBody?: HttpRequestKeyValueRow[]
  authType?: 'none' | 'bearer' | 'api_key'
  bearerToken?: string
  apiKeyIn?: 'header' | 'query'
  apiKeyName?: string
  apiKeyValue?: string
  timeoutMs?: number
  retryEnabled?: boolean
  maxRetries?: number
  retryIntervalMs?: number
  verifySsl?: boolean
}

export interface VariableAssignNodeConfig {
  variableName?: string
  operation?: 'set' | 'increment' | 'append' | 'clear'
  valueTemplate?: string
}

export type HumanInLoopFieldType = 'string' | 'number' | 'integer' | 'boolean' | 'array'
export type HumanInLoopFieldWidget =
  | 'input'
  | 'textarea'
  | 'switch'
  | 'select'
  | 'radio'
  | 'checkbox_group'
  | 'tag_selector'
  | 'date'
  | 'time'

export interface HumanInLoopOptionObject {
  value: string
  label: string
  description?: string
}

export type HumanInLoopOption = string | HumanInLoopOptionObject

export interface HumanInLoopFieldConfig {
  name: string
  label?: string
  type: HumanInLoopFieldType
  widget?: HumanInLoopFieldWidget
  options?: HumanInLoopOption[]
  optionsTemplate?: string
  optionValueKey?: string
  allowCustom?: boolean
  placeholder?: string
  required?: boolean
  valueTemplate?: string
}

export interface HumanInLoopNodeConfig {
  title?: string
  instruction?: string
  fields?: HumanInLoopFieldConfig[]
  approveLabel?: string
  rejectLabel?: string
  requireRejectComment?: boolean
}

export type ContainerBodyNodeType =
  | 'start'
  | 'llm'
  | 'agent'
  | 'tool'
  | 'if_else'
  | 'parameter_extractor'
  | 'knowledge_retrieval'
  | 'code_executor'
  | 'http_request'
  | 'variable_assign'
  | 'human_in_loop'
  | 'workflow_call'

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
  | StartNodeConfig
  | LLMNodeConfig
  | AgentNodeConfig
  | OutputNodeConfig
  | ToolNodeConfig
  | WorkflowCallNodeConfig
  | IfElseNodeConfig
  | ParameterExtractorNodeConfig
  | KnowledgeRetrievalNodeConfig
  | CodeExecutorNodeConfig
  | HttpRequestNodeConfig
  | VariableAssignNodeConfig
  | HumanInLoopNodeConfig
  | IterationNodeConfig
  | LoopNodeConfig
  | Record<string, unknown>

export interface WorkflowHumanApproval {
  id: string
  runId: string
  runtimeWaiting?: boolean
  channelType: string
  conversationId: string | null
  messageId: string | null
  workflowId: string | null
  skillId: string | null
  nodeId: string
  nodeLabel: string | null
  status: 'pending' | 'approved' | 'rejected' | 'cancelled'
  requestPayload: Record<string, unknown>
  fieldSchema: Array<{
    name: string
    label?: string
    type: HumanInLoopFieldType
    widget?: HumanInLoopFieldWidget
    options?: HumanInLoopOption[]
    allowCustom?: boolean
    placeholder?: string
    required?: boolean
  }>
  initialValues: Record<string, unknown>
  submittedValues: Record<string, unknown>
  decision: 'approved' | 'rejected' | null
  comment: string | null
  resolvedAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

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

interface WorkflowNodeTraceContext {
  nodeExecutionId?: string
}

interface WorkflowAgentToolTraceContext extends WorkflowNodeTraceContext {
  nodeId?: string
  nodeType?: string
  agentRound?: number
  toolCallIndex?: number
  toolKind?: 'tool' | 'knowledge'
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

export type WorkflowCopilotMode =
  | 'generate'
  | 'edit_selection'
  | 'fix_validation'
  | 'analyze_test_run'

export type WorkflowCopilotStatus =
  | 'proposal'
  | 'question'
  | 'analysis'
  | 'no_op'

export type WorkflowCopilotSelectionScope =
  | 'workflow'
  | 'selection'
  | 'container'

export type WorkflowCopilotLayoutRecommendation =
  | 'keep'
  | 'autolayout'

export type WorkflowCopilotOperationType =
  | 'add_node'
  | 'update_node'
  | 'remove_node'
  | 'add_edge'
  | 'remove_edge'
  | 'move_node'
  | 'autolayout'

export interface WorkflowCopilotSelection {
  scope: WorkflowCopilotSelectionScope
  nodeIds: string[]
  edgeIds: string[]
  containerId?: string | null
}

export interface WorkflowCopilotConversationItem {
  role: 'user' | 'assistant'
  content: string
}

export interface WorkflowCopilotValidationIssue {
  severity: 'error' | 'warning'
  nodeId?: string | null
  subflowNodeId?: string | null
  message: string
  source?: string
}

export interface WorkflowCopilotValidationContext {
  errors: WorkflowCopilotValidationIssue[]
  warnings: WorkflowCopilotValidationIssue[]
}

export interface WorkflowCopilotTestRunContext {
  selectedRunId: string
  result: unknown
  trace: unknown
  raw: unknown
}

export interface WorkflowCopilotOperation {
  type: WorkflowCopilotOperationType
  containerId?: string | null
  nodeId?: string | null
  nodeType?: NodeType
  label?: string | null
  config?: Record<string, unknown> | null
  configPatch?: Record<string, unknown> | null
  replaceConfig?: boolean
  positionX?: number | null
  positionY?: number | null
  edgeId?: string | null
  sourceNodeId?: string | null
  targetNodeId?: string | null
  sourceHandle?: string | null
  targetHandle?: string | null
  conditionType?: 'expression' | 'default' | null
  conditionExpr?: ConditionExpression | null
}

export interface WorkflowCopilotProposal {
  title: string
  summary: string
  operations: WorkflowCopilotOperation[]
  proposedWorkflow: WorkflowInput
  baseDraftHash: string
  proposedDraftHash: string
  layoutRecommendation: WorkflowCopilotLayoutRecommendation
  validation: WorkflowValidationResponse
  affectedNodeIds: string[]
  warnings: string[]
}

export interface WorkflowCopilotRequest {
  mode: WorkflowCopilotMode
  instruction: string
  draft: WorkflowInput
  selection?: WorkflowCopilotSelection
  conversation?: WorkflowCopilotConversationItem[]
  validationContext?: WorkflowCopilotValidationContext
  testRunContext?: WorkflowCopilotTestRunContext
}

export interface WorkflowCopilotResponse {
  status: WorkflowCopilotStatus
  message: string
  proposal?: WorkflowCopilotProposal | null
  suggestions?: string[]
}

export interface WorkflowConversationHistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export interface WorkflowTestMemoryScope {
  conversationSummary?: string
  skillFacts?: string[]
}

export interface WorkflowTestSessionMemory extends WorkflowTestMemoryScope {
  workflowCallScopes?: Record<string, WorkflowTestMemoryScope>
}

export interface WorkflowTestRunRequest {
  workflow: WorkflowInput
  userInput?: string
  structuredInput?: Record<string, unknown>
  sessionId?: string
  history?: WorkflowConversationHistoryItem[]
  sessionMemory?: WorkflowTestSessionMemory
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
      } & WorkflowNodeTraceContext
    }
  | {
      event: 'node_output_delta'
      data: {
        runId: string
        nodeId: string
        // merged delta payload (not guaranteed one token per event)
        delta: string
        ts: string
      } & WorkflowNodeTraceContext
    }
  | {
      event: 'branch_decision'
      data: {
        runId: string
        nodeId: string
        handle: string
        ts: string
      } & WorkflowNodeTraceContext
    }
  | {
      event: 'tool_call_start'
      data: {
        runId: string
        toolCallId: string
        name: string
        args: Record<string, unknown>
        startedAt?: string
        ts: string
      } & WorkflowAgentToolTraceContext
    }
  | {
      event: 'tool_call_end'
      data: {
        runId: string
        toolCallId: string
        status: string
        result: string
        startedAt?: string | null
        endedAt?: string
        durationMs?: number | null
        ts: string
      } & WorkflowAgentToolTraceContext
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
      } & WorkflowNodeTraceContext
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
      } & WorkflowNodeTraceContext
    }
  | {
      event: 'run_end'
      data: {
        runId: string
        status: 'completed' | 'error' | 'cancelled'
        durationMs: number
        finalText: string
        finalJson: Record<string, unknown> | Array<unknown> | null
        sessionMemory?: WorkflowTestSessionMemory
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
  | {
      event: 'human_approval_requested'
      data: {
        runId: string
        approval: WorkflowHumanApproval
        ts: string
      }
    }
  | {
      event: 'human_approval_resolved'
      data: {
        runId: string
        approval: WorkflowHumanApproval
        ts: string
      }
    }

export interface WorkflowTestStreamOptions {
  signal?: AbortSignal
  onEvent?: (event: WorkflowRunEvent) => void
  path?: string
}

export interface NodeTypeDefinition {
  type: NodeType
  label: string
  description: string
}

// ==================== API Functions ====================

export const getNodeTypes = () =>
  apiClient.get<NodeTypeDefinition[]>('/api/assistant-config/workflow/node-types')



export const runWorkflowTestStream = async (
  _workflowOrLegacyId: string,
  payload: WorkflowTestRunRequest,
  options: WorkflowTestStreamOptions = {},
) => {
  // Nested skill workflow test-run path removed (Plan 10 B2). Callers must pass options.path.
  const targetPath = options.path
  if (!targetPath) {
    throw new Error('runWorkflowTestStream requires options.path (standalone workflow test-run)')
  }
  const response = await fetch(
    targetPath,
    browserFetchInit({
      method: 'POST',
      headers: withMindAtlasLocale({
        'Content-Type': 'application/json',
      }),
      body: JSON.stringify(payload),
      signal: options.signal,
    }),
  )

  if (!response.ok || !response.body) {
    reportBrowserSessionExpired(targetPath, response.status)
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
