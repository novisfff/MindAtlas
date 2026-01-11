import { apiClient } from '@/lib/api/client'
import { Conversation, ConversationList } from '../types'

export async function getConversations(): Promise<ConversationList> {
  return apiClient.get<ConversationList>('/api/assistant/conversations')
}

export async function createConversation(title?: string): Promise<Conversation> {
  return apiClient.post<Conversation>('/api/assistant/conversations', {
    body: title ? { title } : undefined
  })
}

export async function getConversation(id: string): Promise<Conversation> {
  return apiClient.get<Conversation>(`/api/assistant/conversations/${id}`)
}

export async function deleteConversation(id: string): Promise<void> {
  return apiClient.delete(`/api/assistant/conversations/${id}`)
}
