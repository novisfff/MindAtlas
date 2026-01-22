import { apiClient } from '@/lib/api/client'

// ==================== Types ====================

export type AiModelType = 'llm' | 'embedding'

export interface AiModel {
  id: string
  credentialId: string
  name: string
  modelType: AiModelType
  createdAt: string
  updatedAt: string
}

export interface AiModelCreateRequest {
  credentialId: string
  name: string
  modelType: AiModelType
}

export interface AiModelUpdateRequest {
  name?: string
  modelType?: AiModelType
}

// ==================== API Functions ====================

export async function fetchModels(params?: {
  credentialId?: string
  modelType?: AiModelType
}): Promise<AiModel[]> {
  const searchParams = new URLSearchParams()
  if (params?.credentialId) searchParams.set('credentialId', params.credentialId)
  if (params?.modelType) searchParams.set('modelType', params.modelType)
  const query = searchParams.toString()
  return apiClient.get(`/api/ai-models${query ? `?${query}` : ''}`)
}

export async function fetchModel(id: string): Promise<AiModel> {
  return apiClient.get(`/api/ai-models/${id}`)
}

export async function createModel(data: AiModelCreateRequest): Promise<AiModel> {
  return apiClient.post('/api/ai-models', { body: data })
}

export async function updateModel(id: string, data: AiModelUpdateRequest): Promise<AiModel> {
  return apiClient.put(`/api/ai-models/${id}`, { body: data })
}

export async function deleteModel(id: string): Promise<void> {
  return apiClient.delete(`/api/ai-models/${id}`)
}
