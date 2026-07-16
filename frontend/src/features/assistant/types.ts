import type {
  HumanApprovalFieldSchema,
  HumanApprovalRecord,
} from '../shared/hitl'

export interface ToolCall {
  id: string
  name: string
  args: Record<string, unknown>
  result?: string
  status: 'pending' | 'running' | 'completed' | 'error'
  hidden?: boolean
  nodeId?: string
  nodeType?: string
  nodeExecutionId?: string
  agentRound?: number
  toolCallIndex?: number
  toolKind?: 'tool' | 'knowledge'
  startedAt?: string
  endedAt?: string
  durationMs?: number
}

export interface SkillCall {
  id: string
  name: string
  status: 'running' | 'completed' | 'error'
  hidden?: boolean
}

export interface Analysis {
  id: string
  content: string
  status: 'running' | 'completed'
}

export interface WorkflowStep {
  nodeId: string
  nodeType: string
  nodeLabel: string
}

export type AssistantRunStatus =
  | 'queued'
  | 'running'
  | 'recovering'
  | 'waiting_approval'
  | 'waiting_input'
  | 'cancelling'
  | 'needs_reconciliation'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AssistantRun {
  runId: string
  conversationId: string
  messageId?: string | null
  status: AssistantRunStatus | string
  lastEventSeq: number
  checkpointSeq: number
  cancelRequestedAt?: string | null
  startedAt?: string | null
  endedAt?: string | null
}

/** Legacy human approval (Plan 03/04 path). Explicit source discriminator. */
export type HumanApproval = HumanApprovalRecord & {
  source?: 'legacy'
}

/** Durable Interrupt terminal/pending statuses (Plan 07). */
export type DurableInterruptStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'submitted'
  | 'cancelled'
  | 'expired'

export type DurableInterruptKind = 'approval' | 'input'

/**
 * Backend render field shape (`render_interrupt_fields`).
 * `type` here is the widget name (input/select/switch/...), not the data type.
 */
export interface DurableInterruptFieldRaw {
  name: string
  type: string
  label?: string
  required?: boolean
  description?: string
  options?: Array<string | { value: string; label: string; description?: string }>
  allowCustom?: boolean
  placeholder?: string
}

/**
 * Safe public durable Interrupt state.
 * Terminal GET may include resolutionRequestId; never digests/tokens/values/comments.
 */
export interface DurableInterrupt {
  source: 'durable'
  interruptId: string
  runId: string
  conversationId: string
  messageId: string | null
  status: DurableInterruptStatus
  kind: DurableInterruptKind
  requestRevision: number
  runRevision: number
  tokenRevision: number
  expiresAt: string | null
  allowedActions: string[]
  /** Shared HITL field schema (mapped from backend render fields). */
  fields: HumanApprovalFieldSchema[]
  requestPayload: Record<string, unknown>
  initialValues: Record<string, unknown>
  nodeId: string
  nodeVisitId: string
  resolvedAt: string | null
  /** Present only on terminal public state when available. */
  resolutionRequestId?: string
}

/** Discriminated HITL attachment on assistant messages. */
export type AssistantHitlItem =
  | ({ source: 'legacy' } & HumanApprovalRecord)
  | DurableInterrupt

export function isDurableInterrupt(item: AssistantHitlItem | HumanApproval | DurableInterrupt): item is DurableInterrupt {
  return (item as DurableInterrupt).source === 'durable'
}

export function isLegacyHumanApproval(
  item: AssistantHitlItem | HumanApproval | DurableInterrupt,
): item is HumanApprovalRecord & { source?: 'legacy' } {
  return (item as DurableInterrupt).source !== 'durable'
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: ToolCall[]
  skillCalls?: SkillCall[]
  analysisSteps?: Analysis[]
  /** Legacy HumanApprovalRecord list — preserved for existing consumers. */
  humanApprovals?: HumanApproval[]
  /** Durable Interrupt cards attached to this assistant message. */
  durableInterrupts?: DurableInterrupt[]
  toolResults?: {
    id: string
    status: string
    result: string
    nodeId?: string
    nodeType?: string
    nodeExecutionId?: string
    agentRound?: number
    toolCallIndex?: number
    toolKind?: 'tool' | 'knowledge'
    startedAt?: string
    endedAt?: string
    durationMs?: number
  }[]
  createdAt: string
  updatedAt: string
}

export interface Conversation {
  id: string
  title?: string
  summary?: string
  isArchived: boolean
  lastMessageAt?: string
  createdAt: string
  updatedAt: string
  messages?: Message[]
}

export interface ConversationList {
  items: Conversation[]
  total: number
}
