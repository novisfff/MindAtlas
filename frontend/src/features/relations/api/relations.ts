import { apiClient } from '@/lib/api/client'
import type { Relation, RelationType } from '@/types'

export interface RelationCreateRequest {
  sourceEntryId: string
  targetEntryId: string
  relationTypeId: string
  description?: string
}

export async function listRelationTypes(): Promise<RelationType[]> {
  return apiClient.get<RelationType[]>('/api/relation-types')
}

export async function getEntryRelations(entryId: string): Promise<Relation[]> {
  return apiClient.get<Relation[]>(`/api/relations/entry/${encodeURIComponent(entryId)}`)
}

export async function createRelation(payload: RelationCreateRequest): Promise<Relation> {
  return apiClient.post<Relation>('/api/relations', { body: payload })
}

export async function deleteRelation(id: string): Promise<void> {
  await apiClient.delete<void>(`/api/relations/${encodeURIComponent(id)}`)
}
