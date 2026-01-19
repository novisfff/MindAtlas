import { apiClient } from '@/lib/api/client'

export interface SkillStep {
  id: string
  stepOrder: number
  type: 'analysis' | 'tool' | 'summary'
  instruction: string | null
  toolName: string | null
  argsFrom: 'context' | 'previous' | 'custom' | 'json' | null
  argsTemplate: string | null
  outputMode: 'text' | 'json' | null
  outputFields: string[] | null
  includeInSummary: boolean | null
  createdAt: string
  updatedAt: string
}

export type SkillMode = 'steps' | 'agent'

export interface AssistantSkill {
  id: string
  name: string
  description: string
  intentExamples: string[] | null
  tools: string[] | null
  mode: SkillMode
  systemPrompt: string | null
  isSystem: boolean
  enabled: boolean
  steps: SkillStep[]
  createdAt: string
  updatedAt: string
}

export interface SkillStepInput {
  type: 'analysis' | 'tool' | 'summary'
  instruction?: string
  toolName?: string
  argsFrom?: 'context' | 'previous' | 'custom' | 'json'
  argsTemplate?: string
  outputMode?: 'text' | 'json'
  outputFields?: string[]
  includeInSummary?: boolean
}

export interface CreateSkillRequest {
  name: string
  description: string
  intentExamples?: string[]
  tools?: string[]
  mode?: SkillMode
  systemPrompt?: string
  steps?: SkillStepInput[]
  enabled?: boolean
}

export interface UpdateSkillRequest {
  name?: string
  description?: string
  intentExamples?: string[]
  tools?: string[]
  mode?: SkillMode
  systemPrompt?: string
  steps?: SkillStepInput[]
  enabled?: boolean
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
