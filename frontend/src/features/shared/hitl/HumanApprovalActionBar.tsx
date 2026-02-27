import { useTranslation } from 'react-i18next'

interface HumanApprovalActionBarProps {
  approveLabel?: string
  rejectLabel?: string
  requireRejectComment?: boolean
  comment: string
  disabled?: boolean
  submitting?: boolean
  onCommentChange: (value: string) => void
  onApprove: () => void
  onReject: () => void
}

export function HumanApprovalActionBar({
  approveLabel,
  rejectLabel,
  requireRejectComment = true,
  comment,
  disabled = false,
  submitting = false,
  onCommentChange,
  onApprove,
  onReject,
}: HumanApprovalActionBarProps) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2.5 border-t border-slate-200 pt-3">
      <div className="space-y-1">
        <label className="text-[11px] font-medium text-slate-700">
          {t('settings.skills.humanApproval.comment')}
          {requireRejectComment ? ' *' : ''}
        </label>
        <input
          type="text"
          value={comment}
          onChange={(e) => onCommentChange(e.target.value)}
          disabled={disabled || submitting}
          className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
          placeholder={t('settings.skills.humanApproval.commentPlaceholder')}
        />
      </div>
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          disabled={disabled || submitting}
          onClick={onReject}
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {rejectLabel || t('settings.skills.humanApproval.reject')}
        </button>
        <button
          type="button"
          disabled={disabled || submitting}
          onClick={onApprove}
          className="rounded-md border border-emerald-200 bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {approveLabel || t('settings.skills.humanApproval.approve')}
        </button>
      </div>
    </div>
  )
}
