import { apiClient } from '@/lib/api/client'

export interface AiProvider {
  id: string
  name: string
  baseUrl: string
  model: string
  apiKeyHint: string
  isActive: boolean
  createdAt: string
  updatedAt: string
}

export interface AiProviderCreateRequest {
  name: string
  baseUrl: string
  model: string
  apiKey: string
}

export interface AiProviderUpdateRequest {
  name?: string
  baseUrl?: string
  model?: string
  apiKey?: string
}

export interface AiProviderTestResult {
  ok: boolean
  statusCode?: number
  message?: string
}

export interface FetchModelsRequest {
  baseUrl: string
  apiKey: string
}

export interface FetchModelsResult {
  ok: boolean
  models: string[]
  message?: string
}

export async function listAiProviders(): Promise<AiProvider[]> {
  return apiClient.get<AiProvider[]>('/api/ai-providers')
}

export async function getAiProvider(id: string): Promise<AiProvider> {
  return apiClient.get<AiProvider>(`/api/ai-providers/${id}`)
}

export async function createAiProvider(payload: AiProviderCreateRequest): Promise<AiProvider> {
  return apiClient.post<AiProvider>('/api/ai-providers', { body: payload })
}

export async function updateAiProvider(id: string, payload: AiProviderUpdateRequest): Promise<AiProvider> {
  return apiClient.put<AiProvider>(`/api/ai-providers/${id}`, { body: payload })
}

export async function deleteAiProvider(id: string): Promise<void> {
  return apiClient.delete<void>(`/api/ai-providers/${id}`)
}

export async function activateAiProvider(id: string): Promise<AiProvider> {
  return apiClient.post<AiProvider>(`/api/ai-providers/${id}/activate`)
}

export async function testAiProviderConnection(id: string): Promise<AiProviderTestResult> {
  return apiClient.post<AiProviderTestResult>(`/api/ai-providers/${id}/test-connection`)
}

export async function fetchModels(payload: FetchModelsRequest): Promise<FetchModelsResult> {
  return apiClient.post<FetchModelsResult>('/api/ai-providers/fetch-models', { body: payload })
}

export async function fetchModelsById(id: string): Promise<FetchModelsResult> {
  return apiClient.post<FetchModelsResult>(`/api/ai-providers/${id}/fetch-models`)
}
