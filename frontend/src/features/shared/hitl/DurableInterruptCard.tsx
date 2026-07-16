import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { HumanApprovalActionBar } from './HumanApprovalActionBar'
import { HumanApprovalFieldForm } from './HumanApprovalFieldForm'
import { HumanApprovalStatusBadge, type HitlStatusBadgeValue } from './HumanApprovalStatusBadge'
import type {
  HumanApprovalFieldSchema,
  HumanApprovalFieldWidget,
  HumanApprovalOption,
} from './types'

export type DurableInterruptCardStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'submitted'
  | 'cancelled'
  | 'expired'

export type DurableInterruptCardKind = 'approval' | 'input'

export interface DurableInterruptCardModel {
  interruptId: string
  status: DurableInterruptCardStatus
  kind: DurableInterruptCardKind
  fields: HumanApprovalFieldSchema[]
  requestPayload: Record<string, unknown>
  initialValues: Record<string, unknown>
  nodeId: string
  expiresAt: string | null
  resolvedAt: string | null
  requestRevision: number
  runRevision: number
  tokenRevision: number
}

export type DurableInterruptOutcome = 'approved' | 'rejected' | 'submitted' | 'cancelled'

export interface DurableInterruptSubmitPayload {
  outcome: DurableInterruptOutcome
  values: Record<string, unknown>
  comment?: string
  /** Stable per-click id retained for lost-response retry. */
  resolutionRequestId: string
}

export interface DurableInterruptCardProps {
  interrupt: DurableInterruptCardModel
  submitting?: boolean
  conflictMessage?: string | null
  className?: string
  /**
   * Called at action time. Parent should rotate token (in memory only), resolve,
   * and recover from lost POST by comparing resolutionRequestId with GET terminal state.
   */
  onSubmit?: (payload: DurableInterruptSubmitPayload) => Promise<void> | void
  /** Optional: generate resolution request ids (defaults to crypto.randomUUID). */
  createResolutionRequestId?: () => string
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/

function defaultCreateResolutionRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `rr-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`
}

function normalizeDateText(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''
  const normalized = text.replace(/[/.]/g, '-')
  if (!DATE_RE.test(normalized)) return ''
  const [yearText, monthText, dayText] = normalized.split('-')
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return ''
  const check = new Date(Date.UTC(year, month - 1, day))
  if (
    check.getUTCFullYear() !== year
    || check.getUTCMonth() !== month - 1
    || check.getUTCDate() !== day
  ) {
    return ''
  }
  return `${yearText.padStart(4, '0')}-${monthText.padStart(2, '0')}-${dayText.padStart(2, '0')}`
}

function normalizeTimeText(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''
  const short = text.slice(0, 5)
  return TIME_RE.test(short) ? short : ''
}

function defaultWidgetForType(type: HumanApprovalFieldSchema['type']): HumanApprovalFieldWidget {
  if (type === 'boolean') return 'switch'
  if (type === 'array') return 'tag_selector'
  return 'input'
}

function normalizeWidget(field: HumanApprovalFieldSchema): HumanApprovalFieldWidget {
  const raw = String(field.widget ?? '').trim().toLowerCase() as HumanApprovalFieldWidget
  if (raw) return raw
  return defaultWidgetForType(field.type)
}

type NormalizedOption = {
  value: string
  label: string
  description?: string
}

function normalizeOptions(field: HumanApprovalFieldSchema): NormalizedOption[] {
  if (!Array.isArray(field.options)) return []
  const deduped: NormalizedOption[] = []
  const seen = new Set<string>()
  field.options.forEach((item: HumanApprovalOption) => {
    let normalized: NormalizedOption | null = null
    if (typeof item === 'string') {
      const text = item.trim()
      if (text) normalized = { value: text, label: text }
    } else if (item && typeof item === 'object') {
      const value = String(item.value ?? '').trim()
      const label = String(item.label ?? value).trim()
      const description = String(item.description ?? '').trim()
      if (value && label) {
        normalized = {
          value,
          label,
          ...(description ? { description } : {}),
        }
      }
    }
    if (!normalized || seen.has(normalized.value)) return
    seen.add(normalized.value)
    deduped.push(normalized)
  })
  return deduped
}

