import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  buildEditableFieldValues,
  collectFieldValues,
  type FieldCollectErrorCode,
} from './fieldHelpers'
import { HumanApprovalActionBar } from './HumanApprovalActionBar'
import { HumanApprovalFieldForm } from './HumanApprovalFieldForm'
import { HumanApprovalStatusBadge } from './HumanApprovalStatusBadge'
import type {
  HumanApprovalDecisionPayload,
  HumanApprovalRecord,
} from './types'

interface HumanApprovalCardProps {
  approval: HumanApprovalRecord
  submitting?: boolean
  className?: string
  onSubmit?: (payload: HumanApprovalDecisionPayload) => Promise<void> | void
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

function buildEditableValues(approval: HumanApprovalRecord): Record<string, unknown> {
  const base = approval.status === 'pending' ? approval.initialValues : approval.submittedValues
  return buildEditableFieldValues(approval.fieldSchema, base)
}

export function HumanApprovalCard({
  approval,
  submitting = false,
  className,
  onSubmit,
}: HumanApprovalCardProps) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, unknown>>(() => buildEditableValues(approval))
  const [comment, setComment] = useState(approval.comment ?? '')

  useEffect(() => {
    setValues(buildEditableValues(approval))
    setComment(approval.comment ?? '')
  }, [approval])

  const title = String(approval.requestPayload.title ?? '').trim() || approval.nodeLabel || approval.nodeId
  const instruction = String(approval.requestPayload.instruction ?? '').trim()
  const approveLabel = String(approval.requestPayload.approveLabel ?? approval.requestPayload.approve_label ?? '').trim()
  const rejectLabel = String(approval.requestPayload.rejectLabel ?? approval.requestPayload.reject_label ?? '').trim()
  const requireRejectComment = Boolean(
    approval.requestPayload.requireRejectComment
      ?? approval.requestPayload.require_reject_comment
      ?? true,
  )
  const pending = approval.status === 'pending'
  const resolvedTime = approval.resolvedAt
  const createdTime = approval.createdAt

  const subtitle = useMemo(() => {
    if (pending) {
      return createdTime ? new Date(createdTime).toLocaleString() : ''
    }
    return resolvedTime ? new Date(resolvedTime).toLocaleString() : ''
  }, [createdTime, pending, resolvedTime])

  const handleSubmit = async (decision: 'approved' | 'rejected') => {
    if (!pending || !onSubmit) return

    const collected = collectFieldValues(approval.fieldSchema, values)
    if (!collected.ok) {
      toast.error(t(fieldErrorMessageKey(collected.code), {
        field: collected.field.label || collected.field.name,
      }))
      return
    }

    const normalizedComment = comment.trim()
    if (decision === 'rejected' && requireRejectComment && !normalizedComment) {
      toast.error(t('settings.skills.humanApproval.validationRejectCommentRequired'))
      return
    }

    await onSubmit({
      decision,
      values: collected.values,
      comment: normalizedComment || undefined,
    })
  }

  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-3 shadow-sm ${className ?? ''}`}>
      <div className="mb-2.5 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-slate-800">{title}</div>
          <div className="mt-0.5 text-[11px] text-slate-500">
            {pending ? t('settings.skills.humanApproval.pendingAt') : t('settings.skills.humanApproval.resolvedAt')}
            {subtitle ? `: ${subtitle}` : ''}
          </div>
        </div>
        <HumanApprovalStatusBadge status={approval.status} />
      </div>

      {instruction && (
        <p className="mb-2.5 rounded-md border border-slate-100 bg-slate-50 px-2 py-1.5 text-xs text-slate-600">
          {instruction}
        </p>
      )}

      <HumanApprovalFieldForm
        fields={approval.fieldSchema}
        values={values}
        disabled={!pending || submitting}
        onChange={(name, value) => {
          setValues((prev) => ({ ...prev, [name]: value }))
        }}
      />

      {pending ? (
        <HumanApprovalActionBar
          approveLabel={approveLabel}
          rejectLabel={rejectLabel}
          requireRejectComment={requireRejectComment}
          comment={comment}
          submitting={submitting}
          onCommentChange={setComment}
          onApprove={() => void handleSubmit('approved')}
          onReject={() => void handleSubmit('rejected')}
        />
      ) : (
        <div className="mt-3 border-t border-slate-200 pt-2 text-xs text-slate-600">
          {approval.comment ? `${t('settings.skills.humanApproval.comment')}: ${approval.comment}` : t('settings.skills.humanApproval.noComment')}
        </div>
      )}
    </div>
  )
}
