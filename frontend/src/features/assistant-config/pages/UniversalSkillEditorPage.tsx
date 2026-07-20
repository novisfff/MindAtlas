/**
 * Universal Skill package editor page (Plan 09 Task 6).
 */
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'

import { isConflictError, mapSkillPackageError, newRequestId } from '../api/skill-packages'
import { UniversalSkillEditor } from '../components/UniversalSkillEditor'
import {
  usePatchSkillPackageMetadataMutation,
  useSaveSkillPackageDraftMutation,
  useSkillAdminSurfaceQuery,
  useSkillPackageQuery,
  useSkillPackageVersionQuery,
} from '../queries'
import { useSkillEditorStore } from '../stores/skill-editor-store'

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
