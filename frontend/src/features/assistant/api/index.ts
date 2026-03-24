import { apiClient } from '@/lib/api/client'
import { AssistantRun, Conversation, ConversationList, HumanApproval } from '../types'

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

export async function getActiveRun(conversationId: string): Promise<AssistantRun | null> {
  return apiClient.get<AssistantRun | null>(`/api/assistant/conversations/${conversationId}/runs/active`)
}

export async function stopRun(conversationId: string, runId: string): Promise<AssistantRun> {
  return apiClient.post<AssistantRun>(`/api/assistant/conversations/${conversationId}/runs/${runId}/stop`)
}

export function buildRunStreamUrl(conversationId: string, runId: string, afterSeq: number): string {
  const seq = Number.isFinite(afterSeq) ? Math.max(0, Math.floor(afterSeq)) : 0
  return `/api/assistant/conversations/${conversationId}/runs/${runId}/stream?afterSeq=${seq}`
}
