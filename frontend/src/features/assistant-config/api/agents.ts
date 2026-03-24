import { apiClient } from '@/lib/api/client'
import { withMindAtlasLocale } from '@/lib/api/locale'
import { SSEParser } from '@/lib/sse/SSEParser'

export interface AssistantAgentProfile {
  id: string
  name: string
  description: string
  systemPrompt: string | null
  tools: string[] | null
  kbConfig: { enabled?: boolean } | null
  modelSource?: 'default' | 'custom'
  modelId?: string | null
  isSystem: boolean
  enabled: boolean
  draftVersionId: string | null
  publishedVersionId: string | null
  referencedSkillIds: string[]
  referenceCount: number
  referencedSystemBehaviorKeys: string[]
  systemBehaviorReferenceCount: number
  createdAt: string
  updatedAt: string
}

export interface CreateAgentProfileRequest {
  name: string
  description?: string
  systemPrompt: string
  tools?: string[]
  kbConfig?: { enabled?: boolean }
  modelSource?: 'default' | 'custom'
  modelId?: string | null
  enabled?: boolean
}

export interface UpdateAgentProfileRequest {
  name?: string
  description?: string
  systemPrompt?: string
  tools?: string[]
  kbConfig?: { enabled?: boolean }
  modelSource?: 'default' | 'custom'
  modelId?: string | null
  enabled?: boolean
}

export interface AgentVersionRecord {
  id: string
  sequenceNo: number
  versionName: string
  versionSource: 'save' | 'publish'
  createdAt: string
  updatedAt: string
}

export interface AgentVersionListPayload {
  agentProfileId: string
  draftVersionId: string | null
  publishedVersionId: string | null
  versions: AgentVersionRecord[]
}

export interface AgentPublishDraftInput {
  systemPrompt: string
  tools?: string[]
  kbConfig?: { enabled?: boolean } | null
  modelSource?: 'default' | 'custom'
  modelId?: string | null
}

export type AgentTestRunDraftInput = AgentPublishDraftInput

export interface AgentPublishRequest {
  draft: AgentPublishDraftInput
  versionName?: string | null
}

export interface AgentRollbackResponse {
  draftVersionId: string | null
  publishedVersionId: string | null
  agentDraft: AgentPublishDraftInput | null
}

export interface AgentDeleteVersionResponse {
  deletedVersionId: string
  draftVersionId: string | null
  publishedVersionId: string | null
}

export interface AgentClearVersionsResponse {
  deletedCount: number
  keptLatestVersionId: string | null
  draftVersionId: string | null
  publishedVersionId: string | null
}

export const getAgentProfiles = () =>
  apiClient.get<AssistantAgentProfile[]>('/api/assistant-config/agents')

export const getAgentProfile = (id: string) =>
  apiClient.get<AssistantAgentProfile>(`/api/assistant-config/agents/${id}`)

export const createAgentProfile = (data: CreateAgentProfileRequest) =>
  apiClient.post<AssistantAgentProfile>('/api/assistant-config/agents', { body: data })

export const updateAgentProfile = (id: string, data: UpdateAgentProfileRequest) =>
  apiClient.put<AssistantAgentProfile>(`/api/assistant-config/agents/${id}`, { body: data })

export const listAgentVersions = (id: string) =>
  apiClient.get<AgentVersionListPayload>(`/api/assistant-config/agents/${id}/versions`)

export const publishAgent = (id: string, data: AgentPublishRequest) =>
  apiClient.post<AssistantAgentProfile>(`/api/assistant-config/agents/${id}/publish`, { body: data })

export const rollbackAgentVersion = (id: string, versionId: string) =>
  apiClient.post<AgentRollbackResponse>(`/api/assistant-config/agents/${id}/versions/${versionId}/rollback`)

export const deleteAgentVersion = (id: string, versionId: string) =>
  apiClient.delete<AgentDeleteVersionResponse>(`/api/assistant-config/agents/${id}/versions/${versionId}`)

export const clearAgentVersions = (id: string) =>
  apiClient.post<AgentClearVersionsResponse>(`/api/assistant-config/agents/${id}/versions/clear`)

export const deleteAgentProfile = (
  id: string,
  options: { confirmRebindSystemBehaviors?: boolean } = {},
) =>
  apiClient.delete(`/api/assistant-config/agents/${id}`, {
    query: {
      confirmRebindSystemBehaviors: options.confirmRebindSystemBehaviors ?? false,
    },
  })

export interface AgentTestRunRequest {
  draft: AgentTestRunDraftInput
  userInput: string
  history?: Array<{
    role: 'user' | 'assistant'
    content: string
  }>
  streamOutput?: boolean
}

export type AgentTestRunEvent =
  | {
      event: 'run_start'
      data: {
        runId: string
        agentProfileId: string
        streamOutput: boolean
        modelSource?: 'default' | 'custom'
        modelId?: string | null
        startedAt: string
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
      event: 'tool_call_start'
      data: {
        runId: string
        toolCallId: string
        name: string
        args: Record<string, unknown>
        startedAt?: string
        agentRound?: number
        toolCallIndex?: number
        toolKind?: 'tool' | 'knowledge'
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
        startedAt?: string | null
        endedAt?: string
        durationMs?: number | null
        agentRound?: number
        toolCallIndex?: number
        toolKind?: 'tool' | 'knowledge'
        ts: string
      }
    }
  | {
      event: 'analysis_start'
      data: {
        runId: string
        analysisId: string
        ts: string
      }
    }
  | {
      event: 'analysis_delta'
      data: {
        runId: string
        analysisId: string
        delta: string
        ts: string
      }
    }
  | {
      event: 'analysis_end'
      data: {
        runId: string
        analysisId: string
        ts: string
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
      event: 'run_end'
      data: {
        runId: string
        status: 'completed' | 'error' | 'cancelled'
        durationMs: number
        finalText: string
        streamOutput: boolean
      }
    }

export interface AgentTestStreamOptions {
  signal?: AbortSignal
  onEvent?: (event: AgentTestRunEvent) => void
}

export const runAgentTestStream = async (
  agentProfileId: string,
  payload: AgentTestRunRequest,
  options: AgentTestStreamOptions = {},
) => {
  const response = await fetch(`/api/assistant-config/agents/${agentProfileId}/test-run`, {
    method: 'POST',
    headers: withMindAtlasLocale({
      'Content-Type': 'application/json',
    }),
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
        options.onEvent?.(evt as AgentTestRunEvent)
      }
    }
  } finally {
    reader.releaseLock()
  }
}
