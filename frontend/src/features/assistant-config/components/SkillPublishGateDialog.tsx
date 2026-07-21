/**
 * Publish gate dialog — submits only evidence refs + optional non-safety waivers.
 * Never submits/computes passed, assertions, metrics, or waiver eligibility client-side.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

import {
  createPublishGate,
  type CreateGateResponse,
  type PublishGateSubject,
} from '../api/skill-evaluations'
import { mapSkillPackageError, newRequestId } from '../api/skill-packages'

export interface SkillPublishGateDialogProps {
  open: boolean
  onClose: () => void
  subject: PublishGateSubject | null
  qualifyingEvalRunIds: string[]
  onCreated?: (result: CreateGateResponse) => void
  className?: string
}

export function SkillPublishGateDialog({
  open,
  onClose,
  subject,
  qualifyingEvalRunIds,
  onCreated,
  className,
}: SkillPublishGateDialogProps) {
  const { t } = useTranslation()
  const [waiverCodes, setWaiverCodes] = useState('')
  const [waiverReason, setWaiverReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<CreateGateResponse | null>(null)

  if (!open) return null

  async function handleSubmit() {
    if (!subject) {
      setError(t('settings.universalSkills.gateNeedsSubject'))
      return
    }
    if (qualifyingEvalRunIds.length === 0) {
      setError(t('settings.universalSkills.gateNeedsRuns'))
      return
    }
    const codes = waiverCodes
      .split(/[\s,]+/)
      .map((c) => c.trim())
      .filter(Boolean)
    if (codes.length > 0 && !waiverReason.trim()) {
      setError(t('settings.universalSkills.gateWaiverReasonRequired'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      // Server builds authoritative subject; client only sends action + identity + evidence refs.
      const created = await createPublishGate({
        requestId: (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : newRequestId('gate'),
        action: 'skill_publish',
        subjectAggregateId: subject.subject.aggregateId,
        subjectVersionId: subject.subject.versionId,
        qualifyingEvalRunIds,
        requestedNonSafetyWaiverCodes: codes,
        waiverReason: codes.length ? waiverReason.trim() : null,
      })
      setResult(created)
      onCreated?.(created)
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t('settings.universalSkills.gateDialogTitle')}
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4',
        className,
      )}
    >
      <div className="max-h-[90vh] w-full max-w-xl overflow-auto rounded-xl border bg-background p-5 shadow-lg">
        <h3 className="text-lg font-semibold">{t('settings.universalSkills.gateDialogTitle')}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{t('settings.universalSkills.gateDialogHint')}</p>

        <div className="mt-4 space-y-3 text-sm">
          <div>
            <div className="text-muted-foreground">{t('settings.universalSkills.gateEvidenceRuns')}</div>
            <div className="font-mono text-xs break-all">
              {qualifyingEvalRunIds.length ? qualifyingEvalRunIds.join(', ') : '—'}
            </div>
          </div>
          <label className="block space-y-1">
            <span>{t('settings.universalSkills.gateWaiverCodes')}</span>
            <input
              className={uiField.input}
              value={waiverCodes}
              onChange={(e) => setWaiverCodes(e.target.value)}
              placeholder="optional,non_safety_codes"
            />
          </label>
          <label className="block space-y-1">
            <span>{t('settings.universalSkills.gateWaiverReason')}</span>
            <textarea
              className={cn(uiField.textarea, 'min-h-[80px]')}
              value={waiverReason}
              onChange={(e) => setWaiverReason(e.target.value)}
            />
          </label>
          <p className="text-xs text-muted-foreground">{t('settings.universalSkills.gateNoClientDecision')}</p>
        </div>

        {error ? (
          <div role="alert" className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            {error}
          </div>
        ) : null}

        {result ? (
          <div className="mt-3 rounded-md border p-3 text-sm">
            <div>
              {t('settings.universalSkills.gateDecision')}: <strong>{result.decision}</strong>
            </div>
            <div className="font-mono text-xs">gate={result.gate.id}</div>
            {result.decision === 'failed' ? (
              <p className="mt-1 text-destructive">{t('settings.universalSkills.gateHardSafetyOrFail')}</p>
            ) : null}
            {result.decision === 'waived_non_safety' ? (
              <p className="mt-1 text-amber-700 dark:text-amber-300">
                {t('settings.universalSkills.gateNonSafetyWaiver')}
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button type="button" disabled={busy || !subject} onClick={() => void handleSubmit()}>
            {t('settings.universalSkills.requestGate')}
          </Button>
        </div>
      </div>
    </div>
  )
}
