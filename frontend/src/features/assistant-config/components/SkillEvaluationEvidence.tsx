/**
 * Bounded evaluation evidence view (Plan 09 Task 9).
 * Shows actual skills, owner-qualified capability traces, completion/obligations,
 * metrics, assertion failures, missing safety, retention/expiry, promotion eligibility.
 * Never renders raw credentials, unbounded provider payloads, or unsafe resource bytes.
 */
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

import type {
  CaseResultSummary,
  EvalRunEvidence,
  EvalRunSummary,
} from '../api/skill-evaluations'

const PREVIEW_LIMIT = 400

export interface SkillEvaluationEvidenceProps {
  run: EvalRunSummary | null
  caseResults: CaseResultSummary[]
  evidence: EvalRunEvidence | null
  metrics?: Record<string, unknown>
  assertions?: Array<Record<string, unknown>>
  className?: string
}

function previewValue(value: unknown): string {
  try {
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    if (!text) return '—'
    return text.length > PREVIEW_LIMIT ? `${text.slice(0, PREVIEW_LIMIT)}…` : text
  } catch {
    return '—'
  }
}

function collectActualSkills(caseResults: CaseResultSummary[]): string[] {
  const set = new Set<string>()
  for (const row of caseResults) {
    for (const skill of row.actualActiveSkills || []) {
      if (skill) set.add(skill)
    }
  }
  return [...set].sort()
}

function collectAssertionFailures(
  caseResults: CaseResultSummary[],
  assertions: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const failures: Array<Record<string, unknown>> = []
  for (const row of caseResults) {
    const state = (row.resultState || '').toLowerCase()
    if (state.includes('fail') || state.includes('error') || row.safeError) {
      failures.push({
        evalCaseId: row.evalCaseId,
        resultState: row.resultState,
        safeError: row.safeError,
        assertionDetails: row.assertionDetails,
      })
    }
  }
  for (const assertion of assertions) {
    const passed = assertion.passed
    const ok = assertion.ok
    if (passed === false || ok === false) {
      failures.push(assertion)
    }
  }
  return failures.slice(0, 50)
}

function hasMissingSafety(caseResults: CaseResultSummary[], evidence: EvalRunEvidence | null): boolean {
  if (!evidence) return false
  if (evidence.gateEligible) return false
  // structural_synthetic is intentionally non-gate-eligible; that alone is not "missing safety".
  // Only surface missing-safety when case assertions or explicit safety counters say so.
  return caseResults.some((row) => {
    const details = row.assertionDetails || {}
    return (
      details.missingSafety === true ||
      details.safetyMissing === true ||
      details.safetyEvidence === 'missing' ||
      details.missing_safety === true
    )
  })
}

