import { apiClient } from '@/lib/api/client'

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

export interface CreateSkillRequest {
  name: string
  description: string
  intentExamples?: string[]
  tools?: string[]
  targetType?: SkillTargetType
  workflowId?: string
  agentProfileId?: string
  mode?: SkillMode
  langgraphPattern?: LanggraphPattern
  systemPrompt?: string
  enabled?: boolean
  kbConfig?: SkillKBConfig
}

export interface UpdateSkillRequest {
  name?: string
  description?: string
  intentExamples?: string[]
  tools?: string[]
  targetType?: SkillTargetType
  workflowId?: string
  agentProfileId?: string
  mode?: SkillMode
  langgraphPattern?: LanggraphPattern
  systemPrompt?: string
  enabled?: boolean
  kbConfig?: SkillKBConfig
}

export const getSkills = () =>
  apiClient.get<AssistantSkill[]>('/api/assistant-config/skills')

export const getSkill = (id: string) =>
  apiClient.get<AssistantSkill>(`/api/assistant-config/skills/${id}`)

export const createSkill = (data: CreateSkillRequest) =>
  apiClient.post<AssistantSkill>('/api/assistant-config/skills', { body: data })

export const updateSkill = (id: string, data: UpdateSkillRequest) =>
  apiClient.put<AssistantSkill>(`/api/assistant-config/skills/${id}`, { body: data })

export const deleteSkill = (id: string) =>
  apiClient.delete(`/api/assistant-config/skills/${id}`)

export const resetSkill = (id: string) =>
  apiClient.post<AssistantSkill>(`/api/assistant-config/skills/${id}/reset`, {
    body: { confirm: true },
  })

export interface ResetAllSkillsResponse {
  resetCount: number
  deletedCount: number
  createdCount: number
  affected: Array<{ name: string; id: string | null; action: string }>
}

export const resetAllSkills = () =>
  apiClient.post<ResetAllSkillsResponse>('/api/assistant-config/skills/reset-all', {
    body: { confirm: true },
  })
