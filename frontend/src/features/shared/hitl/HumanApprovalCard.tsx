import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { HumanApprovalActionBar } from './HumanApprovalActionBar'
import { HumanApprovalFieldForm } from './HumanApprovalFieldForm'
import { HumanApprovalStatusBadge } from './HumanApprovalStatusBadge'
import type {
  HumanApprovalDecisionPayload,
  HumanApprovalFieldSchema,
  HumanApprovalFieldWidget,
  HumanApprovalRecord,
} from './types'

interface HumanApprovalCardProps {
  approval: HumanApprovalRecord
  submitting?: boolean
  className?: string
  onSubmit?: (payload: HumanApprovalDecisionPayload) => Promise<void> | void
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/

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

function normalizeOptions(field: HumanApprovalFieldSchema): string[] {
  if (!Array.isArray(field.options)) return []
  const deduped: string[] = []
  const seen = new Set<string>()
  field.options.forEach((item) => {
    if (typeof item !== 'string') return
    const text = item.trim()
    if (!text || seen.has(text)) return
    seen.add(text)
    deduped.push(text)
  })
  return deduped
}

function toStringArray(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw
      .map((item) => String(item ?? '').trim())
      .filter(Boolean)
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
  if (widget === 'tag_selector' || field.type === 'array') {
    return toStringArray(raw)
  }
  if (widget === 'switch' || field.type === 'boolean') {
    if (typeof raw === 'boolean') return raw
    return String(raw ?? '').trim().toLowerCase() === 'true'
  }
  if (widget === 'date') {
    return normalizeDateText(raw)
  }
  if (widget === 'time') {
    return normalizeTimeText(raw)
  }
  if (raw === null || raw === undefined) return ''
  return typeof raw === 'string' ? raw : String(raw)
}

function buildEditableValues(approval: HumanApprovalRecord): Record<string, unknown> {
  const base = approval.status === 'pending' ? approval.initialValues : approval.submittedValues
  const values: Record<string, unknown> = {}
  approval.fieldSchema.forEach((field) => {
    values[field.name] = normalizeInitialValue(field, base[field.name])
  })
  return values
}

function isEmptyValue(field: HumanApprovalFieldSchema, raw: unknown): boolean {
  if (raw === null || raw === undefined) return true
  if (field.type === 'array' || normalizeWidget(field) === 'tag_selector') {
    return toStringArray(raw).length === 0
  }
  if (field.type === 'boolean' || normalizeWidget(field) === 'switch') {
    return false
  }
  return String(raw).trim() === ''
}

function coerceFieldValue(field: HumanApprovalFieldSchema, raw: unknown): unknown {
  const widget = normalizeWidget(field)
  const options = normalizeOptions(field)

  if (field.type === 'string') {
    const value = typeof raw === 'string' ? raw : String(raw ?? '')
    if (widget === 'date') {
      const rawText = value.trim()
      if (!rawText) {
        return ''
      }
      const normalized = normalizeDateText(rawText)
      if (!normalized) {
        throw new Error('date')
      }
      return normalized
    }
    if (widget === 'time') {
      const rawText = value.trim()
      if (!rawText) {
        return ''
      }
      const normalized = normalizeTimeText(rawText)
      if (!normalized) {
        throw new Error('time')
      }
      return normalized
    }
    if (widget === 'select' || widget === 'radio') {
      const normalized = value.trim()
      if (!options.includes(normalized)) {
        throw new Error(widget)
      }
      return normalized
    }
    return value
  }

  if (field.type === 'array') {
    const values = toStringArray(raw)
    if (widget === 'tag_selector') {
      const allowCustom = field.allowCustom ?? true
      if (!allowCustom && options.length > 0) {
        const unknown = values.filter((item) => !options.includes(item))
        if (unknown.length > 0) {
          throw new Error('tag')
        }
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
    if (!Number.isInteger(parsed)) {
      throw new Error('integer')
    }
    if (widget === 'select' || widget === 'radio') {
      const candidates = [String(parsed), String(Math.trunc(parsed))]
      if (!candidates.some((item) => options.includes(item))) {
        throw new Error(widget)
      }
    }
    return parsed
  }
  if (field.type === 'number') {
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) {
      throw new Error('number')
    }
    if (widget === 'select' || widget === 'radio') {
      const candidates = [String(parsed), Number.isInteger(parsed) ? String(Math.trunc(parsed)) : '']
      if (!candidates.some((item) => item && options.includes(item))) {
        throw new Error(widget)
      }
    }
    return parsed
  }
  return raw ?? ''
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

    const payloadValues: Record<string, unknown> = {}
    for (const field of approval.fieldSchema) {
      const raw = values[field.name]
      const widget = normalizeWidget(field)
      if (isEmptyValue(field, raw)) {
        if (field.required) {
          toast.error(t('settings.skills.humanApproval.validationRequired', { field: field.label || field.name }))
          return
        }
        if (widget === 'date' || widget === 'time') {
          payloadValues[field.name] = ''
        }
        continue
      }
      let coerced: unknown
      try {
        coerced = coerceFieldValue(field, raw)
      } catch (error) {
        const code = error instanceof Error ? error.message : ''
        if (code === 'select') {
          toast.error(t('settings.skills.humanApproval.validationSelectOption', { field: field.label || field.name }))
          return
        }
        if (code === 'radio') {
          toast.error(t('settings.skills.humanApproval.validationRadioOption', { field: field.label || field.name }))
          return
        }
        if (code === 'tag') {
          toast.error(t('settings.skills.humanApproval.validationTagOption', { field: field.label || field.name }))
          return
        }
        if (code === 'date') {
          toast.error(t('settings.skills.humanApproval.validationDateFormat', { field: field.label || field.name }))
          return
        }
        if (code === 'time') {
          toast.error(t('settings.skills.humanApproval.validationTimeFormat', { field: field.label || field.name }))
          return
        }
        toast.error(t('settings.skills.humanApproval.validationInvalidType', { field: field.label || field.name }))
        return
      }
      payloadValues[field.name] = coerced
    }

    const normalizedComment = comment.trim()
    if (decision === 'rejected' && requireRejectComment && !normalizedComment) {
      toast.error(t('settings.skills.humanApproval.validationRejectCommentRequired'))
      return
    }

    await onSubmit({
      decision,
      values: payloadValues,
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
