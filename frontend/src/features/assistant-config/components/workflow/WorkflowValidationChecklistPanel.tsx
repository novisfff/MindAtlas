import { AlertCircle, AlertTriangle, ListChecks, Loader2, LocateFixed, RefreshCcw, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { WorkflowValidationIssue } from './workflowValidation'
import { WorkflowEditorSurfaceShell } from './WorkflowEditorSurfaceShell'

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
  onAskAiFix?: () => void
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
  onAskAiFix,
}: WorkflowValidationChecklistPanelProps) {
  const { t } = useTranslation()

  if (!open) return null

  const updatedLabel = lastValidatedAt
    ? t('settings.skills.workflowValidationChecklistUpdatedAt', {
        time: new Date(lastValidatedAt).toLocaleTimeString(),
      })
    : t('settings.skills.workflowValidationChecklistUpdatedAt', { time: '-' })

  const hasIssues = errors.length + warnings.length > 0
  const subtitle = isValidating
    ? t('settings.skills.workflowValidationChecklistValidating')
    : updatedLabel

  return (
    <WorkflowEditorSurfaceShell
      size="narrow"
      fluid
      icon={<ListChecks className="h-4 w-4" />}
      title={t('settings.skills.workflowValidationChecklistTitle')}
      subtitle={subtitle}
      onClose={onClose}
      headerActions={(
        <>
          {onAskAiFix && hasIssues ? (
            <button
              onClick={onAskAiFix}
              className="inline-flex items-center gap-1.5 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-[11px] font-medium text-slate-700 transition-colors hover:bg-slate-50"
              title={t('settings.skills.workflowCopilot.fixWithAi')}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {t('settings.skills.workflowCopilot.fixWithAi')}
            </button>
          ) : null}
          <button
            onClick={onRefresh}
            className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-800"
            title={t('settings.skills.workflowActions.validate')}
          >
            <RefreshCcw className={`h-4 w-4 ${isValidating ? 'animate-spin' : ''}`} />
          </button>
        </>
      )}
      bodyClassName="min-h-0 flex-1 overflow-auto bg-slate-50/70 px-4 py-4"
    >
      <div className="flex items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1 rounded-full border border-red-300 bg-red-50 px-2.5 py-1 text-red-700">
          <AlertCircle className="h-3 w-3" />
          {t('settings.skills.workflowValidationChecklistErrors', { count: errors.length })}
        </span>
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-amber-700">
          <AlertTriangle className="h-3 w-3" />
          {t('settings.skills.workflowValidationChecklistWarnings', { count: warnings.length })}
        </span>
      </div>

      <div className="mt-4 space-y-3">
          {requestError && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {t('settings.skills.workflowValidationChecklistRequestFailed')}
              {': '}
              {requestError}
            </div>
          )}

          {!hasIssues && !requestError && (
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
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
    </WorkflowEditorSurfaceShell>
  )
}
