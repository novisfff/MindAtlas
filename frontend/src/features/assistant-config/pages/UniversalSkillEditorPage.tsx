/**
 * Universal Skill package editor page (Plan 09 Task 6 / remediation Task 11).
 * Two-gate lifecycle: skill_publish (draft) then skill_catalog_enable (published).
 * Client never authors digests/decisions/metrics; gates are action+subject-version keyed.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'

import {
  isConflictError,
  mapSkillPackageError,
  newRequestId,
  publishSkillPackageVersion,
} from '../api/skill-packages'
import {
  gateUiStateFromResponse,
  isQualifyingGateRun,
  type GateUiState,
} from '../api/skill-evaluations'
import { UniversalSkillEditor } from '../components/UniversalSkillEditor'
import { SkillVersionHistory } from '../components/SkillVersionHistory'
import { SkillVersionDiff } from '../components/SkillVersionDiff'
import { SkillPublishGateDialog } from '../components/SkillPublishGateDialog'
import {
  useDiffSkillPackageVersionsMutation,
  useEnableSkillPackageCatalogMutation,
  useRestoreSkillPackageVersionMutation,
  useSkillPackageVersionsQuery,
} from '../queries'
import type { SkillVersionDiffResult } from '../components/SkillVersionDiff'
import {
  usePatchSkillPackageMetadataMutation,
  useSaveSkillPackageDraftMutation,
  useSkillAdminSurfaceQuery,
  useSkillPackageQuery,
  useSkillPackageVersionQuery,
} from '../queries'
import { useSkillEditorStore } from '../stores/skill-editor-store'
import { useSkillTestRunStore } from '../stores/skill-test-run-store'

type GateDialogMode = 'publish' | 'promotion' | null

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

export function UniversalSkillEditorPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { packageId = '' } = useParams<{ packageId: string }>()
  // Defense in depth: even if mounted outside the route gate, never fetch
  // protected packages until the surface probe reports available.
  const surface = useSkillAdminSurfaceQuery()
  const surfaceReady = Boolean(surface.data?.available)
  const packageQuery = useSkillPackageQuery(packageId, surfaceReady)
  const draftVersionId = packageQuery.data?.draftVersion?.id ?? null
  const publishedVersionId = packageQuery.data?.publishedVersion?.id ?? null
  const draftQuery = useSkillPackageVersionQuery(packageId, draftVersionId, surfaceReady)
  const saveDraftMutation = useSaveSkillPackageDraftMutation()
  const patchMetadataMutation = usePatchSkillPackageMetadataMutation()
  const loadPackage = useSkillEditorStore((s) => s.loadPackage)
  const clear = useSkillEditorStore((s) => s.clear)
  const buildSaveBody = useSkillEditorStore((s) => s.buildSaveBody)
  const markSaved = useSkillEditorStore((s) => s.markSaved)
  const setConflict = useSkillEditorStore((s) => s.setConflict)
  const setLastRequestId = useSkillEditorStore((s) => s.setLastRequestId)
  const expectedAggregateRevision = useSkillEditorStore((s) => s.expectedAggregateRevision)
  const workingCopy = useSkillEditorStore((s) => s.workingCopy)
  const isDirty = useSkillEditorStore((s) => s.isDirty)
  const [pageError, setPageError] = useState<string | null>(null)
  const [leftVersionId, setLeftVersionId] = useState<string | null>(null)
  const [rightVersionId, setRightVersionId] = useState<string | null>(null)
  const [diff, setDiff] = useState<SkillVersionDiffResult | null>(null)
  // Separate action/subject-keyed gate records — never share publish ↔ promotion.
  const [publishGate, setPublishGate] = useState<GateUiState | null>(null)
  const [promotionGate, setPromotionGate] = useState<GateUiState | null>(null)
  const [gateDialogMode, setGateDialogMode] = useState<GateDialogMode>(null)
  const versionsQuery = useSkillPackageVersionsQuery(packageId, { limit: 50, offset: 0 }, surfaceReady)
  const restoreMutation = useRestoreSkillPackageVersionMutation()
  const diffMutation = useDiffSkillPackageVersionsMutation()
  const enableCatalogMutation = useEnableSkillPackageCatalogMutation()
  const evalRun = useSkillTestRunStore((s) => s.run)
  const clearEvalForSubjectChange = useSkillTestRunStore((s) => s.clearForSubjectChange)

  // After publish, evaluate the published pointer; otherwise evaluate the draft.
  const workbenchSubjectKind = publishedVersionId && !draftVersionId ? 'skill_version' : 'skill_draft'
  const workbenchVersionId =
    workbenchSubjectKind === 'skill_version' ? publishedVersionId : draftVersionId

  const publishGateValid = gateMatches(publishGate, 'skill_publish', draftVersionId)
  const promotionGateValid = gateMatches(promotionGate, 'skill_catalog_enable', publishedVersionId)

  const draftQualifyingRunIds = useMemo(() => {
    if (
      isQualifyingGateRun(evalRun, {
        subjectKind: 'skill_draft',
        subjectAggregateId: packageId,
        subjectVersionId: draftVersionId ?? '',
      })
    ) {
      return [evalRun!.id]
    }
    return []
  }, [evalRun, packageId, draftVersionId])

  const promotionQualifyingRunIds = useMemo(() => {
    if (
      isQualifyingGateRun(evalRun, {
        subjectKind: 'skill_version',
        subjectAggregateId: packageId,
        subjectVersionId: publishedVersionId ?? '',
      })
    ) {
      return [evalRun!.id]
    }
    return []
  }, [evalRun, packageId, publishedVersionId])

  const canEnableCatalog =
    Boolean(publishedVersionId) &&
    promotionGateValid &&
    promotionQualifyingRunIds.length > 0 &&
    !enableCatalogMutation.isPending &&
    !packageQuery.data?.catalogEnabled

  const needsPublishedEvalHint =
    Boolean(publishedVersionId) &&
    !packageQuery.data?.catalogEnabled &&
    (!promotionGateValid || promotionQualifyingRunIds.length === 0)

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

  async function handlePublish() {
    const draftId = packageQuery.data?.draftVersion?.id
    if (!draftId) {
      setPageError(t('settings.universalSkills.noDraftVersion'))
      return
    }
    if (!publishGateValid || !publishGate) {
      setPageError(t('settings.universalSkills.gateNeedsRuns'))
      return
    }
    setPageError(null)
    try {
      await publishSkillPackageVersion(packageId, {
        draftVersionId: draftId,
        expectedAggregateRevision: packageQuery.data?.aggregateRevision ?? 0,
        gateId: publishGate.gateId,
        requestId: newRequestId('publish'),
      })
      // Publish invalidates draft evidence: clear draft run/gate and switch to published subject.
      setPublishGate(null)
      clearEvalForSubjectChange()
      await packageQuery.refetch()
      await versionsQuery.refetch()
    } catch (err) {
      setPageError(mapSkillPackageError(err).message)
    }
  }

  async function handleEnableCatalog() {
    if (!publishedVersionId) {
      setPageError(t('settings.universalSkills.evaluatePublishedBeforeEnable'))
      return
    }
    if (!promotionGateValid || !promotionGate) {
      setPageError(t('settings.universalSkills.evaluatePublishedBeforeEnable'))
      return
    }
    setPageError(null)
    try {
      await enableCatalogMutation.mutateAsync({
        packageId,
        body: {
          requestId: newRequestId('catalog-en'),
          expectedAggregateRevision,
          gateId: promotionGate.gateId,
          expectedPublishedVersionId: publishedVersionId,
        },
      })
      await packageQuery.refetch()
    } catch (err) {
      setPageError(mapSkillPackageError(err).message)
    }
  }

  useEffect(() => {
    return () => clear()
  }, [clear])

  useEffect(() => {
    if (packageQuery.data) {
      loadPackage(packageQuery.data, draftQuery.data ?? null)
    }
  }, [packageQuery.data, draftQuery.data, loadPackage])

  if (surface.isLoading || packageQuery.isLoading) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.editorTitle')}
          description={t('messages.loading')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings/universal-skills') }}
        />
      </SettingsPageShell>
    )
  }

  if (!surface.data?.available) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.editorTitle')}
          description={t('settings.universalSkills.unavailableDesc')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
        <SettingsSection>
          <div role="alert" className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
            {t('settings.universalSkills.unavailableBody')}
          </div>
        </SettingsSection>
      </SettingsPageShell>
    )
  }

  if (packageQuery.isError) {
    const mapped = mapSkillPackageError(packageQuery.error)
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.editorTitle')}
          description={mapped.kind === 'not_found' ? t('settings.universalSkills.notFound') : mapped.message}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings/universal-skills') }}
        />
      </SettingsPageShell>
    )
  }

  async function handleSaveDraft() {
    setPageError(null)
    setConflict(null)
    const requestId = newRequestId('save')
    setLastRequestId(requestId)
    try {
      const body = buildSaveBody()
      const version = await saveDraftMutation.mutateAsync({ packageId, body })
      // Content/binding drift invalidates draft gate + draft evidence for prior subject.
      setPublishGate(null)
      clearEvalForSubjectChange()
      const refreshed = await packageQuery.refetch()
      markSaved({
        packageDetail: refreshed.data ?? null,
        draftVersionId: version.id,
        expectedAggregateRevision: refreshed.data?.aggregateRevision,
        requestId,
      })
    } catch (error) {
      if (isConflictError(error)) {
        setConflict({
          message: mapSkillPackageError(error).message,
          details: mapSkillPackageError(error).details,
        })
      } else {
        setPageError(mapSkillPackageError(error).message)
      }
    }
  }

  async function handleSaveMetadata() {
    setPageError(null)
    setConflict(null)
    const requestId = newRequestId('meta')
    setLastRequestId(requestId)
    try {
      const detail = await patchMetadataMutation.mutateAsync({
        packageId,
        body: {
          requestId,
          expectedAggregateRevision,
          displayName: workingCopy.displayName,
          description: workingCopy.description,
        },
      })
      markSaved({
        packageDetail: detail,
        expectedAggregateRevision: detail.aggregateRevision,
        requestId,
      })
    } catch (error) {
      if (isConflictError(error)) {
        setConflict({
          message: mapSkillPackageError(error).message,
          details: mapSkillPackageError(error).details,
        })
      } else {
        setPageError(mapSkillPackageError(error).message)
      }
    }
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('settings.universalSkills.editorTitle')}
        description={packageQuery.data?.canonicalName}
        backAction={{
          label: t('common.back'),
          onClick: () => {
            if (isDirty && !window.confirm(t('settings.universalSkills.unsavedConfirm'))) return
            navigate('/settings/universal-skills')
          },
        }}
      />
      <SettingsSection className="space-y-4">
        {pageError ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">{pageError}</div>
        ) : null}

        <div className="space-y-4 border-t pt-4">
          <SkillVersionHistory
            packageId={packageId}
            versions={versionsQuery.data?.items ?? []}
            draftVersionId={packageQuery.data?.draftVersion?.id}
            publishedVersionId={packageQuery.data?.publishedVersion?.id}
            selectedLeftId={leftVersionId}
            selectedRightId={rightVersionId}
            onSelectLeft={setLeftVersionId}
            onSelectRight={setRightVersionId}
            restoring={restoreMutation.isPending}
            onCompare={() => {
              if (!leftVersionId || !rightVersionId) return
              void diffMutation
                .mutateAsync({ packageId, leftVersionId, rightVersionId })
                .then((result) => setDiff(result as SkillVersionDiffResult))
                .catch((err) => setPageError(mapSkillPackageError(err).message))
            }}
            onRestore={(versionId) => {
              void restoreMutation
                .mutateAsync({
                  packageId,
                  versionId,
                  body: {
                    requestId: newRequestId('restore'),
                    expectedAggregateRevision,
                  },
                })
                .then(async () => {
                  setPublishGate(null)
                  clearEvalForSubjectChange()
                  await packageQuery.refetch()
                  await versionsQuery.refetch()
                })
                .catch((err) => setPageError(mapSkillPackageError(err).message))
            }}
          />
          <SkillVersionDiff diff={diff} />
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={!draftVersionId}
              onClick={() => setGateDialogMode('publish')}
            >
              {t('settings.universalSkills.openGateDialog')}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!publishedVersionId}
              onClick={() => setGateDialogMode('promotion')}
            >
              {t('settings.universalSkills.openPromotionGateDialog')}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!publishGateValid}
              onClick={() => void handlePublish()}
            >
              {t('settings.universalSkills.publishDraft')}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!canEnableCatalog}
              onClick={() => void handleEnableCatalog()}
            >
              {t('settings.universalSkills.enableCatalog')}
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
            {evalRun?.id ? (
              <span className="font-mono text-xs text-muted-foreground">
                eval={evalRun.id.slice(0, 8)}… {evalRun.subjectKind}
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
            action="skill_publish"
            subjectAggregateId={packageId || null}
            subjectVersionId={draftVersionId}
            subjectKind="skill_draft"
            qualifyingEvalRunIds={draftQualifyingRunIds}
            onCreated={(result) => {
              setPublishGate(gateUiStateFromResponse(result, 'skill_publish'))
            }}
          />
          <SkillPublishGateDialog
            open={gateDialogMode === 'promotion'}
            onClose={() => setGateDialogMode(null)}
            action="skill_catalog_enable"
            subjectAggregateId={packageId || null}
            subjectVersionId={publishedVersionId}
            subjectKind="skill_version"
            qualifyingEvalRunIds={promotionQualifyingRunIds}
            onCreated={(result) => {
              setPromotionGate(gateUiStateFromResponse(result, 'skill_catalog_enable'))
            }}
          />
        </div>

        <UniversalSkillEditor
          onSaveDraft={handleSaveDraft}
          onSaveMetadata={surface.data.adminMounted ? handleSaveMetadata : undefined}
          saving={saveDraftMutation.isPending || patchMetadataMutation.isPending}
          evalSubjectKind={workbenchSubjectKind}
          evalVersionId={workbenchVersionId}
        />
        <p className="text-xs text-muted-foreground">
          <Link to="/settings/assistant-skills" className="underline">{t('settings.universalSkills.openLegacy')}</Link>
        </p>
        {!surface.data.adminMounted ? (
          <p className="text-xs text-amber-700 dark:text-amber-300">{t('settings.universalSkills.adminUnmountedHint')}</p>
        ) : null}
        {draftQuery.isError ? (
          <p className="text-xs text-muted-foreground">{t('settings.universalSkills.draftLoadFailed')}</p>
        ) : null}
      </SettingsSection>
    </SettingsPageShell>
  )
}
