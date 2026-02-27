import { apiClient } from '@/lib/api/client'
import { Conversation, ConversationList, HumanApproval } from '../types'

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

export interface HumanApprovalDecisionRequest {
  decision: 'approved' | 'rejected'
  values: Record<string, unknown>
  comment?: string
}

export async function listPendingApprovals(conversationId: string): Promise<HumanApproval[]> {
  return apiClient.get<HumanApproval[]>(`/api/assistant/conversations/${conversationId}/approvals/pending`)
}

export async function submitApprovalDecision(
  conversationId: string,
  approvalId: string,
  payload: HumanApprovalDecisionRequest,
): Promise<HumanApproval> {
  return apiClient.post<HumanApproval>(
    `/api/assistant/conversations/${conversationId}/approvals/${approvalId}/decision`,
    { body: payload },
  )
}
