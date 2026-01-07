import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import type { Entry, Page } from '@/types'
import {
  createEntry,
  deleteEntry,
  getEntry,
  listEntries,
  updateEntry,
  type EntryUpsertRequest,
  type ListEntriesParams,
} from './api/entries'

export const entriesKeys = {
  all: ['entries'] as const,
  list: (params?: ListEntriesParams) => [...entriesKeys.all, 'list', params] as const,
  detail: (id: string) => [...entriesKeys.all, 'detail', id] as const,
}

export function useEntriesQuery(params: ListEntriesParams = {}) {
  return useQuery({
    queryKey: entriesKeys.list(params),
    queryFn: () => listEntries(params),
    placeholderData: keepPreviousData,
  })
}

export function useEntryQuery(id?: string) {
  return useQuery({
    queryKey: id ? entriesKeys.detail(id) : entriesKeys.detail('__missing__'),
    queryFn: () => getEntry(id as string),
    enabled: Boolean(id),
  })
}

export function useCreateEntryMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: EntryUpsertRequest) => createEntry(payload),
    onSuccess: (entry) => {
      queryClient.setQueryData(entriesKeys.detail(entry.id), entry)
      queryClient.invalidateQueries({ queryKey: entriesKeys.list() })
    },
  })
}

export interface UpdateEntryVariables {
  id: string
  payload: EntryUpsertRequest
}

export function useUpdateEntryMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, payload }: UpdateEntryVariables) => updateEntry(id, payload),
    onSuccess: (entry) => {
      queryClient.setQueryData(entriesKeys.detail(entry.id), entry)
      queryClient.invalidateQueries({ queryKey: entriesKeys.list() })
    },
  })
}

export function useDeleteEntryMutation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => deleteEntry(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: entriesKeys.list() })
    },
  })
}
