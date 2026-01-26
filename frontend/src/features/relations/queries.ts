import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listRelationTypes,
  getEntryRelations,
  createRelation,
  deleteRelation,
  getRelationRecommendations,
  type RelationCreateRequest,
} from './api/relations'

export const relationKeys = {
  types: ['relation-types'] as const,
  byEntry: (entryId: string) => ['relations', 'entry', entryId] as const,
  recommendations: (entryId: string) => ['relations', 'recommendations', entryId] as const,
}

export function useRelationTypesQuery() {
  return useQuery({
    queryKey: relationKeys.types,
    queryFn: listRelationTypes,
  })
}

export function useEntryRelationsQuery(entryId: string) {
  return useQuery({
    queryKey: relationKeys.byEntry(entryId),
    queryFn: () => getEntryRelations(entryId),
    enabled: !!entryId,
  })
}

export function useCreateRelationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: RelationCreateRequest) => createRelation(payload),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: relationKeys.byEntry(variables.sourceEntryId) })
      queryClient.invalidateQueries({ queryKey: relationKeys.byEntry(variables.targetEntryId) })
    },
  })
}

export function useDeleteRelationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteRelation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['relations'] })
    },
  })
}

export function useRelationRecommendationsQuery(entryId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: relationKeys.recommendations(entryId),
    queryFn: () => getRelationRecommendations(entryId),
    staleTime: 1000 * 60 * 5, // 5 minutes cache
    enabled: !!entryId && (options?.enabled ?? true),
  })
}
