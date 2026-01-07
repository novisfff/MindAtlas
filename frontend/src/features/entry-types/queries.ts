import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { listEntryTypes, createEntryType, updateEntryType, deleteEntryType } from './api/entryTypes'
import type { EntryTypeUpsertRequest } from './api/entryTypes'

export const entryTypesKeys = {
  all: ['entry-types'] as const,
  list: () => [...entryTypesKeys.all, 'list'] as const,
}

export function useEntryTypesQuery() {
  return useQuery({
    queryKey: entryTypesKeys.list(),
    queryFn: () => listEntryTypes(),
  })
}

export function useCreateEntryTypeMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: EntryTypeUpsertRequest) => createEntryType(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: entryTypesKeys.all }),
  })
}

export function useUpdateEntryTypeMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<EntryTypeUpsertRequest> }) =>
      updateEntryType(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: entryTypesKeys.all }),
  })
}

export function useDeleteEntryTypeMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteEntryType(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: entryTypesKeys.all }),
  })
}
