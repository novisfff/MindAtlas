import { apiClient } from '@/lib/api/client'

// ==================== Types ====================

export interface AiCredential {
  id: string
  name: string
  baseUrl: string
  apiKeyHint: string
  createdAt: string
  updatedAt: string
}

export interface AiCredentialCreateRequest {
  name: string
  baseUrl: string
  apiKey: string
}

export interface AiCredentialUpdateRequest {
  name?: string
  baseUrl?: string
  apiKey?: string
}

export interface AiCredentialTestResult {
  ok: boolean
  statusCode?: number
  message?: string
}

export interface DiscoveredModel {
  name: string
  suggestedType: 'llm' | 'embedding'
}

export interface DiscoverModelsResult {
  ok: boolean
  models: DiscoveredModel[]
  message?: string
}

// ==================== API Functions ====================

export async function fetchCredentials(): Promise<AiCredential[]> {
  return apiClient.get('/api/ai-credentials')
}

export async function fetchCredential(id: string): Promise<AiCredential> {
  return apiClient.get(`/api/ai-credentials/${id}`)
}

export async function createCredential(data: AiCredentialCreateRequest): Promise<AiCredential> {
  return apiClient.post('/api/ai-credentials', { body: data })
}

export async function updateCredential(id: string, data: AiCredentialUpdateRequest): Promise<AiCredential> {
  return apiClient.put(`/api/ai-credentials/${id}`, { body: data })
}

export async function deleteCredential(id: string): Promise<void> {
  return apiClient.delete(`/api/ai-credentials/${id}`)
}

export async function testCredentialConnection(id: string): Promise<AiCredentialTestResult> {
  return apiClient.post(`/api/ai-credentials/${id}/test-connection`)
}

export async function discoverModelsByCredential(id: string): Promise<DiscoverModelsResult> {
  return apiClient.post(`/api/ai-credentials/${id}/discover-models`)
}

export async function discoverModelsByKey(baseUrl: string, apiKey: string): Promise<DiscoverModelsResult> {
  return apiClient.post('/api/ai-credentials/discover-models', { body: { baseUrl, apiKey } })
}
