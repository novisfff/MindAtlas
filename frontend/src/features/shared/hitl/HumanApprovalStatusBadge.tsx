import { useTranslation } from 'react-i18next'
import type { HumanApprovalStatus } from './types'

const STATUS_STYLES: Record<HumanApprovalStatus, string> = {
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  approved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  rejected: 'bg-rose-50 text-rose-700 border-rose-200',
  cancelled: 'bg-slate-100 text-slate-600 border-slate-200',
}

export function HumanApprovalStatusBadge({ status }: { status: HumanApprovalStatus }) {
  const { t } = useTranslation()
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[status]}`}>
      {t(`settings.skills.humanApproval.status.${status}`)}
    </span>
  )
}
