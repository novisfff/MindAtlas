/**
 * Universal Skill package editor page (Plan 09 Task 6).
 */
import { useEffect, useState } from 'react'
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
import type { PublishGateSubject } from '../api/skill-evaluations'
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

export function UniversalSkillEditorPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { packageId = '' } = useParams<{ packageId: string }>()
  const surface = useSkillAdminSurfaceQuery()
  const packageQuery = useSkillPackageQuery(packageId)
  const draftVersionId = packageQuery.data?.draftVersion?.id ?? null
  const draftQuery = useSkillPackageVersionQuery(packageId, draftVersionId)
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
  const [gateOpen, setGateOpen] = useState(false)
  const [lastGateId, setLastGateId] = useState<string | null>(null)
  const [lastGateDecision, setLastGateDecision] = useState<string | null>(null)
  const versionsQuery = useSkillPackageVersionsQuery(packageId)
  const restoreMutation = useRestoreSkillPackageVersionMutation()
  const diffMutation = useDiffSkillPackageVersionsMutation()
  const enableCatalogMutation = useEnableSkillPackageCatalogMutation()
  const evalRun = useSkillTestRunStore((s) => s.run)

  function buildGateSubject(): PublishGateSubject | null {
    const draft = packageQuery.data?.draftVersion
    const digest = draftQuery.data?.contentDigest || draft?.contentDigest
    const binding = draftQuery.data?.bindingSetDigest || draft?.bindingSetDigest || digest
    if (!packageQuery.data || !draft?.id || !digest || !binding) return null
    if (digest.length !== 64 || binding.length !== 64) return null
    const zero = '0'.repeat(64)
    return {
      schemaVersion: 1,
      subject: {
        schemaVersion: 1,
        kind: 'skill_draft',
        aggregateId: packageId,
        versionId: draft.id,
        contentDigest: digest,
        resolvedBindingDigest: binding,
      },
      profileDigest: zero,
      catalogDigest: zero,
      runtimeContractVersion: 1,
      policyVersion: 'plan09-policy-v1',
      thresholdVersion: 'plan09-policy-v1',
      // Server revalidates dataset pins; interactive-only gates may still need a real dataset later.
      datasetVersionIds: [],
      buildRevision: 'development',
    }
  }

  async function handlePublish() {
    const draftId = packageQuery.data?.draftVersion?.id
    if (!draftId) {
      setPageError(t('settings.universalSkills.noDraftVersion'))
      return
    }
    setPageError(null)
    try {
      await publishSkillPackageVersion(packageId, {
        draftVersionId: draftId,
        expectedAggregateRevision: packageQuery.data?.aggregateRevision ?? 0,
        gateId: lastGateId,
        requestId: newRequestId('publish'),
      })
      await packageQuery.refetch()
      await versionsQuery.refetch()
    } catch (err) {
      setPageError(mapSkillPackageError(err).message)
    }
  }

  async function handleEnableCatalog() {
    if (!lastGateId) {
      setPageError(t('settings.universalSkills.gateNeedsRuns'))
      return
    }
    setPageError(null)
    try {
      await enableCatalogMutation.mutateAsync({
        packageId,
        body: {
          requestId: newRequestId('catalog-en'),
          expectedAggregateRevision,
          gateId: lastGateId,
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
                  await packageQuery.refetch()
                  await versionsQuery.refetch()
                })
                .catch((err) => setPageError(mapSkillPackageError(err).message))
            }}
          />
          <SkillVersionDiff diff={diff} />
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => setGateOpen(true)}>
              {t('settings.universalSkills.openGateDialog')}
            </Button>
            <Button type="button" variant="outline" onClick={() => void handlePublish()}>
              Publish draft
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={!lastGateId || enableCatalogMutation.isPending}
              onClick={() => void handleEnableCatalog()}
            >
              {t('settings.universalSkills.catalogEnabled')}
            </Button>
            {lastGateId ? (
              <span className="font-mono text-xs text-muted-foreground">
                gate={lastGateId.slice(0, 8)}… {lastGateDecision}
              </span>
            ) : null}
            {evalRun?.id ? (
              <span className="font-mono text-xs text-muted-foreground">
                eval={evalRun.id.slice(0, 8)}…
              </span>
            ) : null}
          </div>
          <SkillPublishGateDialog
            open={gateOpen}
            onClose={() => setGateOpen(false)}
            subject={buildGateSubject()}
            qualifyingEvalRunIds={evalRun?.id ? [evalRun.id] : []}
            onCreated={(result) => {
              setLastGateId(result.gate.id)
              setLastGateDecision(result.decision)
            }}
          />
        </div>

        <UniversalSkillEditor
          onSaveDraft={handleSaveDraft}
          onSaveMetadata={surface.data.adminMounted ? handleSaveMetadata : undefined}
          saving={saveDraftMutation.isPending || patchMetadataMutation.isPending}
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
