import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export type UnsupportedCapabilityAction = 'update_entry' | 'merge_entry' | 'create_relation' | 'relation_followup' | 'unknown'

export function UnsupportedCapabilityNotice({ action }: { action: string }) {
  const { t } = useTranslation()
  const known: UnsupportedCapabilityAction[] = ['update_entry', 'merge_entry', 'create_relation', 'relation_followup']
  const safeAction = known.includes(action as UnsupportedCapabilityAction) ? action : 'unknown'
  return (
    <div role="alert" data-testid="unsupported-capability-notice" className="flex items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50/80 px-4 py-3 text-sm text-amber-950">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">{t('preGaLaunch.unsupportedCapability.title')}</p>
        <p>{t('preGaLaunch.unsupportedCapability.description')}</p>
        <p className="text-xs text-amber-800">{t('preGaLaunch.unsupportedCapability.action', { action: safeAction })}</p>
      </div>
    </div>
  )
}
