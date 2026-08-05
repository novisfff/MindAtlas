/**
 * Main Agent Profile editor (Plan 09 Task 7/10 + Residual 1).
 * Prompt layers, catalog scope, control capabilities, budgets, versions.
 * Two-gate lifecycle: profile_publish (draft) then profile_runtime_enable (published).
 * Distinct draft / publish / promotion / enable / disable commands.
 * Must not embed a single Skill as the execution target.
 * Saving draft never demotes live/published UI state.
 * Client never authors digests/decisions/metrics.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'

import {
  assertNoSingleTargetFields,
  createDefaultProfileSnapshotV2,
  disableProtectedDefaultMainAgentRuntime,
  enableProtectedDefaultMainAgentRuntime,
  getProtectedDefaultMainAgentProfile,
  getProtectedDefaultMainAgentVersion,
  isProfileSnapshotV1,
  listProtectedDefaultMainAgentVersions,
  publishProtectedDefaultMainAgent,
  saveProtectedDefaultMainAgentDraft,
  type MainAgentProfileSnapshotV2,
  type ReadableMainAgentProfileSnapshot,
  type MainAgentProfileSummary,
  type MainAgentProfileVersionSummary,
} from '../api/main-agent-profiles'
import {
  gateUiStateFromResponse,
  listQualifyingEvidence,
  type GateUiState,
  type QualifyingEvidenceSummary,
} from '../api/skill-evaluations'
import { mapSkillPackageError, newRequestId } from '../api/skill-packages'
import { SkillPublishGateDialog } from '../components/SkillPublishGateDialog'
import { SkillTestWorkbench } from '../components/SkillTestWorkbench'
import { useSkillTestRunStore } from '../stores/skill-test-run-store'

const DEFAULT_SNAPSHOT: MainAgentProfileSnapshotV2 = createDefaultProfileSnapshotV2()

type MutationKind = 'draft' | 'publish' | 'enable' | 'disable' | null
type GateDialogMode = 'publish' | 'promotion' | null
type EvalTarget = 'draft' | 'published'

function gateMatches(
  gate: GateUiState | null,
  action: GateUiState['action'],
  subjectVersionId: string | null | undefined,
): boolean {
  if (!gate || !subjectVersionId) return false
  if (gate.action !== action) return false
  if (gate.subjectVersionId !== subjectVersionId) return false
  return gate.decision === 'passed' || gate.decision === 'waived_non_safety'
}

function ProfileEditorBody() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [profile, setProfile] = useState<MainAgentProfileSummary | null>(null)
  const [versions, setVersions] = useState<MainAgentProfileVersionSummary[]>([])
  const [snapshot, setSnapshot] = useState<ReadableMainAgentProfileSnapshot>(DEFAULT_SNAPSHOT)
  const [controlKeysText, setControlKeysText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<MutationKind>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  // Separate action/subject-keyed gate records — never share publish ↔ promotion.
  const [publishGate, setPublishGate] = useState<GateUiState | null>(null)
  const [promotionGate, setPromotionGate] = useState<GateUiState | null>(null)
  const [gateDialogMode, setGateDialogMode] = useState<GateDialogMode>(null)
  const [draftEvidence, setDraftEvidence] = useState<QualifyingEvidenceSummary[]>([])
  const [promotionEvidence, setPromotionEvidence] = useState<QualifyingEvidenceSummary[]>([])
  // Dual-pointer eval target: draft for publish evidence, published for promotion.
  const [evalTarget, setEvalTarget] = useState<EvalTarget>('draft')
  const clearEvalForSubjectChange = useSkillTestRunStore((s) => s.clearForSubjectChange)

  // Live-state UI mirrors server only; draft save never mutates these locally.
  const runtimeEnabled = Boolean(profile?.runtimeEnabled)
  const publishedVersionId = profile?.publishedVersion?.id ?? null
  const draftVersionId = profile?.draftVersion?.id ?? null
  const aggregateRevision = profile?.aggregateRevision ?? 0
  const profileId = profile?.id ?? null
  const isHistoricalV1 = isProfileSnapshotV1(snapshot)
  const readOnly = isHistoricalV1

  const dualPointer = Boolean(draftVersionId && publishedVersionId)
  const workbenchSubjectKind:
    | 'main_agent_profile_draft'
    | 'main_agent_profile_version' = (() => {
    if (dualPointer) {
      return evalTarget === 'published'
        ? 'main_agent_profile_version'
        : 'main_agent_profile_draft'
    }
    if (publishedVersionId && !draftVersionId) return 'main_agent_profile_version'
    return 'main_agent_profile_draft'
  })()
  const workbenchVersionId =
    workbenchSubjectKind === 'main_agent_profile_version'
      ? publishedVersionId
      : draftVersionId

  const publishGateValid = gateMatches(publishGate, 'profile_publish', draftVersionId)
  const promotionGateValid = gateMatches(
    promotionGate,
    'profile_runtime_enable',
    publishedVersionId,
  )

  function handleEvalTargetChange(next: EvalTarget) {
    if (next === evalTarget) return
    setEvalTarget(next)
    clearEvalForSubjectChange()
  }

  const draftQualifyingRunIds = useMemo(
    () => draftEvidence.map((row) => row.evalRunId).filter(Boolean),
    [draftEvidence],
  )
  const promotionQualifyingRunIds = useMemo(
    () => promotionEvidence.map((row) => row.evalRunId).filter(Boolean),
    [promotionEvidence],
  )

  const canEnableRuntime =
    Boolean(publishedVersionId) &&
    promotionGateValid &&
    promotionQualifyingRunIds.length > 0 &&
    !runtimeEnabled &&
    busy === null

  const needsPublishedEvalHint =
    Boolean(publishedVersionId) &&
    !runtimeEnabled &&
    (!promotionGateValid || promotionQualifyingRunIds.length === 0)

  const singleTargetIssues = useMemo(
    () => assertNoSingleTargetFields(snapshot as unknown as Record<string, unknown>),
    [snapshot],
  )

  async function loadQualifyingEvidence(
    subjectKind: 'main_agent_profile_draft' | 'main_agent_profile_version',
    subjectAggregateId: string,
    subjectVersionId: string,
  ): Promise<QualifyingEvidenceSummary[]> {
    const page = await listQualifyingEvidence({
      subjectKind,
      subjectAggregateId,
      subjectVersionId,
      limit: 50,
    })
    return (page.items || []).filter(
      (row) =>
        row.gateEligible &&
        row.evidenceProvenance === 'real_orchestration' &&
        row.subjectKind === subjectKind &&
        row.subjectVersionId === subjectVersionId,
    )
  }

  async function reload() {
    setError(null)
    const summary = await getProtectedDefaultMainAgentProfile()
    setProfile(summary)
    const page = await listProtectedDefaultMainAgentVersions()
    setVersions(page.items || [])
    const versionId = summary.draftVersion?.id || summary.publishedVersion?.id
    if (versionId) {
      const detail = await getProtectedDefaultMainAgentVersion(versionId)
      const snap = detail.snapshot as ReadableMainAgentProfileSnapshot
      if (snap && typeof snap === 'object' && snap.basePrompt) {
        if (isProfileSnapshotV1(snap)) {
          // Historical V1 is display-only; preserve fallbackPolicy for read-only inspection.
          setSnapshot({
            ...snap,
            schemaVersion: 1,
            controlCapabilityKeys: snap.controlCapabilityKeys || [],
            skillCatalogScope: snap.skillCatalogScope || DEFAULT_SNAPSHOT.skillCatalogScope,
            modelRequirements: snap.modelRequirements || DEFAULT_SNAPSHOT.modelRequirements,
            contextBudget: snap.contextBudget || DEFAULT_SNAPSHOT.contextBudget,
            outputBudget: snap.outputBudget || DEFAULT_SNAPSHOT.outputBudget,
            globalSafetyPolicy: snap.globalSafetyPolicy || { denyByDefault: true },
            fallbackPolicy: snap.fallbackPolicy || {
              legacyRuntimeAllowed: false,
              beforeSideEffectsOnly: true,
            },
            responseStyle: snap.responseStyle || {},
            supportedEntrypoints: snap.supportedEntrypoints || ['assistant_chat'],
          })
        } else {
          setSnapshot(
            createDefaultProfileSnapshotV2({
              ...snap,
              schemaVersion: 2,
              controlCapabilityKeys: snap.controlCapabilityKeys || [],
              skillCatalogScope: snap.skillCatalogScope || DEFAULT_SNAPSHOT.skillCatalogScope,
              modelRequirements: snap.modelRequirements || DEFAULT_SNAPSHOT.modelRequirements,
              contextBudget: snap.contextBudget || DEFAULT_SNAPSHOT.contextBudget,
              outputBudget: snap.outputBudget || DEFAULT_SNAPSHOT.outputBudget,
              globalSafetyPolicy: snap.globalSafetyPolicy || { denyByDefault: true },
              responseStyle: snap.responseStyle || {},
              supportedEntrypoints: snap.supportedEntrypoints || ['assistant_chat'],
            }),
          )
        }
        setControlKeysText((snap.controlCapabilityKeys || []).join(', '))
        setDirty(false)
      }
    }

    // Evidence is server-listed only; client never fabricates run eligibility.
    if (summary.id && summary.draftVersion?.id) {
      try {
        setDraftEvidence(
          await loadQualifyingEvidence(
            'main_agent_profile_draft',
            summary.id,
            summary.draftVersion.id,
          ),
        )
      } catch {
        setDraftEvidence([])
      }
    } else {
      setDraftEvidence([])
    }
    if (summary.id && summary.publishedVersion?.id) {
      try {
        setPromotionEvidence(
          await loadQualifyingEvidence(
            'main_agent_profile_version',
            summary.id,
            summary.publishedVersion.id,
          ),
        )
      } catch {
        setPromotionEvidence([])
      }
    } else {
      setPromotionEvidence([])
    }
  }

  useEffect(() => {
    void reload().catch((err) => setError(mapSkillPackageError(err).message))
  }, [])

  // Drop stale gates when the subject pointer they target no longer matches.
  useEffect(() => {
    if (publishGate && publishGate.subjectVersionId !== draftVersionId) {
      setPublishGate(null)
    }
  }, [draftVersionId, publishGate])

  useEffect(() => {
    if (promotionGate && promotionGate.subjectVersionId !== publishedVersionId) {
      setPromotionGate(null)
    }
  }, [publishedVersionId, promotionGate])

  async function handleSaveDraft() {
    // Draft save is isolated: only mutates draft content; never sends enable/disable.
    // Live runtimeEnabled / published pointer come only from server reload.
    // New drafts are always V2 with fixed runtime policy — V1 is never authored.
    if (isHistoricalV1) {
      setError(t('settings.universalSkills.profileV1ReadOnly'))
      return
    }
    setBusy('draft')
    setError(null)
    setMessage(null)
    try {
      const keys = controlKeysText
        .split(/[\s,]+/)
        .map((k) => k.trim())
        .filter(Boolean)
      const next: MainAgentProfileSnapshotV2 = createDefaultProfileSnapshotV2({
        ...(snapshot as MainAgentProfileSnapshotV2),
        controlCapabilityKeys: keys,
        schemaVersion: 2,
      })
      if (assertNoSingleTargetFields(next as unknown as Record<string, unknown>).length) {
        throw new Error(t('settings.universalSkills.profileNoSingleTarget'))
      }
      const version = await saveProtectedDefaultMainAgentDraft({
        snapshot: next,
        expectedAggregateRevision: aggregateRevision,
        requestId: newRequestId('profile-draft'),
      })
      setDirty(false)
      // Content drift invalidates draft gate for prior subject.
      setPublishGate(null)
      setMessage(`${t('settings.universalSkills.saveDraft')}: ${version.id}`)
      // Reload authoritative summary; draft save does not demote live flags server-side.
      await reload()
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(null)
    }
  }

  async function handlePublish() {
    if (isHistoricalV1) {
      setError(t('settings.universalSkills.profileV1ReadOnly'))
      return
    }
    if (!draftVersionId) {
      setError(t('settings.universalSkills.noDraftVersion'))
      return
    }
    if (!publishGateValid || !publishGate) {
      setError(t('settings.universalSkills.gateNeedsRuns'))
      return
    }
    setBusy('publish')
    setError(null)
    try {
      await publishProtectedDefaultMainAgent({
        draftVersionId,
        expectedAggregateRevision: aggregateRevision,
        gateId: publishGate.gateId,
        requestId: newRequestId('profile-pub'),
      })
      // Publish invalidates draft evidence: clear draft run/gate and default the
      // workbench to the published pointer so promotion eval is immediately reachable
      // even when the backend keeps the draft pointer.
      setPublishGate(null)
      clearEvalForSubjectChange()
      setEvalTarget('published')
      setMessage(t('settings.universalSkills.profilePublished'))
      await reload()
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(null)
    }
  }

  async function handleEnableRuntime() {
    if (!publishedVersionId) {
      setError(t('settings.universalSkills.profileNeedsPublished'))
      return
    }
    if (!promotionGateValid || !promotionGate) {
      setError(t('settings.universalSkills.profileNeedsPromotionGate'))
      return
    }
    setBusy('enable')
    setError(null)
    try {
      const summary = await enableProtectedDefaultMainAgentRuntime({
        expectedAggregateRevision: aggregateRevision,
        expectedPublishedVersionId: publishedVersionId,
        gateId: promotionGate.gateId,
        requestId: newRequestId('profile-en'),
      })
      setProfile(summary)
      setMessage(t('settings.universalSkills.profileRuntimeEnabled'))
      await reload()
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(null)
    }
  }

  async function handleDisableRuntime() {
    if (
      !window.confirm(t('settings.universalSkills.profileDisableConfirm'))
    ) {
      return
    }
    setBusy('disable')
    setError(null)
    try {
      // Explicit disable: request ID + confirmation; no promotion gate.
      const summary = await disableProtectedDefaultMainAgentRuntime({
        expectedAggregateRevision: aggregateRevision,
        expectedPublishedVersionId: publishedVersionId,
        requestId: newRequestId('profile-dis'),
      })
      setProfile(summary)
      setMessage(t('settings.universalSkills.profileRuntimeDisabled'))
      await reload()
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('settings.universalSkills.profileTitle')}
        description={profile?.profileKey || 'default'}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection className="space-y-4">
        {error ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            {error}
          </div>
        ) : null}
        {message ? <div className="rounded-md border p-3 text-sm">{message}</div> : null}
        {singleTargetIssues.length > 0 ? (
          <div role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
            {t('settings.universalSkills.profileNoSingleTarget')}: {singleTargetIssues.join(', ')}
          </div>
        ) : null}

        {isHistoricalV1 ? (
          <div
            role="status"
            data-testid="profile-v1-readonly-banner"
            className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950"
          >
            {t(
              'settings.universalSkills.profileV1ReadOnlyBanner',
              'Historical Profile V1 — read only. Draft, publish, and prepare require Profile V2.',
            )}
          </div>
        ) : (
          <div
            data-testid="profile-v2-runtime-policy"
            className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700"
          >
            <div className="font-medium">
              {t('settings.universalSkills.profileRuntimePolicy', 'Runtime policy')}
            </div>
            <div className="mt-1 font-mono">
              runtimeKind=main_agent · recoveryScope=same_run_only
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-full border px-2 py-0.5">
            schema=v{snapshot.schemaVersion}
          </span>
          <span className="rounded-full border px-2 py-0.5">
            runtime={runtimeEnabled ? 'enabled' : 'disabled'}
          </span>
          <span className="rounded-full border px-2 py-0.5">
            draft={draftVersionId ?? '—'}
          </span>
          <span className="rounded-full border px-2 py-0.5">
            published={publishedVersionId ?? '—'}
          </span>
          <span className="rounded-full border px-2 py-0.5">
            rev={aggregateRevision}
          </span>
          {dirty ? (
            <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-blue-700 dark:text-blue-300">
              {t('settings.universalSkills.dirty')}
            </span>
          ) : null}
        </div>

        <label className="block space-y-1 text-sm">
          <span>{t('settings.universalSkills.basePrompt')}</span>
          <textarea
            className={cn(uiField.textarea, 'min-h-[180px] font-mono text-xs')}
            value={snapshot.basePrompt}
            disabled={readOnly}
            onChange={(e) => {
              if (readOnly) return
              setSnapshot((s) => ({ ...s, basePrompt: e.target.value }) as ReadableMainAgentProfileSnapshot)
              setDirty(true)
            }}
          />
        </label>

        <label className="block space-y-1 text-sm">
          <span>{t('settings.universalSkills.controlCapabilities')}</span>
          <input
            className={uiField.input}
            value={controlKeysText}
            disabled={readOnly}
            onChange={(e) => {
              if (readOnly) return
              setControlKeysText(e.target.value)
              setDirty(true)
            }}
            placeholder="capability.keys,comma,separated"
          />
        </label>

        <label className="block space-y-1 text-sm">
          <span>{t('settings.universalSkills.catalogScopeMode')}</span>
          <select
            className={uiField.select}
            value={snapshot.skillCatalogScope.mode}
            disabled={readOnly}
            onChange={(e) => {
              if (readOnly) return
              setSnapshot((s) => ({
                ...s,
                skillCatalogScope: {
                  ...s.skillCatalogScope,
                  mode: e.target.value as 'all_published' | 'allowlist',
                },
              }) as ReadableMainAgentProfileSnapshot)
              setDirty(true)
            }}
          >
            <option value="all_published">all_published</option>
            <option value="allowlist">allowlist</option>
          </select>
        </label>

        {isHistoricalV1 && 'fallbackPolicy' in snapshot ? (
          <div
            data-testid="profile-v1-fallback-readonly"
            className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600"
          >
            <div className="font-medium">
              {t('settings.universalSkills.profileV1FallbackReadonly', 'Historical fallback policy (read only)')}
            </div>
            <div className="mt-1 font-mono">
              legacyRuntimeAllowed={String(snapshot.fallbackPolicy.legacyRuntimeAllowed)} ·
              beforeSideEffectsOnly={String(snapshot.fallbackPolicy.beforeSideEffectsOnly)}
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            disabled={readOnly || busy !== null || !dirty}
            onClick={() => void handleSaveDraft()}
          >
            {busy === 'draft' ? t('messages.loading') : t('settings.universalSkills.saveDraft')}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={readOnly || !draftVersionId || busy !== null}
            onClick={() => setGateDialogMode('publish')}
          >
            {t('settings.universalSkills.openGateDialog')}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={readOnly || !publishedVersionId || busy !== null}
            onClick={() => setGateDialogMode('promotion')}
          >
            {t('settings.universalSkills.openPromotionGateDialog')}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={readOnly || busy !== null || !publishGateValid}
            onClick={() => void handlePublish()}
          >
            {busy === 'publish' ? t('messages.loading') : t('settings.universalSkills.publishProfile')}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={readOnly || !canEnableRuntime}
            onClick={() => void handleEnableRuntime()}
          >
            {busy === 'enable' ? t('messages.loading') : t('settings.universalSkills.enableRuntime')}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={readOnly || busy !== null || !runtimeEnabled}
            onClick={() => void handleDisableRuntime()}
          >
            {busy === 'disable' ? t('messages.loading') : t('settings.universalSkills.disableRuntime')}
          </Button>
          {publishGateValid && publishGate ? (
            <span className="font-mono text-xs text-muted-foreground">
              publish-gate={publishGate.gateId.slice(0, 8)}… {publishGate.decision}
            </span>
          ) : null}
          {promotionGateValid && promotionGate ? (
            <span className="font-mono text-xs text-muted-foreground">
              promo-gate={promotionGate.gateId.slice(0, 8)}… {promotionGate.decision}
            </span>
          ) : null}
        </div>
        {needsPublishedEvalHint ? (
          <p className="text-sm text-muted-foreground">
            {t('settings.universalSkills.evaluatePublishedBeforeEnable')}
          </p>
        ) : null}

        <SkillPublishGateDialog
          open={gateDialogMode === 'publish'}
          onClose={() => setGateDialogMode(null)}
          action="profile_publish"
          subjectAggregateId={profileId}
          subjectVersionId={draftVersionId}
          subjectKind="main_agent_profile_draft"
          qualifyingEvalRunIds={draftQualifyingRunIds}
          onCreated={(result) => {
            setPublishGate(gateUiStateFromResponse(result, 'profile_publish'))
          }}
        />
        <SkillPublishGateDialog
          open={gateDialogMode === 'promotion'}
          onClose={() => setGateDialogMode(null)}
          action="profile_runtime_enable"
          subjectAggregateId={profileId}
          subjectVersionId={publishedVersionId}
          subjectKind="main_agent_profile_version"
          qualifyingEvalRunIds={promotionQualifyingRunIds}
          onCreated={(result) => {
            setPromotionGate(gateUiStateFromResponse(result, 'profile_runtime_enable'))
          }}
        />

        {profileId && (draftVersionId || publishedVersionId) ? (
          <div className="space-y-2" data-testid="profile-eval-workbench">
            <h3 className="text-sm font-medium">
              {t('settings.universalSkills.evaluationWorkbench')}
            </h3>
            {dualPointer || publishedVersionId ? (
              <div
                className="flex flex-wrap items-center gap-2 text-sm"
                data-testid="profile-eval-target-selector"
                role="group"
                aria-label={t('settings.universalSkills.evalTargetLabel')}
              >
                <span className="text-muted-foreground">
                  {t('settings.universalSkills.evalTargetLabel')}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant={
                    workbenchSubjectKind === 'main_agent_profile_draft'
                      ? 'default'
                      : 'outline'
                  }
                  disabled={!draftVersionId}
                  onClick={() => handleEvalTargetChange('draft')}
                >
                  {t('settings.universalSkills.evalTargetDraft')}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={
                    workbenchSubjectKind === 'main_agent_profile_version'
                      ? 'default'
                      : 'outline'
                  }
                  disabled={!publishedVersionId}
                  onClick={() => handleEvalTargetChange('published')}
                >
                  {t('settings.universalSkills.evalTargetPublished')}
                </Button>
              </div>
            ) : null}
            <SkillTestWorkbench
              packageId={profileId}
              versionId={workbenchVersionId}
              subjectKind={workbenchSubjectKind}
            />
          </div>
        ) : null}

        <div className="space-y-2">
          <h3 className="text-sm font-medium">{t('settings.universalSkills.versionHistory')}</h3>
          <ul className="divide-y rounded-md border">
            {versions.map((v) => (
              <li key={v.id} className="p-3 text-sm">
                <div className="font-medium">
                  #{v.sequenceNo} {v.versionName} · {v.versionSource}
                </div>
                <div className="font-mono text-xs text-muted-foreground">
                  {v.id} · {v.contentDigest.slice(0, 16)}…
                </div>
              </li>
            ))}
            {versions.length === 0 ? (
              <li className="p-3 text-sm text-muted-foreground">{t('settings.universalSkills.noVersions')}</li>
            ) : null}
          </ul>
        </div>
      </SettingsSection>
    </SettingsPageShell>
  )
}

export function MainAgentProfileEditorPage() {
  // Route-level Plan09RouteGate in App.tsx fails closed before this mounts.
  return <ProfileEditorBody />
}
