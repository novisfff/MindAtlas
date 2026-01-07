import { apiClient } from '@/lib/api/client'
import type { Tag } from '@/types'

export interface TagUpsertRequest {
  name: string
  color?: string
  description?: string
}

export async function listTags(): Promise<Tag[]> {
  return apiClient.get<Tag[]>('/api/tags')
}

export async function createTag(payload: TagUpsertRequest): Promise<Tag> {
  return apiClient.post<Tag>('/api/tags', { body: payload })
}

export async function updateTag(id: string, payload: TagUpsertRequest): Promise<Tag> {
  return apiClient.put<Tag>(`/api/tags/${encodeURIComponent(id)}`, { body: payload })
}

export async function deleteTag(id: string): Promise<void> {
  await apiClient.delete<void>(`/api/tags/${encodeURIComponent(id)}`)
}
