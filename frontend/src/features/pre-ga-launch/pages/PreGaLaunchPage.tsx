import { useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, Clock3, Loader2, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { isApiError } from '@/lib/api/client'
import { useOperatorSessionQuery } from '@/features/operator-auth'
import {
  classifyLaunchStatus,
  classifyLaunchCandidate,
  type EvidenceRef,
  type LaunchCandidate,
  type QualificationTargetSummary,
} from '../api/launch'
import {
  useConsumePreGaLaunchCandidateMutation,
  useCreatePreGaLaunchCandidateMutation,
  usePreGaLaunchCandidatesQuery,
  usePreGaLaunchQualificationTargetQuery,
  usePreGaLaunchStatusQuery,
} from '../queries'
import {
  SettingsBadge,
  SettingsInset,
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'

const DIGEST = /^[0-9a-f]{64}$/

function requestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  const hex = () => Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0')
  return `${hex()}-${hex().slice(0, 4)}-4${hex().slice(0, 3)}-8${hex().slice(0, 3)}-${hex()}${hex().slice(0, 4)}`
}

function digestPrefix(value: string | null | undefined): string {
  return value ? `${value.slice(0, 12)}…` : '—'
}

function errorCopy(error: unknown, t: (key: string) => string): string {
  if (isApiError(error)) {
    if (error.status === 401) return t('preGaLaunch.errors.sessionExpired')
    if (error.status === 403) return t('preGaLaunch.errors.forbidden')
    if (error.status === 409) return t('preGaLaunch.errors.conflict')
    if (error.status === 422) return t('preGaLaunch.errors.invalid')
    if (error.status === 503) return t('preGaLaunch.errors.unavailable')
  }
  return t('preGaLaunch.errors.generic')
}

function CandidateRow({ candidate }: { candidate: LaunchCandidate }) {
  const { t } = useTranslation()
  const state = classifyLaunchCandidate(candidate)
  return (
    <li className="rounded-2xl border border-border/70 bg-background/80 p-4" data-testid="launch-candidate">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium text-foreground">{digestPrefix(candidate.subjectDigest)}</p>
          <p className="mt-1 text-xs text-muted-foreground">{candidate.buildRevision}</p>
        </div>
        <SettingsBadge className={candidate.passed ? 'text-emerald-700' : 'text-destructive'}>
          {t(`preGaLaunch.candidateStates.${state}`)}
        </SettingsBadge>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
        <span>{t('preGaLaunch.fields.candidate')}: {digestPrefix(candidate.candidateId)}</span>
        <span>{t('preGaLaunch.fields.target')}: {digestPrefix(candidate.qualificationTargetDigest)}</span>
        <span>{t('preGaLaunch.fields.evidence')}: {digestPrefix(candidate.automatedEvidenceManifestDigest)}</span>
        <span>{t('preGaLaunch.fields.rehearsalEvidence')}: {digestPrefix(candidate.rehearsalEvidenceManifestDigest)}</span>
        <span>{t('preGaLaunch.fields.runtimeClosure')}: {digestPrefix(candidate.runtimeClosureDigest)}</span>
        <span>{t('preGaLaunch.fields.issuedAt')}: {candidate.issuedAt ?? '—'}</span>
        <span>{t('preGaLaunch.fields.expiresAt')}: {candidate.expiresAt ?? '—'}</span>
        <span>{t('preGaLaunch.fields.usedAt')}: {candidate.usedAt ?? '—'}</span>
        <span>{t('preGaLaunch.fields.matchesCurrent')}: {candidate.active ? t('common.yes') : t('common.no')}</span>
        <span>{t('preGaLaunch.fields.unknownCalls')}: {candidate.unknownCallCount}</span>
        <span>{t('preGaLaunch.fields.reconciliation')}: {candidate.needsReconciliationCount}</span>
        <span>{t('preGaLaunch.fields.activeRuns')}: {candidate.activeRunCount}</span>
      </div>
      {candidate.failureCodes.length > 0 ? (
        <ul className="mt-3 space-y-1 text-xs text-destructive" aria-label={t('preGaLaunch.fields.failureCodes')}>
          {candidate.failureCodes.map((code) => <li key={code}>{code}</li>)}
        </ul>
      ) : null}
    </li>
  )
}

function TargetIdentitySummary({ target }: { target: QualificationTargetSummary }) {
  const { t } = useTranslation()
  const fields: Array<[string, string]> = [
    ['buildRevision', target.buildRevision],
    ['deploymentClass', target.productionSchemaDeploymentClass],
    ['schemaRevision', target.schemaRevision],
    ['runtimeIdentity', digestPrefix(target.productionSchemaRuntimeIdentityDigest)],
    ['imageSet', digestPrefix(target.imageSetDigest)],
    ['deployedArtifacts', digestPrefix(target.deployedArtifactSetDigest)],
    ['rollout', digestPrefix(target.rolloutRevisionId)],
    ['profile', digestPrefix(target.profileVersionId)],
    ['model', digestPrefix(target.modelId)],
    ['runtimeClosure', digestPrefix(target.runtimeClosureDigest)],
    ['dependencyLocks', digestPrefix(target.dependencyLockSetDigest)],
    ['scenarioSet', digestPrefix(target.scenarioSetDigest)],
    ['requiredAssertions', digestPrefix(target.requiredAssertionSetDigest)],
    ['runner', digestPrefix(target.runnerIdentityDigest)],
    ['trustSet', digestPrefix(target.evidenceTrustSetDigest)],
    ['qualificationTarget', digestPrefix(target.qualificationTargetDigest)],
  ]
  return (
    <div data-testid="qualification-target-summary">
      <SettingsSection className="space-y-5">
        <SettingsSectionHeader
          title={t('preGaLaunch.target.title')}
          description={t('preGaLaunch.target.description')}
        />
        <SettingsInset className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {fields.map(([field, value]) => (
            <div key={field}>
              <p className="text-xs text-muted-foreground">{t(`preGaLaunch.target.fields.${field}`)}</p>
              <p className="mt-1 break-all font-mono text-xs text-foreground">{value}</p>
            </div>
          ))}
        </SettingsInset>
      </SettingsSection>
    </div>
  )
}

export function PreGaLaunchPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const session = useOperatorSessionQuery()
  const statusQuery = usePreGaLaunchStatusQuery()
  const targetQuery = usePreGaLaunchQualificationTargetQuery()
  const candidatesQuery = usePreGaLaunchCandidatesQuery()
  const createMutation = useCreatePreGaLaunchCandidateMutation()
  const consumeMutation = useConsumePreGaLaunchCandidateMutation()
  const [automatedManifest, setAutomatedManifest] = useState('')
  const [automatedAttestation, setAutomatedAttestation] = useState('')
  const [rehearsalManifest, setRehearsalManifest] = useState('')
  const [rehearsalAttestation, setRehearsalAttestation] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const isOperator = session.data?.role === 'operator'
  const currentCandidate = statusQuery.data?.candidate ?? null
  const canConsume = Boolean(
    isOperator &&
    currentCandidate &&
    // Candidate expiry is evaluated by the server during the CAS mutation;
    // the browser clock must not suppress an authoritative consume attempt.
    currentCandidate.passed &&
    currentCandidate.usedAt === null &&
    currentCandidate.active === false,
  )
  const [submittedCandidate, setSubmittedCandidate] = useState<LaunchCandidate | null>(null)
  const [createRequestId, setCreateRequestId] = useState<string | null>(null)
  const [createRequestFingerprint, setCreateRequestFingerprint] = useState<string | null>(null)
  const [consumeRequestId, setConsumeRequestId] = useState<string | null>(null)
  const [consumeRequestFingerprint, setConsumeRequestFingerprint] = useState<string | null>(null)
  const latestCandidates = useMemo(() => {
    const items = candidatesQuery.data?.pages.flatMap((page) => page.items) ?? []
    if (!submittedCandidate) return items
    return [submittedCandidate, ...items.filter((item) => item.candidateId !== submittedCandidate.candidateId)]
  }, [candidatesQuery.data?.pages, submittedCandidate])

  function handleMutationFailure(mutationError: unknown) {
    if (isApiError(mutationError) && mutationError.status === 401) navigate('/login')
    if (isApiError(mutationError) && mutationError.status === 409) {
      void statusQuery.refetch?.()
      void candidatesQuery.refetch?.()
    }
    setError(errorCopy(mutationError, t))
  }

  function evidenceRef(kind: EvidenceRef['evidenceKind'], manifestDigest: string, attestationDigest: string): EvidenceRef {
    return { schemaVersion: 1, evidenceKind: kind, manifestDigest, attestationDigest }
  }

  async function createCandidate() {
    if (!isOperator || createMutation.isPending) return
    if (![automatedManifest, automatedAttestation, rehearsalManifest, rehearsalAttestation].every((value) => DIGEST.test(value))) {
      setError(t('preGaLaunch.errors.invalid'))
      return
    }
    if (!reason.trim() || reason.trim().length > 500) {
      setError(t('preGaLaunch.errors.invalid'))
      return
    }
    setError(null)
    const bodyFingerprint = JSON.stringify({ automatedManifest, automatedAttestation, rehearsalManifest, rehearsalAttestation, reason: reason.trim() })
    const nextRequestId = createRequestId && createRequestFingerprint === bodyFingerprint ? createRequestId : requestId()
    setCreateRequestId(nextRequestId)
    setCreateRequestFingerprint(bodyFingerprint)
    try {
      const candidate = await createMutation.mutateAsync({
        automatedEvidenceRef: evidenceRef('automated_qualification', automatedManifest, automatedAttestation),
        rehearsalEvidenceRef: evidenceRef('production_rehearsal', rehearsalManifest, rehearsalAttestation),
        requestId: nextRequestId,
        reason: reason.trim(),
      })
      setSubmittedCandidate(candidate)
      setCreateRequestId(null)
      setCreateRequestFingerprint(null)
      setReason('')
    } catch (mutationError) {
      handleMutationFailure(mutationError)
    }
  }

  async function consumeCandidate() {
    if (!currentCandidate || !statusQuery.data || !canConsume || consumeMutation.isPending) return
    setError(null)
    const consumeReason = reason.trim() || t('preGaLaunch.defaultConsumeReason')
    const bodyFingerprint = JSON.stringify({
      candidateId: currentCandidate.candidateId,
      expectedControlRevision: statusQuery.data.controlRevision,
      reason: consumeReason,
    })
    const nextRequestId = consumeRequestId && consumeRequestFingerprint === bodyFingerprint ? consumeRequestId : requestId()
    setConsumeRequestId(nextRequestId)
    setConsumeRequestFingerprint(bodyFingerprint)
    try {
      const result = await consumeMutation.mutateAsync({
        candidateId: currentCandidate.candidateId,
        input: {
          expectedControlRevision: statusQuery.data.controlRevision,
          requestId: nextRequestId,
          reason: consumeReason,
        },
      })
      if (result.candidate) setSubmittedCandidate(result.candidate)
      setConsumeRequestId(null)
      setConsumeRequestFingerprint(null)
      setReason('')
    } catch (mutationError) {
      handleMutationFailure(mutationError)
    }
  }

  return (
    <SettingsPageShell className="space-y-6">
      <SettingsPageHeader
        title={t('preGaLaunch.title')}
        description={t('preGaLaunch.description')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />

      {statusQuery.isLoading ? <div role="status" className="text-sm text-muted-foreground"><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />{t('preGaLaunch.loading')}</div> : null}
      {statusQuery.isError ? <div role="alert" className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{errorCopy(statusQuery.error, t)}</div> : null}
      {targetQuery.isError ? <div role="alert" className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{errorCopy(targetQuery.error, t)}</div> : null}
      {error ? <div role="alert" className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{error}</div> : null}

      {statusQuery.data ? (
        <SettingsSection className="space-y-5">
          <SettingsSectionHeader title={t('preGaLaunch.status.title')} description={t('preGaLaunch.status.description')} />
          <SettingsInset className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.state')}</p><p className="mt-1 font-medium">{t(`preGaLaunch.states.${classifyLaunchStatus(statusQuery.data)}`)}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.controlRevision')}</p><p className="mt-1 font-medium">{statusQuery.data.controlRevision}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.subject')}</p><p className="mt-1 font-mono text-xs">{digestPrefix(statusQuery.data.activeSubjectDigest)}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.reason')}</p><p className="mt-1 font-medium">{statusQuery.data.reasonCode ?? t('preGaLaunch.states.current')}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.activeCandidate')}</p><p className="mt-1 font-mono text-xs">{digestPrefix(statusQuery.data.activeCandidateId)}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.activeUse')}</p><p className="mt-1 font-mono text-xs">{digestPrefix(statusQuery.data.activeGateUseId)}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.launchedAt')}</p><p className="mt-1 font-mono text-xs">{statusQuery.data.launchedAt ?? '—'}</p></div>
            <div><p className="text-xs text-muted-foreground">{t('preGaLaunch.fields.updatedAt')}</p><p className="mt-1 font-mono text-xs">{statusQuery.data.updatedAt ?? '—'}</p></div>
          </SettingsInset>
          {classifyLaunchStatus(statusQuery.data) === 'current' ? <p className="flex items-start gap-2 text-sm text-emerald-700"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />{t('preGaLaunch.status.currentHelp')}</p> : <p className="flex items-start gap-2 text-sm text-muted-foreground"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />{t(`preGaLaunch.status.${classifyLaunchStatus(statusQuery.data)}Help`)}</p>}
        </SettingsSection>
      ) : null}

      {targetQuery.data ? <TargetIdentitySummary target={targetQuery.data} /> : null}

      <SettingsSection className="space-y-5">
        <SettingsSectionHeader title={t('preGaLaunch.create.title')} description={t('preGaLaunch.create.description')} />
        <div className="grid gap-4 md:grid-cols-2">
          {([
            ['automatedManifest', automatedManifest, setAutomatedManifest, 'preGaLaunch.create.automatedManifest'],
            ['automatedAttestation', automatedAttestation, setAutomatedAttestation, 'preGaLaunch.create.automatedAttestation'],
            ['rehearsalManifest', rehearsalManifest, setRehearsalManifest, 'preGaLaunch.create.rehearsalManifest'],
            ['rehearsalAttestation', rehearsalAttestation, setRehearsalAttestation, 'preGaLaunch.create.rehearsalAttestation'],
          ] as const).map(([id, value, setter, labelKey]) => (
            <label key={id} className="space-y-1.5 text-sm">
              <span className="font-medium text-foreground">{t(labelKey)}</span>
              <input aria-label={t(labelKey)} value={value} onChange={(event) => setter(event.target.value)} className="h-10 w-full rounded-xl border border-border bg-background px-3 font-mono text-xs outline-none focus:ring-2 focus:ring-primary/20" maxLength={64} />
            </label>
          ))}
        </div>
        <label className="block space-y-1.5 text-sm"><span className="font-medium text-foreground">{t('preGaLaunch.create.reason')}</span><textarea aria-label={t('preGaLaunch.create.reason')} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={500} rows={3} className="w-full rounded-xl border border-border bg-background px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20" /></label>
        <div data-testid="create-request-id" className="rounded-xl border border-border/60 bg-background/60 p-3 text-xs">
          <span className="text-muted-foreground">{t('preGaLaunch.fields.requestId')}: </span>
          <code className="font-mono text-foreground">{createRequestId ?? t('preGaLaunch.requestIdPending')}</code>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" onClick={() => void createCandidate()} disabled={!isOperator || createMutation.isPending}>
            {createMutation.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />}
            {t(isOperator ? 'preGaLaunch.create.submit' : 'preGaLaunch.operatorOnly')}
          </Button>
          {currentCandidate && statusQuery.data && canConsume ? (
            <>
              <div data-testid="consume-confirmation" className="w-full rounded-xl border border-border/60 bg-background/60 p-3 text-xs sm:w-auto sm:min-w-[280px]">
                <p><span className="text-muted-foreground">{t('preGaLaunch.fields.candidate')}: </span><code className="font-mono">{digestPrefix(currentCandidate.candidateId)}</code></p>
                <p><span className="text-muted-foreground">{t('preGaLaunch.fields.subject')}: </span><code className="font-mono">{digestPrefix(currentCandidate.subjectDigest)}</code></p>
                <p><span className="text-muted-foreground">{t('preGaLaunch.fields.controlRevision')}: </span>{statusQuery.data.controlRevision}</p>
                <p><span className="text-muted-foreground">{t('preGaLaunch.fields.expiresAt')}: </span><code className="font-mono">{currentCandidate.expiresAt ?? '—'}</code></p>
                <p><span className="text-muted-foreground">{t('preGaLaunch.fields.requestId')}: </span><code className="font-mono">{consumeRequestId ?? t('preGaLaunch.requestIdPending')}</code></p>
              </div>
              <Button type="button" variant="secondary" onClick={() => void consumeCandidate()} disabled={consumeMutation.isPending}><Clock3 />{t('preGaLaunch.consume.submit')}</Button>
            </>
          ) : null}
        </div>
      </SettingsSection>

      <SettingsSection className="space-y-5">
        <SettingsSectionHeader title={t('preGaLaunch.history.title')} description={t('preGaLaunch.history.description')} />
        {candidatesQuery.isLoading ? <div role="status" className="text-sm text-muted-foreground">{t('preGaLaunch.loading')}</div> : null}
        {candidatesQuery.isError ? <div role="alert" className="text-sm text-destructive">{errorCopy(candidatesQuery.error, t)}</div> : null}
        {latestCandidates.length > 0 ? <ul className="space-y-3">{latestCandidates.map((candidate) => <CandidateRow key={candidate.candidateId} candidate={candidate} />)}</ul> : <p className="text-sm text-muted-foreground">{t('preGaLaunch.history.empty')}</p>}
        {candidatesQuery.hasNextPage ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => void candidatesQuery.fetchNextPage()}
            disabled={candidatesQuery.isFetchingNextPage}
          >
            {candidatesQuery.isFetchingNextPage ? <Loader2 className="animate-spin" /> : null}
            {t('preGaLaunch.history.loadMore')}
          </Button>
        ) : null}
      </SettingsSection>
    </SettingsPageShell>
  )
}
