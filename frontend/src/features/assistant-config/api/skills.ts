/**
 * Shared legacy skill shape types.
 *
 * HTTP clients for `/api/assistant-config/skills` are retired (410 Gone).
 * Keep the deserialize/target option shapes for workflow preview + target keys.
 */

export interface SkillKBConfig {
  enabled?: boolean
}

export type SkillMode = 'langgraph'
export type LanggraphPattern = 'agent_loop' | 'workflow_dag'
export type SkillTargetType = 'workflow' | 'agent'

export interface SkillTargetSummary {
  id: string
  name: string
  enabled: boolean
}

export interface AssistantSkill {
  id: string
  name: string
  description: string
  intentExamples: string[] | null
  tools: string[] | null
  mode: SkillMode
  targetType: SkillTargetType | null
  workflowId: string | null
  agentProfileId: string | null
  targetSummary?: SkillTargetSummary | null
  langgraphPattern: LanggraphPattern | null
  systemPrompt: string | null
  isSystem: boolean
  enabled: boolean
  kbConfig: SkillKBConfig | null
  workflowVersion?: number
  workflowViewport?: { x: number; y: number; zoom: number } | null
  nodes?: Array<{
    id: string
    nodeId: string
    nodeType: string
    label: string
    positionX: number
    positionY: number
    config: Record<string, unknown> | null
    createdAt: string
    updatedAt: string
  }>
  edges?: Array<{
    id: string
    edgeId: string
    sourceNodeId: string
    targetNodeId: string
    sourceHandle: string
    targetHandle: string
    conditionType: string | null
    conditionExpr: Record<string, unknown> | null
    label: string | null
    createdAt: string
    updatedAt: string
  }>
  createdAt: string
  updatedAt: string
}
