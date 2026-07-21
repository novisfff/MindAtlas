/**
 * Bounded eval run result / trace view.
 * No raw secrets, resource bodies, or provider payloads.
 */
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

import type { EvalRunSummary } from '../api/skill-evaluations'
import type { SkillTestTraceEvent, SkillTestRunStatus, SkillTestTransportMode } from '../stores/skill-test-run-store'

const PAYLOAD_PREVIEW = 800

export interface SkillEvaluationRunProps {
  status: SkillTestRunStatus
  run: EvalRunSummary | null
  events: SkillTestTraceEvent[]
  lastSequence: number
  transportMode?: SkillTestTransportMode
  className?: string
}

function previewPayload(payload: Record<string, unknown>): string {
  try {
    const text = JSON.stringify(payload, null, 0)
    if (text.length <= PAYLOAD_PREVIEW) return text
    return `${text.slice(0, PAYLOAD_PREVIEW)}…`
  } catch {
    return '{}'
  }
}

export function SkillEvaluationRun({
  status,
  run,
  events,
  lastSequence,
  transportMode = 'idle',
  className,
}: SkillEvaluationRunProps) {
  const { t } = useTranslation()

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded-full border px-2 py-0.5">status={status}</span>
        <span className="rounded-full border px-2 py-0.5">seq≥{lastSequence}</span>
        <span className="rounded-full border px-2 py-0.5">transport={transportMode}</span>
        {run?.id ? <span className="rounded-full border px-2 py-0.5 font-mono">run={run.id}</span> : null}
        {run?.gateEligible != null ? (
          <span className="rounded-full border px-2 py-0.5">
            gateEligible={run.gateEligible ? 'yes' : 'no'}
          </span>
        ) : null}
        {run?.evidenceProvenance ? (
          <span className="rounded-full border px-2 py-0.5 font-mono">{run.evidenceProvenance}</span>
        ) : null}
        {run?.failureCode ? (
          <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-destructive">
            {run.failureCode}
          </span>
        ) : null}
      </div>

      {events.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {t('settings.universalSkills.noEvalEvents')}
        </div>
      ) : (
        <ol
          className="max-h-80 space-y-2 overflow-auto rounded-md border p-3"
          aria-label={t('settings.universalSkills.evalTrace')}
        >
          {events.map((event) => (
            <li key={`${event.runId}:${event.sequence}`} className="text-xs">
              <div className="flex flex-wrap gap-2">
                <span className="font-mono">#{event.sequence}</span>
                <span className="rounded bg-muted px-1.5 py-0.5">{event.eventType}</span>
              </div>
              <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                {previewPayload(event.payload)}
              </pre>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
