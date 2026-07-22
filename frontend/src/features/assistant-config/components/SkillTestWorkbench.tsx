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

export type WorkbenchSubjectKind =
  | 'skill_draft'
  | 'skill_version'
  | 'main_agent_profile_draft'
  | 'main_agent_profile_version'

export interface SkillTestWorkbenchProps {
  /** Subject aggregate id: skill package id or main-agent profile id. */
  packageId: string
  versionId: string | null
  subjectKind?: WorkbenchSubjectKind
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
const SSE_MAX_RECONNECTS = 2
/** Backoff before reconnect attempt n (1-based): 250ms, 500ms, then 1000ms cap. */
export function sseReconnectDelayMs(attempt: number): number {
  const n = Math.max(1, Math.floor(attempt))
  return Math.min(1000, 250 * 2 ** (n - 1))
}
const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled'])

function deriveAggregateMetrics(input: {
  caseResults: CaseResultSummary[]
  evidence: EvalRunEvidence | null
  existing?: Record<string, unknown>
}): Record<string, unknown> {
  // Non-authoritative client chrome only — used when server metrics are absent.
  const metrics: Record<string, unknown> = { ...(input.existing || {}) }
  const cases = input.caseResults
  if (cases.length > 0) {
    let rounds = 0
    let calls = 0
    let tokens = 0
    let latencyMs = 0
    let passed = 0
    let failed = 0
    for (const row of cases) {
      if (typeof row.rounds === 'number') rounds += row.rounds
      if (typeof row.calls === 'number') calls += row.calls
      if (typeof row.tokens === 'number') tokens += row.tokens
      if (typeof row.latencyMs === 'number') latencyMs += row.latencyMs
      const state = (row.resultState || '').toLowerCase()
      if (state.includes('pass') || state === 'completed') passed += 1
      if (state.includes('fail') || state.includes('error')) failed += 1
    }
    metrics.caseCount = cases.length
    metrics.passedCount = passed
    metrics.failedCount = failed
    if (rounds) metrics.rounds = rounds
    if (calls) metrics.calls = calls
    if (tokens) metrics.tokens = tokens
    if (latencyMs) metrics.latencyMs = latencyMs
  }
  if (input.evidence?.capabilityCalls?.length) {
    metrics.capabilityCallCount = input.evidence.capabilityCalls.length
  }
  if (input.evidence?.artifacts?.length) {
    metrics.artifactCount = input.evidence.artifacts.length
  }
  if (input.evidence?.evidenceProvenance) {
    metrics.evidenceProvenance = input.evidence.evidenceProvenance
  }
  return metrics
}

function hasServerAggregateMetrics(
  metrics: Record<string, unknown> | null | undefined,
): metrics is Record<string, unknown> {
  return !!metrics && typeof metrics === 'object' && Object.keys(metrics).length > 0
}

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
  // Empty = structural_synthetic default for interactive; pin enables real_orchestration.
  const [providerFixtureRevision, setProviderFixtureRevision] = useState<string>('')
  const [liveModelId, setLiveModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [caseResults, setCaseResults] = useState<CaseResultSummary[]>([])
  const [evidence, setEvidence] = useState<EvalRunEvidence | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const pollRef = useRef<number | null>(null)
  const pollTicksRef = useRef(0)
  const pollInFlightRef = useRef(false)
  const generationRef = useRef(0)
  const sseReconnectsRef = useRef(0)
  const sseReconnectTimerRef = useRef<number | null>(null)
  const cancelInFlightRef = useRef(false)

  const status = useSkillTestRunStore((s) => s.status)
  const activeRunId = useSkillTestRunStore((s) => s.activeRunId)
  const lastSequence = useSkillTestRunStore((s) => s.lastSequence)
  const events = useSkillTestRunStore((s) => s.events)
  const run = useSkillTestRunStore((s) => s.run)
  const metrics = useSkillTestRunStore((s) => s.metrics)
  const assertions = useSkillTestRunStore((s) => s.assertions)
  const transportMode = useSkillTestRunStore((s) => s.transportMode)
  const errorMessage = useSkillTestRunStore((s) => s.errorMessage)
  const cancelAttempt = useSkillTestRunStore((s) => s.cancelAttempt)
  const beginRun = useSkillTestRunStore((s) => s.beginRun)
  const ingestEvents = useSkillTestRunStore((s) => s.ingestEvents)
  const ingestHeartbeat = useSkillTestRunStore((s) => s.ingestHeartbeat)
  const setTransportMode = useSkillTestRunStore((s) => s.setTransportMode)
  const setMetrics = useSkillTestRunStore((s) => s.setMetrics)
  const reconcileRun = useSkillTestRunStore((s) => s.reconcileRun)
  const markCancelRequested = useSkillTestRunStore((s) => s.markCancelRequested)
  const pinCancelAttempt = useSkillTestRunStore((s) => s.pinCancelAttempt)
  const markError = useSkillTestRunStore((s) => s.markError)
  const markTransportNotice = useSkillTestRunStore((s) => s.markTransportNotice)
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
  // interactive_scripted: fixture is optional (empty → structural_synthetic default).
  const offersFixture = mode === 'dataset_scripted' || mode === 'interactive_scripted'
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

  const clearSseReconnectTimer = useCallback(() => {
    if (sseReconnectTimerRef.current != null) {
      window.clearTimeout(sseReconnectTimerRef.current)
      sseReconnectTimerRef.current = null
    }
  }, [])

  const stopStream = useCallback(() => {
    clearSseReconnectTimer()
    abortRef.current?.abort()
    abortRef.current = null
  }, [clearSseReconnectTimer])

  useEffect(
    () => () => {
      stopPolling()
      stopStream()
      generationRef.current += 1
    },
    [stopPolling, stopStream],
  )

  const loadTerminalEvidence = useCallback(
    async (runId: string, generation: number) => {
      if (generation !== generationRef.current) return
      try {
        const [latest, cases, ev] = await Promise.all([
          getEvalRun(runId),
          listEvalRunCaseResults(runId),
          listEvalRunEvidence(runId),
        ])
        // Superseded stream must not overwrite newer run evidence.
        if (generation !== generationRef.current) return
        const active = useSkillTestRunStore.getState().activeRunId
        if (active && active !== runId) return

        reconcileRun(latest)
        setCaseResults(cases.items)
        setEvidence(ev)
        // Prefer server aggregate_metrics after terminal evidence arrives.
        // Client deriveAggregateMetrics is non-authoritative chrome only when
        // the run row has no server metrics yet.
        if (hasServerAggregateMetrics(latest.aggregateMetrics)) {
          setMetrics({ ...latest.aggregateMetrics })
        } else {
          const derived = deriveAggregateMetrics({
            caseResults: cases.items,
            evidence: ev,
            existing: useSkillTestRunStore.getState().metrics,
          })
          setMetrics(derived)
        }
      } catch (error) {
        if (generation !== generationRef.current) return
        markError(mapSkillPackageError(error).message)
      }
    },
    [markError, reconcileRun, setMetrics],
  )

  const startPollingFallback = useCallback(
    (runId: string, generation: number) => {
      stopPolling()
      setTransportMode('polling')
      pollTicksRef.current = 0
      pollInFlightRef.current = false
      // Keep run status non-terminal; only note transport degradation.
      markTransportNotice(t('settings.universalSkills.workbenchTransportFallback'))

      const tick = async () => {
        if (generation !== generationRef.current) return
        if (pollInFlightRef.current) return
        pollInFlightRef.current = true
        pollTicksRef.current += 1
        try {
          if (pollTicksRef.current > POLL_MAX_TICKS) {
            stopPolling()
            setTransportMode('closed')
            markTransportNotice(t('settings.universalSkills.workbenchPollTimeout'))
            return
          }
          const seq = useSkillTestRunStore.getState().lastSequence
          const page = await listEvalRunEvents(runId, { afterSequence: seq, limit: 100 })
          if (generation !== generationRef.current) return
          if (page.items.length) ingestEvents(runId, page.items)
          const latest = await getEvalRun(runId)
          if (generation !== generationRef.current) return
          reconcileRun(latest)
          if (TERMINAL_RUN_STATUSES.has(latest.status)) {
            stopPolling()
            setTransportMode('closed')
            markTransportNotice(null)
            await loadTerminalEvidence(runId, generation)
          }
        } catch (error) {
          if (generation !== generationRef.current) return
          // Keep cancel available; only fail hard if we cannot observe the run at all.
          markTransportNotice(mapSkillPackageError(error).message)
          stopPolling()
          setTransportMode('closed')
        } finally {
          pollInFlightRef.current = false
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
      markTransportNotice,
      reconcileRun,
      setTransportMode,
      stopPolling,
      t,
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
          // Transport errors fall through to reconnect/polling; do not mark terminal error.
          if (controller.signal.aborted) return
          markTransportNotice(error.message)
        },
      })

      if (generation !== generationRef.current) return

      if (reason === 'aborted') {
        setTransportMode('closed')
        return
      }

      const scheduleReconnect = () => {
        if (sseReconnectsRef.current >= SSE_MAX_RECONNECTS) {
          startPollingFallback(runId, generation)
          return
        }
        sseReconnectsRef.current += 1
        const delayMs = sseReconnectDelayMs(sseReconnectsRef.current)
        markTransportNotice(t('settings.universalSkills.workbenchSseReconnecting'))
        clearSseReconnectTimer()
        sseReconnectTimerRef.current = window.setTimeout(() => {
          sseReconnectTimerRef.current = null
          if (generation !== generationRef.current) return
          void attachEventStream(runId, generation)
        }, delayMs)
      }

      if (reason === 'transport_failure') {
        // Prefer a bounded SSE reconnect with backoff before polling.
        scheduleReconnect()
        return
      }

      // Clean body EOF: only treat as terminal when the run is actually terminal.
      try {
        const latest = await getEvalRun(runId)
        if (generation !== generationRef.current) return
        reconcileRun(latest)
        if (TERMINAL_RUN_STATUSES.has(latest.status)) {
          setTransportMode('closed')
          markTransportNotice(null)
          await loadTerminalEvidence(runId, generation)
          return
        }
        // Non-terminal run with EOF — reconnect with backoff or fall back to poll.
        scheduleReconnect()
      } catch (error) {
        if (generation !== generationRef.current) return
        markTransportNotice(mapSkillPackageError(error).message)
        startPollingFallback(runId, generation)
      }
    },
    [
      clearSseReconnectTimer,
      ingestEvents,
      ingestHeartbeat,
      loadTerminalEvidence,
      markTransportNotice,
      reconcileRun,
      setTransportMode,
      startPollingFallback,
      stopPolling,
      stopStream,
      t,
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
    sseReconnectsRef.current = 0
    clearSseReconnectTimer()
    pollInFlightRef.current = false
    reset()

    try {
      // interactive_scripted: optional fixture pin → real_orchestration when set.
      const fixturePin =
        mode === 'dataset_scripted'
          ? providerFixtureRevision
          : mode === 'interactive_scripted'
            ? providerFixtureRevision.trim() || null
            : null
      // Profile subjects pin the evaluated version as the Profile admission pin
      // when the operator has not selected a different profileVersionId.
      const isProfileSubject =
        subjectKind === 'main_agent_profile_draft' ||
        subjectKind === 'main_agent_profile_version'
      const resolvedProfileVersionId =
        profileVersionId.trim() ||
        (isProfileSubject && versionId ? versionId : profileVersionId)
      const request: CreateEvalRunRequest = {
        requestId: newRequestId('eval'),
        subjectKind,
        subjectAggregateId: packageId,
        subjectVersionId: versionId,
        prompt: prompt.trim(),
        locale: locale.trim(),
        profileVersionId: resolvedProfileVersionId,
        mode,
        datasetVersionIds: needsDataset && datasetVersionId ? [datasetVersionId] : [],
        providerFixtureRevision: fixturePin,
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
    if (!activeRunId || busy || cancelInFlightRef.current) return
    const pre = useSkillTestRunStore.getState()
    // Server already in cancelling and we have no local pin to retry — do not mint a new id.
    if (pre.status === 'cancelling' && !pre.cancelAttempt) return
    // Cancel already accepted (no local transport error) — do not re-POST.
    if (pre.status === 'cancelling' && pre.cancelAttempt && !localError) return

    cancelInFlightRef.current = true
    setBusy(true)
    setLocalError(null)
    markCancelRequested()
    try {
      const existing = useSkillTestRunStore.getState().cancelAttempt
      let requestId: string
      let expectedStateRevision: number

      if (existing) {
        // Retry: reuse the original requestId + expectedStateRevision for durable CAS.
        requestId = existing.requestId
        expectedStateRevision = existing.expectedStateRevision
      } else {
        // First click: refresh revision once, pin the pair for any later retry.
        const latest = await getEvalRun(activeRunId)
        if (useSkillTestRunStore.getState().activeRunId !== activeRunId) return
        reconcileRun(latest)
        if (TERMINAL_RUN_STATUSES.has(latest.status)) {
          await loadTerminalEvidence(activeRunId, generationRef.current)
          return
        }
        // Server reconcile already cancelling without our pin — short-circuit (no new id).
        if (latest.status === 'cancelling') {
          return
        }
        const revision = latest.stateRevision
        if (revision == null) {
          setLocalError(t('settings.universalSkills.workbenchMissingRevision'))
          return
        }
        requestId = newRequestId('cancel')
        expectedStateRevision = revision
        pinCancelAttempt({ requestId, expectedStateRevision })
      }

      const cancelled = await cancelEvalRun(activeRunId, {
        requestId,
        expectedStateRevision,
      })
      if (useSkillTestRunStore.getState().activeRunId !== activeRunId) return
      reconcileRun(cancelled)
      // Keep stream/poll alive until terminal status lands, then evidence loads.
    } catch (error) {
      // Keep cancelAttempt so a retry reuses the same requestId + revision.
      setLocalError(mapSkillPackageError(error).message)
    } finally {
      cancelInFlightRef.current = false
      setBusy(false)
    }
  }

  const running = status === 'queued' || status === 'running' || status === 'cancelling'
  // Disable while busy, after accepted cancel (cancelling without retry error), or no active run.
  // After a transport failure, localError + cancelAttempt re-enables Cancel for a durable retry.
  const cancelDisabled =
    !activeRunId ||
    busy ||
    !(status === 'queued' || status === 'running' || (status === 'cancelling' && Boolean(cancelAttempt) && Boolean(localError)))
  const datasetOptions = datasetVersionsQuery.data ?? []

  return (
    <div
      className={cn('space-y-4', className)}
      data-testid="skill-test-workbench"
      data-subject-kind={subjectKind}
    >
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
                // Keep prior fixture pin if any; empty means structural default.
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

        {offersFixture ? (
          <label className="space-y-1 text-sm">
            <span>{t('settings.universalSkills.providerFixture')}</span>
            <select
              className={uiField.select}
              value={providerFixtureRevision}
              disabled={running || busy}
              aria-label={t('settings.universalSkills.providerFixture')}
              onChange={(e) => setProviderFixtureRevision(e.target.value)}
            >
              {mode === 'interactive_scripted' ? (
                <option value="">
                  {t('settings.universalSkills.providerFixtureStructuralDefault')}
                </option>
              ) : null}
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
        <Button
          type="button"
          variant="outline"
          disabled={cancelDisabled}
          onClick={() => void handleCancel()}
        >
          <Square className="mr-1.5 h-4 w-4" />
          {t('settings.universalSkills.cancelEval')}
        </Button>
      </div>

      {localError ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
          {localError}
        </div>
      ) : null}

      {errorMessage && !localError ? (
        <div
          role="status"
          className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm"
        >
          {errorMessage}
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
