import { apiClient } from '@/lib/api/client'
import type { EntryType } from '@/types'

export interface EntryTypeUpsertRequest {
  code: string
  name: string
  description?: string
  color?: string
  icon?: string
  graphEnabled?: boolean
  aiEnabled?: boolean
  enabled?: boolean
}

export async function listEntryTypes(): Promise<EntryType[]> {
  return apiClient.get<EntryType[]>('/api/entry-types')
}

export async function getEntryType(id: string): Promise<EntryType> {
  return apiClient.get<EntryType>(`/api/entry-types/${id}`)
}

export async function createEntryType(payload: EntryTypeUpsertRequest): Promise<EntryType> {
  return apiClient.post<EntryType>('/api/entry-types', { body: payload })
}

export async function updateEntryType(id: string, payload: Partial<EntryTypeUpsertRequest>): Promise<EntryType> {
  return apiClient.put<EntryType>(`/api/entry-types/${id}`, { body: payload })
}

export async function deleteEntryType(id: string): Promise<void> {
  return apiClient.delete<void>(`/api/entry-types/${id}`)
}
