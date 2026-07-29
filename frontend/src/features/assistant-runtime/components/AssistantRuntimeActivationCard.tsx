import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Loader2, ShieldCheck } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { isApiError } from '@/lib/api/client'
import { cn } from '@/lib/utils'

import {
  getAssistantReadinessDiagnostics,
  listAssistantRollouts,
  type AssistantReadinessDiagnostics,
  type AssistantReadinessReason,
  type RolloutControlSummary,
} from '../api/runtime'
import { assistantRuntimeKeys, useActivateAssistantRolloutMutation } from '../queries'
import { reasonTranslationKey } from './reasonCopy'

function newActivationRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `activate-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export interface AssistantRuntimeActivationCardProps {
  preparedRolloutRevisionId: string | null
  rolloutControlRevision: number | null
  diagnostics: AssistantReadinessDiagnostics | null
  onActivated?: () => void
  className?: string
}

export function AssistantRuntimeActivationCard({
  preparedRolloutRevisionId,
  rolloutControlRevision,
  diagnostics,
  onActivated,
  className,
}: AssistantRuntimeActivationCardProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const activateMutation = useActivateAssistantRolloutMutation()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Local diagnostics only as fallback when parent props are null (e.g. post-409
  // before the next poll). Prefer live props so polling is never frozen.
  const [localDiagnostics, setLocalDiagnostics] = useState<AssistantReadinessDiagnostics | null>(
    null,
  )
  // Local control revision from 409/list refresh keeps CAS expected revision current.
  const [localControlRevision, setLocalControlRevision] = useState<number | null>(null)

  const effectiveDiagnostics = diagnostics ?? localDiagnostics
  const effectiveControlRevision = localControlRevision ?? rolloutControlRevision ?? 0
  // Always prefer the prepared id from props — never replace it with the active revision.
  const effectivePreparedId = preparedRolloutRevisionId

  const compatibleWorkers = effectiveDiagnostics?.compatibleWorkerIds ?? []
  const hasCompatibleWorker = compatibleWorkers.length > 0
  const alreadyActive =
    effectiveDiagnostics?.ready === true &&
    effectiveDiagnostics.activeRolloutRevisionId != null &&
    (effectivePreparedId == null ||
      effectiveDiagnostics.activeRolloutRevisionId === effectivePreparedId)

  const reasonCodes = (effectiveDiagnostics?.reasonCodes ?? []) as AssistantReadinessReason[]

  const canActivate = useMemo(() => {
    if (busy) return false
    if (alreadyActive) return false
    if (!effectivePreparedId) return false
    if (!hasCompatibleWorker) return false
    if (effectiveControlRevision == null) return false
    return true
  }, [
    alreadyActive,
    busy,
    effectiveControlRevision,
    effectivePreparedId,
    hasCompatibleWorker,
  ])

  function invalidateRuntimeQueries() {
    void queryClient.invalidateQueries({ queryKey: assistantRuntimeKeys.publicReadiness() })
    void queryClient.invalidateQueries({ queryKey: assistantRuntimeKeys.diagnostics() })
    void queryClient.invalidateQueries({ queryKey: assistantRuntimeKeys.rollouts() })
  }

  async function refreshControlAndDiagnostics() {
    const [nextDiagnostics, rollouts] = await Promise.all([
      getAssistantReadinessDiagnostics(),
      listAssistantRollouts(),
    ])
    setLocalDiagnostics(nextDiagnostics)
    const control: RolloutControlSummary | undefined = rollouts.control
    if (control) {
      // Only refresh control revision for CAS; keep prepared id from props.
      setLocalControlRevision(control.controlRevision)
    }
    invalidateRuntimeQueries()
    return { nextDiagnostics, control }
  }

  async function handleActivate() {
    if (!canActivate || !effectivePreparedId) return
    setBusy(true)
    setError(null)
    const requestId = newActivationRequestId()
    try {
      await activateMutation.mutateAsync({
        revisionId: effectivePreparedId,
        body: {
          expectedControlRevision: effectiveControlRevision,
          requestId,
          reason: 'activate prepared Main Agent runtime',
        },
      })
      onActivated?.()
    } catch (err) {
      const status = isApiError(err) ? err.status : (err as { status?: number } | null)?.status
      if (status === 409) {
        setError(t('assistantRuntime.activation.conflict'))
        try {
          await refreshControlAndDiagnostics()
        } catch {
          // Keep conflict copy; operator can retry after manual refresh.
        }
      } else {
        setError(t('assistantRuntime.activation.error'))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className={cn(
        'space-y-4 rounded-[24px] border border-slate-200/80 bg-white/95 p-6 shadow-sm',
        className,
      )}
      data-testid="assistant-runtime-activation-card"
    >
      <div className="flex items-start gap-3">
        <div className="rounded-2xl bg-slate-900 p-3 text-white">
          <ShieldCheck className="h-5 w-5" />
        </div>
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-slate-900">
            {t('assistantRuntime.activation.title')}
          </h2>
          <p className="text-sm leading-6 text-slate-600">
            {t('assistantRuntime.activation.description')}
          </p>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            {t('assistantRuntime.activation.pendingBootstrap')}
          </p>
        </div>
      </div>

      {!hasCompatibleWorker ? (
        <div
          className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          role="status"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('assistantRuntime.activation.waitingWorker')}
        </div>
      ) : null}

      {reasonCodes.length > 0 ? (
        <ul className="space-y-2" data-testid="activation-reason-list">
          {reasonCodes.map((code) => (
            <li
              key={code}
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700"
            >
              {t(reasonTranslationKey(code), { defaultValue: code })}
            </li>
          ))}
        </ul>
      ) : null}

      {error ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {alreadyActive ? (
        <p className="text-sm font-medium text-emerald-700">
          {t('assistantRuntime.activation.ready')}
        </p>
      ) : (
        <Button
          type="button"
          disabled={!canActivate}
          onClick={() => void handleActivate()}
        >
          {busy
            ? t('assistantRuntime.activation.activating')
            : t('assistantRuntime.activation.activate')}
        </Button>
      )}
    </div>
  )
}
