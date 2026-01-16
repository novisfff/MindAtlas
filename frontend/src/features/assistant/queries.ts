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

export function useConversationQuery(id: string | null, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: [...assistantKeys.conversations(), id],
    queryFn: () => getConversation(id!),
    enabled: !!id && (options?.enabled !== false),
    staleTime: 30000, // 30秒内不重新获取，避免流式输出期间重复加载
    refetchOnWindowFocus: false, // 禁用窗口聚焦时重新获取
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
