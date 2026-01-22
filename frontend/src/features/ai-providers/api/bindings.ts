import { apiClient } from '@/lib/api/client'
import type { AiModel } from './models'

// ==================== Types ====================

export interface ComponentBinding {
  llmModelId: string | null
  embeddingModelId: string | null
  llmModel: AiModel | null
  embeddingModel: AiModel | null
}

export interface ModelBindings {
  assistant: ComponentBinding
  lightrag: ComponentBinding
}

export interface UpdateComponentBindingRequest {
  llmModelId?: string | null
  embeddingModelId?: string | null
}

export interface UpdateModelBindingsRequest {
  assistant?: UpdateComponentBindingRequest
  lightrag?: UpdateComponentBindingRequest
}

// ==================== API Functions ====================

export async function fetchModelBindings(): Promise<ModelBindings> {
  return apiClient.get('/api/model-bindings')
}

export async function updateModelBindings(data: UpdateModelBindingsRequest): Promise<ModelBindings> {
  return apiClient.put('/api/model-bindings', { body: data })
}
