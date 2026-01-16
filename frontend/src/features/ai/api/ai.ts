import { apiClient } from '@/lib/api/client'

export interface AiGenerateRequest {
  title: string
  content: string
  typeName: string
}

export interface AiGenerateResponse {
  refinedContent?: string
  summary?: string
  suggestedTags: string[]
}

export async function generateAiContent(request: AiGenerateRequest): Promise<AiGenerateResponse> {
  return apiClient.post<AiGenerateResponse>('/api/ai/generate', { body: request })
}
