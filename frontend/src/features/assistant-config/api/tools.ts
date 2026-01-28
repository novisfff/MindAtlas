import { apiClient } from '@/lib/api/client'

export type AuthType = 'none' | 'bearer' | 'basic' | 'api-key'
export type BodyType = 'none' | 'form-data' | 'x-www-form-urlencoded' | 'json' | 'xml' | 'raw'

export interface InputParam {
  name: string
  description?: string
  paramType: string
  required: boolean
}

// 系统工具完整定义（从代码获取）
export interface SystemToolDefinition {
  name: string
  description: string | null
  kind: 'local'
  isSystem: true
  enabled: boolean
  inputParams: InputParam[] | null
  returns: string | null
  jsonSchema: Record<string, unknown> | null
}

export interface AssistantTool {
  id: string
  name: string
  description: string | null
  kind: string
  isSystem: boolean
  enabled: boolean
  inputParams: InputParam[] | null
  endpointUrl: string | null
  httpMethod: string | null
  headers: Record<string, string> | null
  queryParams: Record<string, string> | null
  bodyType: BodyType | null
  bodyContent: string | null
  authType: AuthType | null
  authHeaderName: string | null
  authScheme: string | null
  apiKeyHint: string | null
  timeoutSeconds: number | null
  payloadWrapper: string | null
  createdAt: string
  updatedAt: string
}

export interface CreateToolRequest {
  name: string
  description?: string
  kind?: 'remote'
  enabled?: boolean
  inputParams?: InputParam[]
  endpointUrl: string
  httpMethod?: string
  headers?: Record<string, string>
  queryParams?: Record<string, string>
  bodyType?: BodyType
  bodyContent?: string
  authType?: AuthType
  authHeaderName?: string
  authScheme?: string
  apiKey?: string
  timeoutSeconds?: number
  payloadWrapper?: string
}

export interface UpdateToolRequest {
  name?: string
  description?: string
  enabled?: boolean
  inputParams?: InputParam[]
  endpointUrl?: string
  httpMethod?: string
  headers?: Record<string, string>
  queryParams?: Record<string, string>
  bodyType?: BodyType
  bodyContent?: string
  authType?: AuthType
  authHeaderName?: string
  authScheme?: string
  apiKey?: string
  timeoutSeconds?: number
  payloadWrapper?: string
}

export const getTools = () =>
  apiClient.get<AssistantTool[]>('/api/assistant-config/tools')

export const getTool = (id: string) =>
  apiClient.get<AssistantTool>(`/api/assistant-config/tools/${id}`)

export const createTool = (data: CreateToolRequest) =>
  apiClient.post<AssistantTool>('/api/assistant-config/tools', { body: data })

export const updateTool = (id: string, data: UpdateToolRequest) =>
  apiClient.put<AssistantTool>(`/api/assistant-config/tools/${id}`, { body: data })

export const deleteTool = (id: string) =>
  apiClient.delete(`/api/assistant-config/tools/${id}`)

// 获取系统工具完整定义（从代码获取）
export const getSystemToolDefinitions = (params?: {
  includeDisabled?: boolean
  includeSchema?: boolean
}) =>
  apiClient.get<SystemToolDefinition[]>('/api/assistant-config/system-tools/definitions', {
    query: {
      include_disabled: params?.includeDisabled ?? true,
      include_schema: params?.includeSchema ?? false,
    },
  })

export const updateSystemToolEnabled = (name: string, enabled: boolean) =>
  apiClient.put<{ name: string; enabled: boolean }>(
    `/api/assistant-config/system-tools/${encodeURIComponent(name)}/enabled`,
    { body: { enabled } }
  )
