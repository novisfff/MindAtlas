import { apiClient } from '@/lib/api/client'

// ==================== Node Types ====================

export type NodeType =
  | 'start'
  | 'llm'
  | 'tool'
  | 'if_else'
  | 'template'
  | 'parameter_extractor'
  | 'knowledge_retrieval'
  | 'variable_aggregator'

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

export type MergeStrategy = 'all_required' | 'first_completed'

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
  isOutput?: boolean
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

export interface TemplateNodeConfig {
  template?: string
}

export interface ParameterExtractorNodeConfig {
  instruction?: string
  outputFields?: Array<{ name: string; type?: string; nullable?: boolean }>
}

export interface KnowledgeRetrievalNodeConfig {
  query?: string
  topK?: number
}

export interface VariableAggregatorNodeConfig {
  sourceNodes?: string[]
  mergeStrategy?: MergeStrategy
}

export type NodeConfig =
  | LLMNodeConfig
  | ToolNodeConfig
  | IfElseNodeConfig
  | TemplateNodeConfig
  | ParameterExtractorNodeConfig
  | KnowledgeRetrievalNodeConfig
  | VariableAggregatorNodeConfig
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
