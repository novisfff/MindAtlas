import { apiClient } from '@/lib/api/client'

export interface SkillKBConfig {
  enabled?: boolean
}

// Output field type options
export type OutputFieldType = 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array'

// Output field specification for JSON mode
export interface OutputFieldSpec {
  name: string
  type: OutputFieldType
  nullable: boolean
  itemsType?: OutputFieldType
  enum?: string[]
}

export interface SkillStep {
  id: string
  stepOrder: number
  type: 'analysis' | 'tool' | 'summary'
  instruction: string | null
  toolName: string | null
  argsFrom: 'context' | 'previous' | 'custom' | 'json' | null
  argsTemplate: string | null
  outputMode: 'text' | 'json' | null
  outputFields: OutputFieldSpec[] | string[] | null
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
  kbConfig: SkillKBConfig | null
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
  outputFields?: OutputFieldSpec[]
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
  kbConfig?: SkillKBConfig
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
