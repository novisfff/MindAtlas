import { apiClient } from '@/lib/api/client'
import type { Entry, Page } from '@/types'

export interface ListEntriesParams {
  q?: string
  typeId?: string
  tagIds?: string[]
  timeFrom?: string
  timeTo?: string
  page?: number
  size?: number
}

export type EntryTimeMode = Entry['timeMode']

export interface EntryUpsertRequest {
  title: string
  summary?: string
  content?: string
  typeId: string
  timeMode: EntryTimeMode
  timeAt?: string
  timeFrom?: string
  timeTo?: string
  tagIds?: string[]
}

export async function listEntries(params: ListEntriesParams = {}): Promise<Page<Entry>> {
  const query: Record<string, string | number | undefined> = {}
  if (params.q) query.q = params.q
  if (params.typeId) query.typeId = params.typeId
  if (params.tagIds?.length) query.tagIds = params.tagIds.join(',')
  // Convert date to ISO-8601 Instant format with Z suffix
  if (params.timeFrom) query.timeFrom = `${params.timeFrom}T00:00:00Z`
  if (params.timeTo) query.timeTo = `${params.timeTo}T23:59:59Z`
  if (params.page) query.page = params.page - 1  // Convert 1-indexed to 0-indexed for backend
  if (params.size) query.size = params.size

  return apiClient.get<Page<Entry>>('/api/entries', { query: query as Record<string, string> })
}

export async function getEntry(id: string): Promise<Entry> {
  return apiClient.get<Entry>(`/api/entries/${encodeURIComponent(id)}`)
}

export async function createEntry(payload: EntryUpsertRequest): Promise<Entry> {
  return apiClient.post<Entry>('/api/entries', { body: payload })
}

export async function updateEntry(id: string, payload: EntryUpsertRequest): Promise<Entry> {
  return apiClient.put<Entry>(`/api/entries/${encodeURIComponent(id)}`, { body: payload })
}

export async function deleteEntry(id: string): Promise<void> {
  await apiClient.delete<void>(`/api/entries/${encodeURIComponent(id)}`)
}

export interface IndexStatus {
  status: 'pending' | 'processing' | 'succeeded' | 'dead' | 'unknown'
  attempts: number
  lastError: string | null
  updatedAt: string | null
}

export async function getEntryIndexStatus(id: string): Promise<IndexStatus> {
  return apiClient.get<IndexStatus>(`/api/entries/${encodeURIComponent(id)}/index-status`)
}
