/**
 * Interactive evaluation workbench shell (Plan 09 Task 7).
 * Creates isolated eval runs, polls events after sequence, supports cancel.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Play, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

import {
  cancelEvalRun,
  createEvalRun,
  getEvalRun,
  listEvalRunEvents,
  type EvalRunMode,
} from '../api/skill-evaluations'
import { mapSkillPackageError, newRequestId } from '../api/skill-packages'
import { useSkillTestRunStore } from '../stores/skill-test-run-store'
import { SkillEvaluationRun } from './SkillEvaluationRun'

export interface SkillTestWorkbenchProps {
  packageId: string
  versionId: string | null
  contentDigest: string | null
  bindingDigest?: string | null
  className?: string
}

const ZERO_DIGEST = '0'.repeat(64)

export function SkillTestWorkbench({
  packageId,
  versionId,
  contentDigest,
  bindingDigest,
  className,
}: SkillTestWorkbenchProps) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<EvalRunMode>('interactive_scripted')
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const status = useSkillTestRunStore((s) => s.status)
  const activeRunId = useSkillTestRunStore((s) => s.activeRunId)
  const lastSequence = useSkillTestRunStore((s) => s.lastSequence)
  const events = useSkillTestRunStore((s) => s.events)
  const run = useSkillTestRunStore((s) => s.run)
  const beginRun = useSkillTestRunStore((s) => s.beginRun)
  const ingestEvents = useSkillTestRunStore((s) => s.ingestEvents)
  const reconcileRun = useSkillTestRunStore((s) => s.reconcileRun)
  const markCancelRequested = useSkillTestRunStore((s) => s.markCancelRequested)
  const markError = useSkillTestRunStore((s) => s.markError)
  const reset = useSkillTestRunStore((s) => s.reset)

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  useEffect(() => () => stopPolling(), [stopPolling])

  const poll = useCallback(
    async (runId: string, afterSequence: number) => {
      try {
        const page = await listEvalRunEvents(runId, { afterSequence, limit: 100 })
        if (page.items.length) ingestEvents(runId, page.items)
        const latest = await getEvalRun(runId)
        reconcileRun(latest)
        if (['completed', 'failed', 'cancelled'].includes(latest.status)) {
          stopPolling()
        }
      } catch (error) {
        markError(mapSkillPackageError(error).message)
        stopPolling()
      }
    },
    [ingestEvents, markError, reconcileRun, stopPolling],
  )

  async function handleStart() {
    if (!versionId || !contentDigest) {
      setLocalError(t('settings.universalSkills.workbenchNeedsDraft'))
      return
    }
    const digest = contentDigest
    const version = versionId
    setLocalError(null)
    setBusy(true)
    reset()
    try {
      const created = await createEvalRun({
        requestId: newRequestId('eval'),
        subjectKind: 'skill_draft',
        subjectAggregateId: packageId,
        subjectVersionId: version,
        subjectContentDigest: digest,
        subjectBindingDigest: bindingDigest || digest || ZERO_DIGEST,
        mode,
        datasetVersionIds: [],
      })
      beginRun(created)
      stopPolling()
      pollRef.current = window.setInterval(() => {
        const seq = useSkillTestRunStore.getState().lastSequence
        const id = useSkillTestRunStore.getState().activeRunId
        if (id) void poll(id, seq)
      }, 1500)
      await poll(created.id, 0)
    } catch (error) {
      setLocalError(mapSkillPackageError(error).message)
      markError(mapSkillPackageError(error).message)
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    if (!activeRunId) return
    setBusy(true)
    markCancelRequested()
    try {
      const cancelled = await cancelEvalRun(activeRunId, {
        requestId: newRequestId('cancel'),
        expectedStateRevision: run?.stateRevision,
      })
      reconcileRun(cancelled)
    } catch (error) {
      setLocalError(mapSkillPackageError(error).message)
    } finally {
      setBusy(false)
    }
  }

  const running = status === 'queued' || status === 'running' || status === 'cancelling'

  return (
    <div className={cn('space-y-4', className)}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-sm">
          <span>{t('settings.universalSkills.evalMode')}</span>
          <select
            className={uiField.select}
            value={mode}
            disabled={running || busy}
            onChange={(e) => setMode(e.target.value as EvalRunMode)}
          >
            <option value="interactive_scripted">interactive_scripted</option>
            <option value="dataset_scripted">dataset_scripted</option>
            <option value="dataset_live">dataset_live</option>
          </select>
        </label>
        <Button type="button" disabled={busy || running || !versionId} onClick={() => void handleStart()}>
          {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Play className="mr-1.5 h-4 w-4" />}
          {t('settings.universalSkills.startEval')}
        </Button>
        <Button type="button" variant="outline" disabled={!running || busy} onClick={() => void handleCancel()}>
          <Square className="mr-1.5 h-4 w-4" />
          {t('settings.universalSkills.cancelEval')}
        </Button>
      </div>

      {localError ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
          {localError}
        </div>
      ) : null}

      <SkillEvaluationRun
        status={status}
        run={run}
        events={events}
        lastSequence={lastSequence}
      />
    </div>
  )
}
