import { apiClient } from '@/lib/api/client'
import {
  AssistantRun,
  Conversation,
  ConversationList,
  DurableInterrupt,
  HumanApproval,
} from '../types'
import { normalizeDurableInterrupt } from '../interruptUtils'

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

// ---------------------------------------------------------------------------
// Durable Interrupt APIs (Plan 07 Task 8) — conversation-scoped only
// ---------------------------------------------------------------------------

export interface DurableInterruptTokenRequest {
  expectedRequestRevision: number
  expectedRunRevision: number
}

export interface DurableInterruptTokenResponse {
  token: string
  tokenRevision: number
}

export interface DurableInterruptResolveRequest {
  token: string
  resolutionRequestId: string
  expectedTokenRevision: number
  expectedRequestRevision: number
  expectedRunRevision: number
  outcome: 'approved' | 'rejected' | 'submitted' | 'cancelled'
  values?: Record<string, unknown>
  comment?: string
}

function normalizeInterruptList(raw: unknown): DurableInterrupt[] {
  if (!Array.isArray(raw)) {
    if (raw && typeof raw === 'object') {
      return [normalizeDurableInterrupt(raw as Record<string, unknown>)]
    }
    return []
  }
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item) => normalizeDurableInterrupt(item))
}

export async function listPendingInterrupts(
  conversationId: string,
  runId: string,
): Promise<DurableInterrupt[]> {
  const data = await apiClient.get<unknown>(
    `/api/assistant/conversations/${conversationId}/runs/${runId}/interrupts/pending`,
  )
  return normalizeInterruptList(data)
}

export async function getInterruptDetail(
  conversationId: string,
  runId: string,
  interruptId: string,
): Promise<DurableInterrupt> {
  const data = await apiClient.get<Record<string, unknown>>(
    `/api/assistant/conversations/${conversationId}/runs/${runId}/interrupts/${interruptId}`,
  )
  return normalizeDurableInterrupt(data)
}

export async function rotateInterruptToken(
  conversationId: string,
  runId: string,
  interruptId: string,
  payload: DurableInterruptTokenRequest,
): Promise<DurableInterruptTokenResponse> {
  return apiClient.post<DurableInterruptTokenResponse>(
    `/api/assistant/conversations/${conversationId}/runs/${runId}/interrupts/${interruptId}/token`,
    { body: payload },
  )
}

export async function resolveInterrupt(
  conversationId: string,
  runId: string,
  interruptId: string,
  payload: DurableInterruptResolveRequest,
): Promise<DurableInterrupt> {
  const data = await apiClient.post<Record<string, unknown>>(
    `/api/assistant/conversations/${conversationId}/runs/${runId}/interrupts/${interruptId}/resolve`,
    { body: payload },
  )
  return normalizeDurableInterrupt(data)
}
