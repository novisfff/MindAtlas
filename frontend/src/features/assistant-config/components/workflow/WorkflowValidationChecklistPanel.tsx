import { AlertCircle, AlertTriangle, Loader2, LocateFixed, RefreshCcw, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { WorkflowValidationIssue } from './workflowValidation'

interface WorkflowValidationChecklistPanelProps {
  open: boolean
  isValidating: boolean
  errors: WorkflowValidationIssue[]
  warnings: WorkflowValidationIssue[]
  requestError: string | null
  lastValidatedAt: number | null
  onClose: () => void
  onLocate: (issue: WorkflowValidationIssue) => void
  onRefresh: () => void
}

function IssueRow({
  issue,
  onLocate,
  locateLabel,
  severityLabel,
}: {
  issue: WorkflowValidationIssue
  onLocate: (issue: WorkflowValidationIssue) => void
  locateLabel: string
  severityLabel: string
}) {
  const canLocate = Boolean(issue.nodeId)
  const nodeDisplay = issue.subflowNodeId
    ? `${issue.nodeId}::${issue.subflowNodeId}`
    : issue.nodeId

  return (
    <div className="rounded-lg border bg-muted/10 px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`
                inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide
                ${issue.severity === 'error'
                  ? 'border-red-300 bg-red-50 text-red-700'
                  : 'border-amber-300 bg-amber-50 text-amber-700'
                }
              `}
            >
              {severityLabel}
            </span>
            {nodeDisplay && (
              <span className="text-[11px] text-muted-foreground truncate">{nodeDisplay}</span>
            )}
          </div>
          <div className="mt-1 text-xs leading-relaxed break-words">{issue.message}</div>
        </div>
        {canLocate && (
          <button
            onClick={() => onLocate(issue)}
            className="inline-flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[11px] hover:bg-muted"
            title={locateLabel}
          >
            <LocateFixed className="w-3 h-3" />
            {locateLabel}
          </button>
        )}
      </div>
    </div>
  )
}

export function WorkflowValidationChecklistPanel({
  open,
  isValidating,
  errors,
  warnings,
  requestError,
  lastValidatedAt,
  onClose,
  onLocate,
  onRefresh,
}: WorkflowValidationChecklistPanelProps) {
  const { t } = useTranslation()

  if (!open) return null

  const updatedLabel = lastValidatedAt
    ? t('settings.skills.workflowValidationChecklistUpdatedAt', {
        time: new Date(lastValidatedAt).toLocaleTimeString(),
      })
    : t('settings.skills.workflowValidationChecklistUpdatedAt', { time: '-' })

  const hasIssues = errors.length + warnings.length > 0

  return (
    <div className="absolute top-24 right-4 xl:right-[26rem] z-20 pointer-events-auto w-[420px] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-7rem)]">
      <div className="h-full rounded-2xl border bg-white shadow-2xl overflow-hidden flex flex-col">
        <div className="px-4 py-3 border-b bg-white/95">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold">
                {t('settings.skills.workflowValidationChecklistTitle')}
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                {isValidating ? (
                  <>
                    <Loader2 className="w-3 h-3 animate-spin" />
                    {t('settings.skills.workflowValidationChecklistValidating')}
                  </>
                ) : (
                  <>{updatedLabel}</>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={onRefresh}
                className="p-1.5 rounded-md hover:bg-muted"
                title={t('settings.skills.workflowActions.validate')}
              >
                <RefreshCcw className={`w-4 h-4 ${isValidating ? 'animate-spin' : ''}`} />
              </button>
              <button
                onClick={onClose}
                className="p-1.5 rounded-md hover:bg-muted"
                title={t('actions.close')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1 rounded border border-red-300 bg-red-50 px-2 py-1 text-red-700">
              <AlertCircle className="w-3 h-3" />
              {t('settings.skills.workflowValidationChecklistErrors', { count: errors.length })}
            </span>
            <span className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-amber-700">
              <AlertTriangle className="w-3 h-3" />
              {t('settings.skills.workflowValidationChecklistWarnings', { count: warnings.length })}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-3 space-y-3">
          {requestError && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {t('settings.skills.workflowValidationChecklistRequestFailed')}
              {': '}
              {requestError}
            </div>
          )}

          {!hasIssues && !requestError && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              {t('settings.skills.workflowValidationChecklistNoIssues')}
            </div>
          )}

          {errors.length > 0 && (
            <section className="space-y-2">
              <div className="text-xs font-semibold text-red-700">
                {t('settings.skills.workflowValidationChecklistErrors', { count: errors.length })}
              </div>
              {errors.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  onLocate={onLocate}
                  locateLabel={t('settings.skills.workflowValidationChecklistLocate')}
                  severityLabel={t('settings.skills.workflowValidationSeverityError')}
                />
              ))}
            </section>
          )}

          {warnings.length > 0 && (
            <section className="space-y-2">
              <div className="text-xs font-semibold text-amber-700">
                {t('settings.skills.workflowValidationChecklistWarnings', { count: warnings.length })}
              </div>
              {warnings.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  onLocate={onLocate}
                  locateLabel={t('settings.skills.workflowValidationChecklistLocate')}
                  severityLabel={t('settings.skills.workflowValidationSeverityWarning')}
                />
              ))}
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
