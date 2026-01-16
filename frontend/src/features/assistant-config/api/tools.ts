import { apiClient } from '@/lib/api/client'

export type AuthType = 'none' | 'bearer' | 'basic' | 'api-key'
export type BodyType = 'none' | 'form-data' | 'x-www-form-urlencoded' | 'json' | 'xml' | 'raw'

export interface InputParam {
  name: string
  description?: string
  paramType: string
  required: boolean
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