function toStringArray(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.map((item) => String(item ?? '').trim()).filter(Boolean)
  }
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return []
    return text.split(',').map((item) => item.trim()).filter(Boolean)
  }
  return []
}

function normalizeInitialValue(field: HumanApprovalFieldSchema, raw: unknown): unknown {
  const widget = normalizeWidget(field)
  if (widget === 'tag_selector' || field.type === 'array') return toStringArray(raw)
  if (widget === 'switch' || field.type === 'boolean') {
    if (typeof raw === 'boolean') return raw
    return String(raw ?? '').trim().toLowerCase() === 'true'
  }
  if (widget === 'date') return normalizeDateText(raw)
  if (widget === 'time') return normalizeTimeText(raw)
  if (raw === null || raw === undefined) return ''
  return typeof raw === 'string' ? raw : String(raw)
}

function buildEditableValues(interrupt: DurableInterruptCardModel): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  interrupt.fields.forEach((field) => {
    values[field.name] = normalizeInitialValue(field, interrupt.initialValues[field.name])
  })
  return values
}

function isEmptyValue(field: HumanApprovalFieldSchema, raw: unknown): boolean {
  if (raw === null || raw === undefined) return true
  if (field.type === 'array' || normalizeWidget(field) === 'tag_selector') {
    return toStringArray(raw).length === 0
  }
  if (field.type === 'boolean' || normalizeWidget(field) === 'switch') return false
  return String(raw).trim() === ''
}

function coerceFieldValue(field: HumanApprovalFieldSchema, raw: unknown): unknown {
  const widget = normalizeWidget(field)
  const options = normalizeOptions(field)
  const optionValues = options.map((item) => item.value)

  if (field.type === 'string') {
    const value = typeof raw === 'string' ? raw : String(raw ?? '')
    if (widget === 'date') {
      const rawText = value.trim()
      if (!rawText) return ''
      const normalized = normalizeDateText(rawText)
      if (!normalized) throw new Error('date')
      return normalized
    }
    if (widget === 'time') {
      const rawText = value.trim()
      if (!rawText) return ''
      const normalized = normalizeTimeText(rawText)
      if (!normalized) throw new Error('time')
      return normalized
    }
    if (widget === 'select' || widget === 'radio') {
      const normalized = value.trim()
      if (!optionValues.includes(normalized)) throw new Error(widget)
      return normalized
    }
    return value
  }

  if (field.type === 'array') {
    const values = toStringArray(raw)
    if (widget === 'checkbox_group') {
      const invalid = values.filter((item) => !optionValues.includes(item))
      if (invalid.length > 0) throw new Error('checkbox_group')
      return Array.from(new Set(values))
    }
    if (widget === 'tag_selector') {
      const allowCustom = field.allowCustom ?? true
      if (!allowCustom && optionValues.length > 0) {
        const unknown = values.filter((item) => !optionValues.includes(item))
        if (unknown.length > 0) throw new Error('tag')
      }
    }
    return values
  }

  const text = String(raw ?? '').trim()
  if (field.type === 'boolean') {
    if (typeof raw === 'boolean') return raw
    if (text === 'true') return true
    if (text === 'false') return false
    throw new Error('boolean')
  }
  if (field.type === 'integer') {
    const parsed = Number(text)
    if (!Number.isInteger(parsed)) throw new Error('integer')
    return parsed
  }
  if (field.type === 'number') {
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) throw new Error('number')
    return parsed
  }
  return raw ?? ''
}

function statusForBadge(status: DurableInterruptCardStatus): HitlStatusBadgeValue {
  return status
}

/**
 * Thin durable Interrupt card: composes shared HITL field/action/status pieces.
 * Does not store raw tokens; parent rotates token at action time.
 * Retains one resolutionRequestId per click for lost-response recovery.
 */
