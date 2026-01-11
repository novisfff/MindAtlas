import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getConversations,
  getConversation,
  createConversation,
  deleteConversation,
} from './api'

export const assistantKeys = {
  all: ['assistant'] as const,
  conversations: () => [...assistantKeys.all, 'conversations'] as const,
}

export function useConversationsQuery() {
  return useQuery({
    queryKey: assistantKeys.conversations(),
    queryFn: getConversations,
  })
}

export function useConversationQuery(id: string | null) {
  return useQuery({
    queryKey: [...assistantKeys.conversations(), id],
    queryFn: () => getConversation(id!),
    enabled: !!id,
  })
}

export function useCreateConversationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
    },
  })
}

export function useDeleteConversationMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
    },
  })
}
