import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createTag,
  deleteTag,
  listTags,
  updateTag,
  type TagUpsertRequest,
} from './api/tags'

export const tagsKeys = {
  all: ['tags'] as const,
  list: () => [...tagsKeys.all, 'list'] as const,
}

export function useTagsQuery() {
  return useQuery({
    queryKey: tagsKeys.list(),
    queryFn: listTags,
  })
}

export function useCreateTagMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createTag,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: tagsKeys.list() })
    },
  })
}

export function useUpdateTagMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: TagUpsertRequest }) =>
      updateTag(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: tagsKeys.list() })
    },
  })
}

export function useDeleteTagMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteTag,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: tagsKeys.list() })
    },
  })
}
