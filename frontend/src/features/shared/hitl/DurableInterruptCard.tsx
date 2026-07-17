import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  buildEditableFieldValues,
  collectFieldValues,
  type FieldCollectErrorCode,
} from './fieldHelpers'
import { HumanApprovalActionBar } from './HumanApprovalActionBar'
import { HumanApprovalFieldForm } from './HumanApprovalFieldForm'
import { HumanApprovalStatusBadge, type HitlStatusBadgeValue } from './HumanApprovalStatusBadge'
import type { HumanApprovalFieldSchema } from './types'

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

function defaultCreateResolutionRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `rr-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`
}

function statusForBadge(status: DurableInterruptCardStatus): HitlStatusBadgeValue {
  return status
}

function fieldErrorMessageKey(code: FieldCollectErrorCode): string {
  if (code === 'required') return 'settings.skills.humanApproval.validationRequired'
  if (code === 'select') return 'settings.skills.humanApproval.validationSelectOption'
  if (code === 'radio') return 'settings.skills.humanApproval.validationRadioOption'
  if (code === 'checkbox_group') return 'settings.skills.humanApproval.validationCheckboxOption'
  if (code === 'tag') return 'settings.skills.humanApproval.validationTagOption'
  if (code === 'date') return 'settings.skills.humanApproval.validationDateFormat'
  if (code === 'time') return 'settings.skills.humanApproval.validationTimeFormat'
  return 'settings.skills.humanApproval.validationInvalidType'
}

/**
 * Thin durable Interrupt card: composes shared HITL field/action/status pieces.
 * Does not store raw tokens; parent rotates token at action time.
 * Retains one resolutionRequestId per click for lost-response recovery of that outcome.
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
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    buildEditableFieldValues(interrupt.fields, interrupt.initialValues),
  )
  const [comment, setComment] = useState('')
  // Per-click request id keyed by outcome: same-outcome retry reuses; different action mints new.
  const resolutionRequestRef = useRef<{ outcome: DurableInterruptOutcome; id: string } | null>(null)

  // Reset form only when interrupt identity / request / terminal status change.
  // Do NOT depend on tokenRevision — mid-submit token rotate must not wipe user edits.
  useEffect(() => {
    setValues(buildEditableFieldValues(interrupt.fields, interrupt.initialValues))
    setComment('')
    resolutionRequestRef.current = null
    // requestRevision covers request payload/fields/initial value changes from the server.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally omit tokenRevision and object identity of fields/initialValues
  }, [interrupt.interruptId, interrupt.status, interrupt.requestRevision])

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

  const handleAction = async (outcome: DurableInterruptOutcome) => {
    if (actionsDisabled || !onSubmit) return

    const collected = collectFieldValues(interrupt.fields, values)
    if (!collected.ok) {
      toast.error(t(fieldErrorMessageKey(collected.code), {
        field: collected.field.label || collected.field.name,
      }))
      return
    }

    const normalizedComment = comment.trim()
    if (outcome === 'rejected' && requireRejectComment && !normalizedComment) {
      toast.error(t('settings.skills.humanApproval.validationRejectCommentRequired'))
      return
    }

    // Same-outcome retry reuses the retained id; a different action mints a new one.
    if (!resolutionRequestRef.current || resolutionRequestRef.current.outcome !== outcome) {
      resolutionRequestRef.current = {
        outcome,
        id: createResolutionRequestId(),
      }
    }
    const resolutionRequestId = resolutionRequestRef.current.id

    await onSubmit({
      outcome,
      values: collected.values,
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
