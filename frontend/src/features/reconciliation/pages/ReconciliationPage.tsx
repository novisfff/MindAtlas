import { useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2, ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { isApiError } from '@/lib/api/client'
import { useOperatorSessionQuery } from '@/features/operator-auth'
import {
  useReconcileCapabilityCallMutation,
  useReconciliationQuery,
} from '../queries'
import type { ReconciliationDecision } from '../api/reconciliation'
import {
  SettingsBadge,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function requestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return '00000000-0000-4000-8000-000000000001'
}

function errorCopy(error: unknown, t: (key: string) => string): string {
  if (isApiError(error)) {
    if (error.status === 401) return t('reconciliation.errors.sessionExpired')
    if (error.status === 403) return t('reconciliation.errors.forbidden')
    if (error.status === 409) return t('reconciliation.errors.conflict')
    if (error.status === 503) return t('reconciliation.errors.unavailable')
  }
  return t('reconciliation.errors.generic')
}

export function ReconciliationPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const session = useOperatorSessionQuery()
  const queue = useReconciliationQuery()
  const mutation = useReconcileCapabilityCallMutation()
  const [decision, setDecision] = useState<ReconciliationDecision>('mark_failed')
  const [artifactIds, setArtifactIds] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const isOperator = session.data?.role === 'operator'

  async function reconcile(callId: string, callRevision: number, runRevision: number) {
    if (!isOperator || mutation.isPending) return
    const ids = artifactIds.split(',').map((value) => value.trim()).filter(Boolean)
    if (!ids.length || ids.some((value) => !UUID.test(value)) || !reason.trim() || reason.trim().length > 500) {
      setError(t('reconciliation.errors.invalid'))
      return
    }
    setError(null)
    try {
      await mutation.mutateAsync({
        callId,
        input: {
          expectedCallRevision: callRevision,
          expectedRunRevision: runRevision,
          decision,
          evidenceArtifactIds: ids,
          requestId: requestId(),
          reason: reason.trim(),
        },
      })
      setReason('')
      setArtifactIds('')
    } catch (mutationError) {
      setError(errorCopy(mutationError, t))
    }
  }

  return (
    <SettingsPageShell className="space-y-6">
      <SettingsPageHeader
        title={t('reconciliation.title')}
        description={t('reconciliation.description')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      {error ? <div role="alert" className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{error}</div> : null}
      <SettingsSection className="space-y-5">
        <SettingsSectionHeader title={t('reconciliation.queue.title')} description={t('reconciliation.queue.description')} />
        {queue.isLoading ? <div role="status" className="text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />{t('reconciliation.loading')}</div> : null}
        {queue.isError ? <div role="alert" className="text-sm text-destructive">{errorCopy(queue.error, t)}</div> : null}
        {queue.data && queue.data.items.length === 0 ? <p className="text-sm text-muted-foreground">{t('reconciliation.queue.empty')}</p> : null}
        <ul className="space-y-4">
          {(queue.data?.items ?? []).map((call) => (
            <li key={call.callId} className="rounded-2xl border border-border/70 bg-background/80 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-mono text-sm text-foreground">{call.callId.slice(0, 12)}…</p>
                  <p className="mt-1 text-xs text-muted-foreground">{t('reconciliation.fields.run')}: {call.runId.slice(0, 12)}…</p>
                </div>
                <SettingsBadge className="text-amber-700">{call.status}</SettingsBadge>
              </div>
              <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                <span>{t('reconciliation.fields.callRevision')}: {call.stateRevision}</span>
                <span>{t('reconciliation.fields.runRevision')}: {call.runRevision}</span>
                <span>{t('reconciliation.fields.attempts')}: {call.attemptCount}</span>
                <span>{t('reconciliation.fields.failure')}: {call.failureCode ?? '—'}</span>
              </div>
              {call.executionMode === 'local_create_entry' ? <p className="mt-3 flex items-start gap-2 text-xs text-amber-700"><ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />{t('reconciliation.localCreateEntry')}</p> : null}
              {isOperator ? (
                <div className="mt-4 grid gap-3 border-t border-border/60 pt-4 md:grid-cols-[180px_1fr_auto]">
                  <label className="space-y-1 text-xs"><span className="font-medium">{t('reconciliation.form.decision')}</span><select value={decision} onChange={(event) => setDecision(event.target.value as ReconciliationDecision)} className="h-9 w-full rounded-lg border border-border bg-background px-2"><option value="mark_failed">{t('reconciliation.form.markFailed')}</option><option value="mark_succeeded">{t('reconciliation.form.markSucceeded')}</option><option value="mark_compensated">{t('reconciliation.form.markCompensated')}</option></select></label>
                  <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs"><span className="font-medium">{t('reconciliation.form.artifacts')}</span><input value={artifactIds} onChange={(event) => setArtifactIds(event.target.value)} placeholder={t('reconciliation.form.artifactsPlaceholder')} className="h-9 w-full rounded-lg border border-border bg-background px-2 font-mono text-xs" /></label><label className="space-y-1 text-xs"><span className="font-medium">{t('reconciliation.form.reason')}</span><input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} className="h-9 w-full rounded-lg border border-border bg-background px-2" /></label></div>
                  <Button type="button" size="sm" disabled={mutation.isPending} onClick={() => void reconcile(call.callId, call.stateRevision, call.runRevision)}><CheckCircle2 />{t('reconciliation.form.submit')}</Button>
                </div>
              ) : <p className="mt-4 flex items-start gap-2 text-xs text-muted-foreground"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{t('reconciliation.operatorOnly')}</p>}
            </li>
          ))}
        </ul>
      </SettingsSection>
    </SettingsPageShell>
  )
}
