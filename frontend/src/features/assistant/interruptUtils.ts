import type { HumanApprovalFieldSchema, HumanApprovalFieldType, HumanApprovalFieldWidget } from '../shared/hitl'
import type { DurableInterrupt, DurableInterruptFieldRaw, DurableInterruptStatus } from './types'
import { isApiError } from '@/lib/api/client'

/** Widget-style field types returned by durable interrupt `fields` (render model). */
const WIDGET_TO_DATA_TYPE: Record<string, { type: HumanApprovalFieldType; widget: HumanApprovalFieldWidget }> = {
  input: { type: 'string', widget: 'input' },
  textarea: { type: 'string', widget: 'textarea' },
  select: { type: 'string', widget: 'select' },
  radio: { type: 'string', widget: 'radio' },
  switch: { type: 'boolean', widget: 'switch' },
  checkbox_group: { type: 'array', widget: 'checkbox_group' },
  tag_selector: { type: 'array', widget: 'tag_selector' },
  date: { type: 'string', widget: 'date' },
  time: { type: 'string', widget: 'time' },
}

/**
 * Map backend durable render fields onto shared HITL field schema.
 * Backend uses widget names in `type`; shared form expects data type + widget.
 */
export function mapDurableFieldsToSchema(
  fields: DurableInterruptFieldRaw[] | HumanApprovalFieldSchema[] | null | undefined,
): HumanApprovalFieldSchema[] {
  if (!Array.isArray(fields)) return []
  return fields.map((raw) => {
    const item = raw as DurableInterruptFieldRaw & Partial<HumanApprovalFieldSchema>
    // Already in shared shape (has data type + optional widget).
    if (
      item.type === 'string'
      || item.type === 'number'
      || item.type === 'integer'
      || item.type === 'boolean'
      || item.type === 'array'
    ) {
      return {
        name: String(item.name),
        label: item.label,
        type: item.type,
        widget: item.widget,
        options: item.options,
        allowCustom: item.allowCustom,
        placeholder: item.placeholder,
        required: item.required,
      }
    }
    const mapped = WIDGET_TO_DATA_TYPE[String(item.type || 'input')] ?? WIDGET_TO_DATA_TYPE.input
    return {
      name: String(item.name),
      label: item.label,
      type: mapped.type,
      widget: mapped.widget,
      options: item.options,
      allowCustom: item.allowCustom,
      placeholder: item.placeholder,
      required: item.required,
    }
  })
}

export function normalizeDurableInterrupt(raw: Record<string, unknown>): DurableInterrupt {
  const fields = mapDurableFieldsToSchema(
    (raw.fields as DurableInterruptFieldRaw[] | undefined) ?? [],
  )
  const status = String(raw.status || 'pending') as DurableInterruptStatus
  const kind = String(raw.kind || 'approval') === 'input' ? 'input' : 'approval'
  const resolutionRequestId = raw.resolutionRequestId != null
    ? String(raw.resolutionRequestId)
    : undefined

  return {
    source: 'durable',
    interruptId: String(raw.interruptId ?? raw.id ?? ''),
    runId: String(raw.runId ?? ''),
    conversationId: String(raw.conversationId ?? ''),
    messageId: raw.messageId != null ? String(raw.messageId) : null,
    status,
    kind,
    requestRevision: Number(raw.requestRevision ?? 1),
    runRevision: Number(raw.runRevision ?? 0),
    tokenRevision: Number(raw.tokenRevision ?? 0),
    expiresAt: raw.expiresAt != null ? String(raw.expiresAt) : null,
    allowedActions: Array.isArray(raw.allowedActions)
      ? raw.allowedActions.map((item) => String(item))
      : [],
    fields,
    requestPayload: (raw.requestPayload && typeof raw.requestPayload === 'object'
      ? raw.requestPayload as Record<string, unknown>
      : {}),
    initialValues: (raw.initialValues && typeof raw.initialValues === 'object'
      ? raw.initialValues as Record<string, unknown>
      : {}),
    nodeId: String(raw.nodeId ?? ''),
    nodeVisitId: String(raw.nodeVisitId ?? ''),
    resolvedAt: raw.resolvedAt != null ? String(raw.resolvedAt) : null,
    ...(resolutionRequestId ? { resolutionRequestId } : {}),
  }
}

export function isDurableInterruptPending(status: string | null | undefined): boolean {
  return String(status || '') === 'pending'
}

export function isDurableInterruptTerminal(status: string | null | undefined): boolean {
  const s = String(status || '')
  return s === 'approved'
    || s === 'rejected'
    || s === 'submitted'
    || s === 'cancelled'
    || s === 'expired'
}

/** Extract public reason code from API errors (details.reasonCode). */
export function extractInterruptReasonCode(error: unknown): string | null {
  if (!isApiError(error)) return null
  const details = error.details
  if (!details || typeof details !== 'object') return null
  const record = details as Record<string, unknown>
  if (typeof record.reasonCode === 'string' && record.reasonCode) return record.reasonCode
  if (typeof record.reason_code === 'string' && record.reason_code) return record.reason_code
  return null
}

export function createResolutionRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // Fallback for older environments; still unique enough for client correlation.
  return `rr-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`
}

/**
 * After a lost/failed resolve POST, compare retained click resolutionRequestId
 * with GET terminal state.
 * - same ID → this click won (treat as success)
 * - different ID → another tab/action won
 * - still pending → may retry same click id
 */
export type LostResponseRecovery =
  | { kind: 'won'; interrupt: DurableInterrupt }
  | { kind: 'lost_to_other'; interrupt: DurableInterrupt }
  | { kind: 'still_pending'; interrupt: DurableInterrupt }
  | { kind: 'unknown' }

export function recoverFromLostResolveResponse(params: {
  retainedResolutionRequestId: string
  current: DurableInterrupt
}): LostResponseRecovery {
  const { retainedResolutionRequestId, current } = params
  if (isDurableInterruptPending(current.status)) {
    return { kind: 'still_pending', interrupt: current }
  }
  if (
    current.resolutionRequestId
    && current.resolutionRequestId === retainedResolutionRequestId
  ) {
    return { kind: 'won', interrupt: current }
  }
  if (current.resolutionRequestId) {
    return { kind: 'lost_to_other', interrupt: current }
  }
  // Terminal without public resolutionRequestId (e.g. expired scanner path edge).
  return { kind: 'lost_to_other', interrupt: current }
}

/** Map durable terminal status onto shared badge statuses where possible. */
export function durableStatusForBadge(
  status: DurableInterruptStatus,
): 'pending' | 'approved' | 'rejected' | 'cancelled' | 'submitted' | 'expired' {
  return status
}