export function SkillEvaluationEvidence({
  run,
  caseResults,
  evidence,
  metrics = {},
  assertions = [],
  className,
}: SkillEvaluationEvidenceProps) {
  const { t } = useTranslation()
  if (!run && caseResults.length === 0 && !evidence) return null

  const actualSkills = collectActualSkills(caseResults)
  const failures = collectAssertionFailures(caseResults, assertions)
  const capabilityCalls = evidence?.capabilityCalls ?? []
  const missingSafety = hasMissingSafety(caseResults, evidence)
  const gateEligible = evidence?.gateEligible ?? run?.gateEligible ?? false
  const retention =
    typeof metrics.retentionUntil === 'string'
      ? metrics.retentionUntil
      : typeof metrics.expiresAt === 'string'
        ? metrics.expiresAt
        : run?.endedAt
          ? `${run.endedAt} (run end)`
          : null

  const completions = caseResults.filter((row) => {
    const stop = (row.stopReason || '').toLowerCase()
    return stop.includes('complete') || row.resultState === 'passed' || row.resultState === 'completed'
  })

  return (
    <div className={cn('space-y-3 rounded-md border p-3', className)} aria-label={t('settings.universalSkills.evidenceTitle')}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">{t('settings.universalSkills.evidenceTitle')}</span>
        <span className="rounded-full border px-2 py-0.5">
          {t('settings.universalSkills.promotionEligible')}: {gateEligible ? 'yes' : 'no'}
        </span>
        {evidence?.evidenceProvenance ? (
          <span className="rounded-full border px-2 py-0.5 font-mono">
            {evidence.evidenceProvenance}
          </span>
        ) : null}
        {missingSafety ? (
          <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-destructive">
            {t('settings.universalSkills.missingSafety')}
          </span>
        ) : null}
      </div>

      <section className="space-y-1">
        <h4 className="text-xs font-medium text-muted-foreground">
          {t('settings.universalSkills.actualSkills')}
        </h4>
        {actualSkills.length === 0 ? (
          <p className="text-xs text-muted-foreground">—</p>
        ) : (
          <ul className="flex flex-wrap gap-1">
            {actualSkills.map((skill) => (
              <li key={skill} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
                {skill}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-1">
        <h4 className="text-xs font-medium text-muted-foreground">
          {t('settings.universalSkills.capabilityTraces')}
        </h4>
        {capabilityCalls.length === 0 ? (
          <p className="text-xs text-muted-foreground">—</p>
        ) : (
          <ol className="max-h-40 space-y-1 overflow-auto text-[11px]">
            {capabilityCalls.map((call) => (
              <li key={call.id} className="rounded border px-2 py-1 font-mono">
                <span>{call.logicalCallKey}</span>
                <span className="mx-1 text-muted-foreground">·</span>
                <span>{call.outcome}</span>
                <span className="mx-1 text-muted-foreground">·</span>
                <span>attempt={call.attempt}</span>
                {call.bindingDigest ? (
                  <>
                    <span className="mx-1 text-muted-foreground">·</span>
                    <span title={call.bindingDigest}>bind={call.bindingDigest.slice(0, 12)}…</span>
                  </>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <h4 className="text-xs font-medium text-muted-foreground">
            {t('settings.universalSkills.completionObligations')}
          </h4>
          <p className="text-xs">
            completed={completions.length}/{caseResults.length}
          </p>
          <ul className="max-h-28 space-y-1 overflow-auto text-[11px] text-muted-foreground">
            {caseResults.slice(0, 20).map((row) => (
              <li key={row.id} className="font-mono">
                {row.evalCaseId.slice(0, 8)}… · {row.resultState}
                {row.stopReason ? ` · ${row.stopReason}` : ''}
              </li>
            ))}
          </ul>
        </div>
        <div className="space-y-1">
          <h4 className="text-xs font-medium text-muted-foreground">
            {t('settings.universalSkills.aggregateMetrics')}
          </h4>
          {Object.keys(metrics).length === 0 ? (
            <p className="text-xs text-muted-foreground">—</p>
          ) : (
            <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
              {previewValue(metrics)}
            </pre>
          )}
          {retention ? (
            <p className="text-[11px] text-muted-foreground">
              {t('settings.universalSkills.retentionExpiry')}: {retention}
            </p>
          ) : null}
        </div>
      </section>

      <section className="space-y-1">
        <h4 className="text-xs font-medium text-muted-foreground">
          {t('settings.universalSkills.assertionFailures')}
        </h4>
        {failures.length === 0 ? (
          <p className="text-xs text-muted-foreground">—</p>
        ) : (
          <ol className="max-h-36 space-y-1 overflow-auto text-[11px]">
            {failures.map((item, index) => (
              <li key={index} className="rounded border border-destructive/30 bg-destructive/5 px-2 py-1">
                <pre className="whitespace-pre-wrap break-words text-muted-foreground">
                  {previewValue(item)}
                </pre>
              </li>
            ))}
          </ol>
        )}
      </section>

      {evidence?.artifacts?.length ? (
        <section className="space-y-1">
          <h4 className="text-xs font-medium text-muted-foreground">
            {t('settings.universalSkills.artifacts')}
          </h4>
          <ul className="max-h-28 space-y-1 overflow-auto text-[11px] font-mono text-muted-foreground">
            {evidence.artifacts.map((artifact) => (
              <li key={artifact.id}>
                {artifact.kind}
                {artifact.label ? ` · ${artifact.label}` : ''}
                {artifact.byteSize != null ? ` · ${artifact.byteSize}B` : ''}
                {artifact.contentDigest ? ` · ${artifact.contentDigest.slice(0, 12)}…` : ''}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
