import { apiClient } from '@/lib/api/client'
import type { Relation, RelationType } from '@/types'

export interface RelationCreateRequest {
  sourceEntryId: string
  targetEntryId: string
  relationTypeId: string
  description?: string
}

export interface RecommendationItem {
  targetEntryId: string
  relationType?: string
  score: number
}

export interface RecommendationResponse {
  items: RecommendationItem[]
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

export async function getRelationRecommendations(
  entryId: string
): Promise<RecommendationItem[]> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 65000)

  try {
    const data = await apiClient.get<RecommendationResponse>(
      `/api/lightrag/entries/${encodeURIComponent(entryId)}/relation-recommendations?exclude_existing_relations=true`,
      { signal: controller.signal }
    )
    return data.items
  } finally {
    clearTimeout(timeoutId)
  }
}
