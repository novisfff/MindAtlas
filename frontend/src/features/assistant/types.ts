import type { HumanApprovalRecord } from '../shared/hitl'

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
  | 'waiting_approval'
  | 'cancelling'
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

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: ToolCall[]
  skillCalls?: SkillCall[]
  analysisSteps?: Analysis[]
  humanApprovals?: HumanApproval[]
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

export type HumanApproval = HumanApprovalRecord

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
