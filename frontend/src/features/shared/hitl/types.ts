export type HumanApprovalStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'
export type HumanApprovalFieldType = 'string' | 'number' | 'integer' | 'boolean' | 'array'
export type HumanApprovalFieldWidget =
  | 'input'
  | 'textarea'
  | 'switch'
  | 'select'
  | 'radio'
  | 'tag_selector'
  | 'date'
  | 'time'

export interface HumanApprovalFieldSchema {
  name: string
  label?: string
  type: HumanApprovalFieldType
  widget?: HumanApprovalFieldWidget
  options?: string[]
  allowCustom?: boolean
  placeholder?: string
  required?: boolean
}

export interface HumanApprovalRecord {
  id: string
  runId: string
  channelType: string
  conversationId: string | null
  messageId: string | null
  workflowId: string | null
  skillId: string | null
  nodeId: string
  nodeLabel: string | null
  status: HumanApprovalStatus
  requestPayload: Record<string, unknown>
  fieldSchema: HumanApprovalFieldSchema[]
  initialValues: Record<string, unknown>
  submittedValues: Record<string, unknown>
  decision: 'approved' | 'rejected' | null
  comment: string | null
  resolvedAt: string | null
  createdAt: string | null
  updatedAt: string | null
}

export interface HumanApprovalDecisionPayload {
  decision: 'approved' | 'rejected'
  values: Record<string, unknown>
  comment?: string
}