export function DurableInterruptCard({
  interrupt,
  submitting = false,
  conflictMessage = null,
  className,
  onSubmit,
  createResolutionRequestId = defaultCreateResolutionRequestId,
}: DurableInterruptCardProps) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, unknown>>(() => buildEditableValues(interrupt))
  const [comment, setComment] = useState('')
  // Retained per-click request id for network retry of the same click.
  const resolutionRequestIdRef = useRef<string | null>(null)

  useEffect(() => {
    setValues(buildEditableValues(interrupt))
    // Clear local comment/request id when interrupt identity or terminal status changes.
    setComment('')
    if (interrupt.status !== 'pending') {
      resolutionRequestIdRef.current = null
    }
  }, [interrupt.interruptId, interrupt.status, interrupt.requestRevision, interrupt.tokenRevision])

  const title = String(interrupt.requestPayload.title ?? '').trim() || interrupt.nodeId
  const instruction = String(interrupt.requestPayload.instruction ?? '').trim()
  const approveLabel = String(
    interrupt.requestPayload.approveLabel
      ?? interrupt.requestPayload.approve_label
      ?? '',
  ).trim()
  const rejectLabel = String(
    interrupt.requestPayload.rejectLabel
      ?? interrupt.requestPayload.reject_label
      ?? '',
  ).trim()
  const submitLabel = String(
    interrupt.requestPayload.submitLabel
      ?? interrupt.requestPayload.submit_label
      ?? '',
  ).trim()
  const requireRejectComment = Boolean(
    interrupt.requestPayload.requireRejectComment
      ?? interrupt.requestPayload.require_reject_comment
      ?? true,
  )
  const pending = interrupt.status === 'pending'
  const expiredByClock = Boolean(
    pending
    && interrupt.expiresAt
    && !Number.isNaN(Date.parse(interrupt.expiresAt))
    && Date.parse(interrupt.expiresAt) <= Date.now(),
  )
  const actionsDisabled = !pending || submitting || expiredByClock

  const subtitle = useMemo(() => {
    if (pending) {
      return interrupt.expiresAt ? new Date(interrupt.expiresAt).toLocaleString() : ''
    }
    return interrupt.resolvedAt ? new Date(interrupt.resolvedAt).toLocaleString() : ''
  }, [interrupt.expiresAt, interrupt.resolvedAt, pending])

  const collectValues = (): Record<string, unknown> | null => {
    const payloadValues: Record<string, unknown> = {}
    for (const field of interrupt.fields) {
      const raw = values[field.name]
      const widget = normalizeWidget(field)
      if (isEmptyValue(field, raw)) {
        if (field.required) {
          toast.error(t('settings.skills.humanApproval.validationRequired', { field: field.label || field.name }))
          return null
        }
        if (widget === 'date' || widget === 'time') {
          payloadValues[field.name] = ''
        }
        continue
      }
      try {
        payloadValues[field.name] = coerceFieldValue(field, raw)
      } catch (error) {
        const code = error instanceof Error ? error.message : ''
        if (code === 'select') {
          toast.error(t('settings.skills.humanApproval.validationSelectOption', { field: field.label || field.name }))
          return null
        }
        if (code === 'radio') {
          toast.error(t('settings.skills.humanApproval.validationRadioOption', { field: field.label || field.name }))
          return null
        }
        if (code === 'checkbox_group') {
          toast.error(t('settings.skills.humanApproval.validationCheckboxOption', { field: field.label || field.name }))
          return null
        }
        if (code === 'tag') {
          toast.error(t('settings.skills.humanApproval.validationTagOption', { field: field.label || field.name }))
          return null
        }
        if (code === 'date') {
          toast.error(t('settings.skills.humanApproval.validationDateFormat', { field: field.label || field.name }))
          return null
        }
        if (code === 'time') {
          toast.error(t('settings.skills.humanApproval.validationTimeFormat', { field: field.label || field.name }))
          return null
        }
        toast.error(t('settings.skills.humanApproval.validationInvalidType', { field: field.label || field.name }))
        return null
      }
    }
    return payloadValues
  }

  const handleAction = async (outcome: DurableInterruptOutcome) => {
    if (actionsDisabled || !onSubmit) return
    const payloadValues = collectValues()
    if (!payloadValues) return

    const normalizedComment = comment.trim()
    if (outcome === 'rejected' && requireRejectComment && !normalizedComment) {
      toast.error(t('settings.skills.humanApproval.validationRejectCommentRequired'))
      return
    }

    // One resolutionRequestId per click; retries of the same click reuse it.
    if (!resolutionRequestIdRef.current) {
      resolutionRequestIdRef.current = createResolutionRequestId()
    }
    const resolutionRequestId = resolutionRequestIdRef.current

    await onSubmit({
      outcome,
      values: payloadValues,
      comment: normalizedComment || undefined,
      resolutionRequestId,
    })
  }

  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white p-3 shadow-sm ${className ?? ''}`}
      data-testid="durable-interrupt-card"
      data-interrupt-id={interrupt.interruptId}
      data-interrupt-status={interrupt.status}
      data-interrupt-kind={interrupt.kind}
    >
      <div className="mb-2.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800">{title}</div>
          <div className="mt-0.5 text-[11px] text-slate-500">
            {pending
              ? t('settings.skills.humanApproval.pendingAt', 'Pending')
              : t('settings.skills.humanApproval.resolvedAt', 'Resolved')}
            {subtitle ? `: ${subtitle}` : ''}
            {pending && interrupt.expiresAt ? (
              <span className="ml-1 text-slate-400">
                ({t('settings.skills.humanApproval.expiresAt', 'expires')} {new Date(interrupt.expiresAt).toLocaleString()})
              </span>
            ) : null}
          </div>
        </div>
        <HumanApprovalStatusBadge status={statusForBadge(interrupt.status)} />
      </div>

      {instruction && (
        <p className="mb-2.5 rounded-md border border-slate-100 bg-slate-50 px-2 py-1.5 text-xs text-slate-600">
          {instruction}
        </p>
      )}

      <HumanApprovalFieldForm
        fields={interrupt.fields}
        values={values}
        disabled={actionsDisabled}
        onChange={(name, value) => {
          setValues((prev) => ({ ...prev, [name]: value }))
        }}
      />

      {conflictMessage ? (
        <div
          className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-800"
          data-testid="durable-interrupt-conflict"
        >
          {conflictMessage}
        </div>
      ) : null}

      {pending && !expiredByClock ? (
        interrupt.kind === 'input' ? (
          <div className="mt-3 space-y-2.5 border-t border-slate-200 pt-3">
            <div className="space-y-1">
              <label className="text-[11px] font-medium text-slate-700">
                {t('settings.skills.humanApproval.comment', 'Comment')}
              </label>
              <input
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                disabled={submitting}
                className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
                placeholder={t('settings.skills.humanApproval.commentPlaceholder', 'Optional comment')}
              />
            </div>
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                disabled={submitting}
                onClick={() => void handleAction('submitted')}
                className="rounded-md border border-emerald-200 bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
                data-testid="durable-interrupt-submit"
              >
                {submitLabel || t('settings.skills.humanApproval.submit', 'Submit')}
              </button>
            </div>
          </div>
        ) : (
          <HumanApprovalActionBar
            approveLabel={approveLabel}
            rejectLabel={rejectLabel}
            requireRejectComment={requireRejectComment}
            comment={comment}
            submitting={submitting}
            onCommentChange={setComment}
            onApprove={() => void handleAction('approved')}
            onReject={() => void handleAction('rejected')}
          />
        )
      ) : (
        <div className="mt-3 border-t border-slate-200 pt-2 text-xs text-slate-600">
          {expiredByClock
            ? t('settings.skills.humanApproval.status.expired', 'expired')
            : t('settings.skills.humanApproval.noComment', 'No comment')}
        </div>
      )}
    </div>
  )
}
