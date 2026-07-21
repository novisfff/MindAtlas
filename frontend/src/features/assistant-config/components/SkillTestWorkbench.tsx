/**
 * Interactive evaluation workbench (Plan 09 Task 9).
 * Typed inputs for prompt/locale/profile/mode/dataset/fixture.
 * SSE replay with afterSequence; polling only after transport failure.
 * Client never authors digests.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, Play, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

import {
  cancelEvalRun,
  createEvalRun,
  getEvalRun,
  listDatasetVersions,
  listEvalDatasets,
  listEvalRunCaseResults,
  listEvalRunEvidence,
  listEvalRunEvents,
  streamEvalRunEvents,
  type CaseResultSummary,
  type CreateEvalRunRequest,
  type DatasetVersionSummary,
  type EvalRunEvidence,
  type EvalRunMode,
} from '../api/skill-evaluations'
import {
  getDefaultMainAgentProfile,
  listDefaultMainAgentVersions,
} from '../api/main-agent-profiles'
import { mapSkillPackageError, newRequestId } from '../api/skill-packages'
import { useSkillTestRunStore } from '../stores/skill-test-run-store'
import { SkillEvaluationEvidence } from './SkillEvaluationEvidence'
import { SkillEvaluationRun } from './SkillEvaluationRun'

export interface SkillTestWorkbenchProps {
  packageId: string
  versionId: string | null
  subjectKind?: 'skill_draft' | 'skill_version'
  className?: string
}

const BUILTIN_FIXTURES = [
  'provider-direct-answer@eval-v1',
  'provider-selects-skill-a@eval-v1',
  'provider-selects-skill-b@eval-v1',
  'provider-missing-secret-counter@eval-v1',
] as const

const POLL_INTERVAL_MS = 1500
const POLL_MAX_TICKS = 120

export function SkillTestWorkbench({
  packageId,
  versionId,
  subjectKind = 'skill_draft',
  className,
}: SkillTestWorkbenchProps) {
  const { t, i18n } = useTranslation()
  const [mode, setMode] = useState<EvalRunMode>('interactive_scripted')
  const [prompt, setPrompt] = useState('Evaluate this skill package draft.')
  const [locale, setLocale] = useState(() => i18n?.language?.slice(0, 2) || 'en')
  const [profileVersionId, setProfileVersionId] = useState('')
  const [datasetVersionId, setDatasetVersionId] = useState('')
  const [providerFixtureRevision, setProviderFixtureRevision] = useState<string>(
    BUILTIN_FIXTURES[0],
  )
  const [liveModelId, setLiveModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [caseResults, setCaseResults] = useState<CaseResultSummary[]>([])
  const [evidence, setEvidence] = useState<EvalRunEvidence | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const pollRef = useRef<number | null>(null)
  const pollTicksRef = useRef(0)
  const generationRef = useRef(0)

  const status = useSkillTestRunStore((s) => s.status)
  const activeRunId = useSkillTestRunStore((s) => s.activeRunId)
  const lastSequence = useSkillTestRunStore((s) => s.lastSequence)
  const events = useSkillTestRunStore((s) => s.events)
  const run = useSkillTestRunStore((s) => s.run)
  const metrics = useSkillTestRunStore((s) => s.metrics)
  const assertions = useSkillTestRunStore((s) => s.assertions)
  const transportMode = useSkillTestRunStore((s) => s.transportMode)
  const beginRun = useSkillTestRunStore((s) => s.beginRun)
  const ingestEvents = useSkillTestRunStore((s) => s.ingestEvents)
  const ingestHeartbeat = useSkillTestRunStore((s) => s.ingestHeartbeat)
  const setTransportMode = useSkillTestRunStore((s) => s.setTransportMode)
  const reconcileRun = useSkillTestRunStore((s) => s.reconcileRun)
  const markCancelRequested = useSkillTestRunStore((s) => s.markCancelRequested)
  const markError = useSkillTestRunStore((s) => s.markError)
  const reset = useSkillTestRunStore((s) => s.reset)

  const datasetsQuery = useQuery({
    queryKey: ['skill-eval', 'datasets'],
    queryFn: listEvalDatasets,
    staleTime: 30_000,
  })

  const profileQuery = useQuery({
    queryKey: ['main-agent-profile', 'default'],
    queryFn: getDefaultMainAgentProfile,
    staleTime: 30_000,
  })

  const profileVersionsQuery = useQuery({
    queryKey: ['main-agent-profile', 'default', 'versions'],
    queryFn: () => listDefaultMainAgentVersions({ limit: 50 }),
    staleTime: 30_000,
  })

  const publishedDatasets = useMemo(
    () => (datasetsQuery.data?.items ?? []).filter((d) => Boolean(d.publishedVersionId)),
    [datasetsQuery.data?.items],
  )

  const datasetVersionsQuery = useQuery({
    queryKey: ['skill-eval', 'dataset-versions', publishedDatasets.map((d) => d.id).join(',')],
    queryFn: async () => {
      const all: DatasetVersionSummary[] = []
      for (const ds of publishedDatasets) {
        const page = await listDatasetVersions(ds.id)
        for (const version of page.items) {
          if (ds.publishedVersionId && version.id === ds.publishedVersionId) {
            all.push(version)
          } else if (!ds.publishedVersionId) {
            all.push(version)
          }
        }
        // Always include the published pointer even if list is empty of match.
        if (ds.publishedVersionId && !all.some((v) => v.id === ds.publishedVersionId)) {
          all.push({
            id: ds.publishedVersionId,
            datasetId: ds.id,
            sequence: 0,
            versionName: ds.displayName,
            schemaVersion: 1,
            contentDigest: '',
            caseCount: 0,
          })
        }
      }
      return all
    },
    enabled: publishedDatasets.length > 0,
    staleTime: 30_000,
  })

  const profileVersions = useMemo(() => {
    const items = profileVersionsQuery.data?.items ?? []
    if (items.length > 0) return items
    const fallback =
      profileQuery.data?.publishedVersion ?? profileQuery.data?.draftVersion ?? null
    return fallback ? [fallback] : []
  }, [profileQuery.data, profileVersionsQuery.data?.items])

  useEffect(() => {
    if (profileVersionId) return
    const preferred =
      profileQuery.data?.publishedVersion?.id ||
      profileQuery.data?.draftVersion?.id ||
      profileVersions[0]?.id ||
      ''
    if (preferred) setProfileVersionId(preferred)
  }, [profileQuery.data, profileVersionId, profileVersions])

  const needsDataset = mode === 'dataset_scripted' || mode === 'dataset_live'
  const needsFixture = mode === 'dataset_scripted'
  const needsLiveModel = mode === 'dataset_live'

  const canStart = Boolean(
    versionId &&
      packageId &&
      prompt.trim() &&
      locale.trim() &&
      profileVersionId &&
      (!needsDataset || datasetVersionId) &&
      (!needsFixture || providerFixtureRevision) &&
      (!needsLiveModel || liveModelId.trim()),
  )

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
    pollTicksRef.current = 0
  }, [])

  const stopStream = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  useEffect(
    () => () => {
      stopPolling()
      stopStream()
      generationRef.current += 1
    },
    [stopPolling, stopStream],
  )

  const loadTerminalEvidence = useCallback(async (runId: string) => {
    try {
      const [latest, cases, ev] = await Promise.all([
        getEvalRun(runId),
        listEvalRunCaseResults(runId),
        listEvalRunEvidence(runId),
      ])
      reconcileRun(latest)
      setCaseResults(cases.items)
      setEvidence(ev)
    } catch (error) {
      markError(mapSkillPackageError(error).message)
    }
  }, [markError, reconcileRun])

  const startPollingFallback = useCallback(
    (runId: string, generation: number) => {
      stopPolling()
      setTransportMode('polling')
      pollTicksRef.current = 0
      const tick = async () => {
        if (generation !== generationRef.current) return
        pollTicksRef.current += 1
        if (pollTicksRef.current > POLL_MAX_TICKS) {
          stopPolling()
          setTransportMode('closed')
          return
        }
        try {
          const seq = useSkillTestRunStore.getState().lastSequence
          const page = await listEvalRunEvents(runId, { afterSequence: seq, limit: 100 })
          if (page.items.length) ingestEvents(runId, page.items)
          const latest = await getEvalRun(runId)
          reconcileRun(latest)
          if (['completed', 'failed', 'cancelled'].includes(latest.status)) {
            stopPolling()
            setTransportMode('closed')
            await loadTerminalEvidence(runId)
          }
        } catch (error) {
          markError(mapSkillPackageError(error).message)
          stopPolling()
          setTransportMode('closed')
        }
      }
      void tick()
      pollRef.current = window.setInterval(() => {
        void tick()
      }, POLL_INTERVAL_MS)
    },
    [
      ingestEvents,
      loadTerminalEvidence,
      markError,
      reconcileRun,
      setTransportMode,
      stopPolling,
    ],
  )

  const attachEventStream = useCallback(
    async (runId: string, generation: number) => {
      stopPolling()
      stopStream()
      const controller = new AbortController()
      abortRef.current = controller
      setTransportMode('sse')

      const afterSequence = useSkillTestRunStore.getState().lastSequence
      const reason = await streamEvalRunEvents(runId, {
        afterSequence,
        signal: controller.signal,
        onEvent: (event) => {
          if (generation !== generationRef.current) return
          ingestEvents(runId, [event])
        },
        onHeartbeat: (payload) => {
          if (generation !== generationRef.current) return
          ingestHeartbeat(runId, payload)
        },
        onError: (error) => {
          if (generation !== generationRef.current) return
          // Transport errors fall through to polling; do not hard-fail yet.
          if (controller.signal.aborted) return
          markError(error.message)
        },
      })

      if (generation !== generationRef.current) return

      if (reason === 'aborted') {
        setTransportMode('closed')
        return
      }

      if (reason === 'transport_failure') {
        startPollingFallback(runId, generation)
        return
      }

      // Stream closed cleanly — fetch terminal run + evidence.
      setTransportMode('closed')
      await loadTerminalEvidence(runId)
    },
    [
      ingestEvents,
      ingestHeartbeat,
      loadTerminalEvidence,
      markError,
      setTransportMode,
      startPollingFallback,
      stopPolling,
      stopStream,
    ],
  )

  async function handleStart() {
    if (!versionId) {
      setLocalError(t('settings.universalSkills.workbenchNeedsDraft'))
      return
    }
    if (!canStart) {
      setLocalError(t('settings.universalSkills.workbenchInvalidInputs'))
      return
    }

    setLocalError(null)
    setBusy(true)
    setCaseResults([])
    setEvidence(null)
    stopPolling()
    stopStream()
    generationRef.current += 1
    const generation = generationRef.current
    reset()

    try {
      const request: CreateEvalRunRequest = {
        requestId: newRequestId('eval'),
        subjectKind,
        subjectAggregateId: packageId,
        subjectVersionId: versionId,
        prompt: prompt.trim(),
        locale: locale.trim(),
        profileVersionId,
        mode,
        datasetVersionIds: needsDataset && datasetVersionId ? [datasetVersionId] : [],
        providerFixtureRevision: needsFixture ? providerFixtureRevision : null,
        liveModelId: needsLiveModel ? liveModelId.trim() : null,
      }
      const created = await createEvalRun(request)
      if (generation !== generationRef.current) return
      beginRun(created)
      void attachEventStream(created.id, generation)
    } catch (error) {
      setLocalError(mapSkillPackageError(error).message)
      markError(mapSkillPackageError(error).message)
    } finally {
      setBusy(false)
    }
  }

  async function handleCancel() {
    if (!activeRunId) return
    const revision = run?.stateRevision
    if (revision == null) {
      setLocalError(t('settings.universalSkills.workbenchMissingRevision'))
      return
    }
    setBusy(true)
    markCancelRequested()
    try {
      const cancelled = await cancelEvalRun(activeRunId, {
        requestId: newRequestId('cancel'),
        expectedStateRevision: revision,
      })
      reconcileRun(cancelled)
      // Keep stream/poll alive until terminal status lands, then evidence loads.
    } catch (error) {
      setLocalError(mapSkillPackageError(error).message)
    } finally {
      setBusy(false)
    }
  }

  const running = status === 'queued' || status === 'running' || status === 'cancelling'
  const datasetOptions = datasetVersionsQuery.data ?? []

  return (
    <div className={cn('space-y-4', className)}>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span>{t('settings.universalSkills.evalPrompt')}</span>
          <textarea
            className={cn(uiField.input, 'min-h-[72px]')}
            value={prompt}
            disabled={running || busy}
            aria-label={t('settings.universalSkills.evalPrompt')}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>
        <div className="space-y-3">
          <label className="block space-y-1 text-sm">
            <span>{t('settings.universalSkills.evalLocale')}</span>
            <input
              className={uiField.input}
              value={locale}
              disabled={running || busy}
              aria-label={t('settings.universalSkills.evalLocale')}
              onChange={(e) => setLocale(e.target.value)}
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span>{t('settings.universalSkills.profileVersion')}</span>
            <select
              className={uiField.select}
              value={profileVersionId}
              disabled={running || busy || profileVersions.length === 0}
              aria-label={t('settings.universalSkills.profileVersion')}
              onChange={(e) => setProfileVersionId(e.target.value)}
            >
              {profileVersions.length === 0 ? (
                <option value="">{t('settings.universalSkills.loadingProfiles')}</option>
              ) : (
                profileVersions.map((version) => (
                  <option key={version.id} value={version.id}>
                    {version.versionName} · {version.id.slice(0, 8)}
                  </option>
                ))
              )}
            </select>
          </label>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-sm">
          <span>{t('settings.universalSkills.evalMode')}</span>
          <select
            className={uiField.select}
            value={mode}
            disabled={running || busy}
            aria-label={t('settings.universalSkills.evalMode')}
            onChange={(e) => {
              const next = e.target.value as EvalRunMode
              setMode(next)
              if (next === 'interactive_scripted') {
                setDatasetVersionId('')
                setLiveModelId('')
              }
              if (next === 'dataset_live') {
                setProviderFixtureRevision('')
              }
              if (next === 'dataset_scripted' && !providerFixtureRevision) {
                setProviderFixtureRevision(BUILTIN_FIXTURES[0])
              }
            }}
          >
            <option value="interactive_scripted">interactive_scripted</option>
            <option value="dataset_scripted">dataset_scripted</option>
            <option value="dataset_live">dataset_live</option>
          </select>
        </label>

        {needsDataset ? (
          <label className="space-y-1 text-sm">
            <span>{t('settings.universalSkills.datasetVersion')}</span>
            <select
              className={uiField.select}
              value={datasetVersionId}
              disabled={running || busy || datasetOptions.length === 0}
              aria-label={t('settings.universalSkills.datasetVersion')}
              onChange={(e) => setDatasetVersionId(e.target.value)}
            >
              <option value="">{t('settings.universalSkills.selectDatasetVersion')}</option>
              {datasetOptions.map((version) => (
                <option key={version.id} value={version.id}>
                  {version.versionName} · {version.id.slice(0, 8)}
                  {version.caseCount ? ` · ${version.caseCount} cases` : ''}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {needsFixture ? (
          <label className="space-y-1 text-sm">
            <span>{t('settings.universalSkills.providerFixture')}</span>
            <select
              className={uiField.select}
              value={providerFixtureRevision}
              disabled={running || busy}
              aria-label={t('settings.universalSkills.providerFixture')}
              onChange={(e) => setProviderFixtureRevision(e.target.value)}
            >
              {BUILTIN_FIXTURES.map((fixture) => (
                <option key={fixture} value={fixture}>
                  {fixture}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {needsLiveModel ? (
          <label className="space-y-1 text-sm">
            <span>{t('settings.universalSkills.liveModelId')}</span>
            <input
              className={uiField.input}
              value={liveModelId}
              disabled={running || busy}
              placeholder="model-uuid"
              aria-label={t('settings.universalSkills.liveModelId')}
              onChange={(e) => setLiveModelId(e.target.value)}
            />
          </label>
        ) : null}

        <Button
          type="button"
          disabled={busy || running || !canStart}
          onClick={() => void handleStart()}
        >
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
        transportMode={transportMode}
      />

      <SkillEvaluationEvidence
        run={run}
        caseResults={caseResults}
        evidence={evidence}
        metrics={metrics}
        assertions={assertions}
      />
    </div>
  )
}
