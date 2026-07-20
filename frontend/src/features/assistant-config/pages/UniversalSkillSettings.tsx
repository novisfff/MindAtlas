import type { ChangeEvent } from 'react'
/**
 * Universal Skills list (Plan 09 Task 6).
 * Feature-gated via skill-admin surface probe; fail-closed when unavailable.
 */
import { useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Archive, Download, PackagePlus, RefreshCw, Upload } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

import {
  exportSkillPackageVersionUrl,
  mapSkillPackageError,
  newRequestId,
  type ImportMode,
  type SkillPackageSummary,
} from '../api/skill-packages'
import {
  useApplySkillPackageImportMutation,
  useArchiveSkillPackageMutation,
  useCreateSkillPackageMutation,
  usePreviewSkillPackageImportMutation,
  useSkillAdminSurfaceQuery,
  useSkillPackagesQuery,
  useUnarchiveSkillPackageMutation,
} from '../queries'

const DEFAULT_SKILL_MD = `---
name: example-skill
description: Example universal skill package
---

# Example Skill

Describe when to use this skill.
`

export function UniversalSkillSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const surface = useSkillAdminSurfaceQuery()
  const packagesQuery = useSkillPackagesQuery({ limit: 100, offset: 0 })
  const createMutation = useCreateSkillPackageMutation()
  const archiveMutation = useArchiveSkillPackageMutation()
  const unarchiveMutation = useUnarchiveSkillPackageMutation()
  const previewMutation = usePreviewSkillPackageImportMutation()
  const applyMutation = useApplySkillPackageImportMutation()
  const fileRef = useRef<HTMLInputElement>(null)
  const [importMode, setImportMode] = useState<ImportMode>('create')
  const [importTargetPackageId, setImportTargetPackageId] = useState('')
  const [importForkCanonicalName, setImportForkCanonicalName] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  const items = packagesQuery.data?.items ?? []
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return items
    return items.filter(
      (p) =>
        p.canonicalName.toLowerCase().includes(q) ||
        p.displayName.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q),
    )
  }, [items, filter])

  if (surface.isLoading) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.title')}
          description={t('messages.loading')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
      </SettingsPageShell>
    )
  }

  if (!surface.data?.available) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.title')}
          description={t('settings.universalSkills.unavailableDesc')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
        <SettingsSection>
          <div role="alert" className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
            {t('settings.universalSkills.unavailableBody')}
            <div className="mt-4">
              <Button asChild variant="outline">
                <Link to="/settings/assistant-skills">{t('settings.universalSkills.openLegacy')}</Link>
              </Button>
            </div>
          </div>
        </SettingsSection>
      </SettingsPageShell>
    )
  }

  async function handleCreate() {
    setActionError(null)
    try {
      const name = `skill-${Date.now().toString(36)}`
      const skillMd = DEFAULT_SKILL_MD.replace('example-skill', name)
      const created = await createMutation.mutateAsync({
        skillMd,
        mindatlasYaml: 'capabilities: []\n',
        resources: [],
      })
      navigate(`/settings/universal-skills/${created.id}`)
    } catch (error) {
      setActionError(mapSkillPackageError(error).message)
    }
  }

  async function handleArchiveToggle(pkg: SkillPackageSummary) {
    setActionError(null)
    const body = {
      requestId: newRequestId('archive'),
      expectedAggregateRevision: pkg.aggregateRevision,
    }
    try {
      if (pkg.archivedAt) {
        await unarchiveMutation.mutateAsync({ packageId: pkg.id, body })
      } else {
        await archiveMutation.mutateAsync({ packageId: pkg.id, body })
      }
    } catch (error) {
      setActionError(mapSkillPackageError(error).message)
    }
  }

  async function handleImportFile(file: File) {
    setActionError(null)
    try {
      if (importMode === 'append_to_existing') {
        if (!importTargetPackageId) {
          setActionError(t('settings.universalSkills.importTargetRequired'))
          return
        }
      }
      if (importMode === 'fork_as_new') {
        if (!importForkCanonicalName.trim()) {
          setActionError(t('settings.universalSkills.importForkNameRequired'))
          return
        }
      }
      const targetPkg =
        importMode === 'append_to_existing'
          ? items.find((p) => p.id === importTargetPackageId)
          : undefined
      if (importMode === 'append_to_existing' && !targetPkg) {
        setActionError(t('settings.universalSkills.importTargetRequired'))
        return
      }
      const preview = await previewMutation.mutateAsync({
        file,
        mode: importMode,
        targetPackageId: targetPkg?.id,
        expectedAggregateRevision: targetPkg?.aggregateRevision,
        forkCanonicalName:
          importMode === 'fork_as_new' ? importForkCanonicalName.trim() : undefined,
      })
      const applied = await applyMutation.mutateAsync({
        previewId: preview.previewId,
        requestId: newRequestId('import'),
      })
      navigate(`/settings/universal-skills/${applied.package.id}`)
    } catch (error) {
      setActionError(mapSkillPackageError(error).message)
    }
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('settings.universalSkills.title')}
        description={t('settings.universalSkills.description')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />

      <SettingsSection className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={() => void handleCreate()} disabled={createMutation.isPending}>
            <PackagePlus className="mr-1.5 h-4 w-4" />
            {t('settings.universalSkills.create')}
          </Button>
          <Button type="button" variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload className="mr-1.5 h-4 w-4" />
            {t('settings.universalSkills.import')}
          </Button>
          <select
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={importMode}
            onChange={(e) => setImportMode(e.target.value as ImportMode)}
            aria-label={t('settings.universalSkills.importMode')}
          >
            <option value="create">create</option>
            <option value="append_to_existing">append_to_existing</option>
            <option value="fork_as_new">fork_as_new</option>
          </select>
          {importMode === 'append_to_existing' ? (
            <select
              className="h-9 min-w-[12rem] rounded-md border bg-background px-2 text-sm"
              value={importTargetPackageId}
              onChange={(e) => setImportTargetPackageId(e.target.value)}
              aria-label={t('settings.universalSkills.importTarget')}
            >
              <option value="">{t('settings.universalSkills.importTargetPlaceholder')}</option>
              {items.map((pkg) => (
                <option key={pkg.id} value={pkg.id}>
                  {pkg.displayName || pkg.canonicalName} (rev {pkg.aggregateRevision})
                </option>
              ))}
            </select>
          ) : null}
          {importMode === 'fork_as_new' ? (
            <input
              className={cn(uiField.input, 'h-9 max-w-xs')}
              value={importForkCanonicalName}
              onChange={(e: ChangeEvent<HTMLInputElement>) => setImportForkCanonicalName(e.target.value)}
              placeholder={t('settings.universalSkills.importForkNamePlaceholder')}
              aria-label={t('settings.universalSkills.importForkName')}
            />
          ) : null}
          <input
            ref={fileRef}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) void handleImportFile(file)
            }}
          />
          <Button type="button" variant="ghost" onClick={() => void packagesQuery.refetch()} disabled={packagesQuery.isFetching}>
            <RefreshCw className={cn('mr-1.5 h-4 w-4', packagesQuery.isFetching && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
          <div className="ml-auto min-w-[200px] max-w-sm flex-1">
            <input value={filter} onChange={(e: ChangeEvent<HTMLInputElement>) => setFilter(e.target.value)} placeholder={t('settings.universalSkills.filterPlaceholder')} className={uiField.input} />
          </div>
        </div>

        {!surface.data.adminMounted ? (
          <p className="text-xs text-amber-700 dark:text-amber-300">{t('settings.universalSkills.adminUnmountedHint')}</p>
        ) : null}

        {actionError ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">{actionError}</div>
        ) : null}

        {packagesQuery.isLoading ? <p className="text-sm text-muted-foreground">{t('messages.loading')}</p> : null}

        {packagesQuery.isError ? (
          <div role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
            {mapSkillPackageError(packagesQuery.error).message}
          </div>
        ) : null}

        {!packagesQuery.isLoading && filtered.length === 0 ? (
          <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">{t('settings.universalSkills.empty')}</div>
        ) : null}

        <ul className="divide-y rounded-md border" role="list">
          {filtered.map((pkg) => (
            <li key={pkg.id} className="flex flex-wrap items-center gap-3 p-4">
              <div className="min-w-0 flex-1">
                <Link to={`/settings/universal-skills/${pkg.id}`} className="font-medium text-primary hover:underline">
                  {pkg.displayName || pkg.canonicalName}
                </Link>
                <div className="mt-1 font-mono text-xs text-muted-foreground">{pkg.canonicalName}</div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  <span className="rounded-full border px-2 py-0.5">{pkg.migrationState}</span>
                  <span className="rounded-full border px-2 py-0.5">rev {pkg.aggregateRevision}</span>
                  {pkg.catalogEnabled ? (
                    <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-emerald-700 dark:text-emerald-300">
                      {t('settings.universalSkills.catalogEnabled')}
                    </span>
                  ) : (
                    <span className="rounded-full bg-muted px-2 py-0.5">{t('settings.universalSkills.catalogDisabled')}</span>
                  )}
                  {pkg.archivedAt ? (
                    <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-amber-700 dark:text-amber-300">
                      {t('settings.universalSkills.archived')}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {pkg.publishedVersion?.id || pkg.draftVersion?.id ? (
                  <Button type="button" size="sm" variant="outline" asChild>
                    <a href={exportSkillPackageVersionUrl(pkg.id, (pkg.publishedVersion?.id || pkg.draftVersion?.id) as string)}>
                      <Download className="mr-1 h-3.5 w-3.5" />
                      {t('common.export')}
                    </a>
                  </Button>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!surface.data.adminMounted || archiveMutation.isPending || unarchiveMutation.isPending}
                  onClick={() => void handleArchiveToggle(pkg)}
                >
                  <Archive className="mr-1 h-3.5 w-3.5" />
                  {pkg.archivedAt ? t('settings.universalSkills.unarchive') : t('settings.universalSkills.archive')}
                </Button>
                <Button type="button" size="sm" onClick={() => navigate(`/settings/universal-skills/${pkg.id}`)}>
                  {t('common.edit')}
                </Button>
              </div>
            </li>
          ))}
        </ul>

        <p className="text-xs text-muted-foreground">
          <Link to="/settings/assistant-skills" className="underline">{t('settings.universalSkills.openLegacy')}</Link>
        </p>
      </SettingsSection>
    </SettingsPageShell>
  )
}
