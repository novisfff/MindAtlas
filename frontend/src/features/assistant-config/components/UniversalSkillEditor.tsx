import type { ChangeEvent } from 'react'
/**
 * Universal Skill package editor sections (Plan 09 Task 6/10).
 * Working copy is local; save posts a complete normalized snapshot.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Save } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

import { extractCapabilityKeys, SkillCapabilityEditor } from './SkillCapabilityEditor'
import { SkillPolicyEditor } from './SkillPolicyEditor'
import { SkillResourceBrowser } from './SkillResourceBrowser'
import { SkillTestWorkbench } from './SkillTestWorkbench'
import { useSkillEditorStore } from '../stores/skill-editor-store'
import {
  fetchSkillPackageResourceBlob,
  listPublishedCapabilityIdentities,
  type CapabilityRegistryIdentity,
  type SkillResourceInput,
  type SkillResourceMetadata,
} from '../api/skill-packages'

type EditorTab =
  | 'overview'
  | 'instructions'
  | 'applicability'
  | 'capabilities'
  | 'policy'
  | 'budgets'
  | 'completion'
  | 'resources'
  | 'versions'

const TABS: EditorTab[] = [
  'overview',
  'instructions',
  'applicability',
  'capabilities',
  'policy',
  'budgets',
  'completion',
  'resources',
  'versions',
]

export interface UniversalSkillEditorProps {
  onSaveDraft: () => Promise<void>
  onSaveMetadata?: () => Promise<void>
  saving?: boolean
  className?: string
  /** Workbench subject kind — draft for publish evidence, version for promotion. */
  evalSubjectKind?: 'skill_draft' | 'skill_version'
  /** Workbench subject version id (draft or published pointer). */
  evalVersionId?: string | null
}

async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer()
  let binary = ''
  const bytes = new Uint8Array(buffer)
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

export function UniversalSkillEditor({
  onSaveDraft,
  onSaveMetadata,
  saving = false,
  className,
  evalSubjectKind = 'skill_draft',
  evalVersionId,
}: UniversalSkillEditorProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<EditorTab>('overview')
  const [registry, setRegistry] = useState<CapabilityRegistryIdentity[]>([])

  const packageDetail = useSkillEditorStore((s) => s.packageDetail)
  const draftDetail = useSkillEditorStore((s) => s.draftDetail)
  const draftVersionId = useSkillEditorStore((s) => s.draftVersionId)
  const workingCopy = useSkillEditorStore((s) => s.workingCopy)
  const isDirty = useSkillEditorStore((s) => s.isDirty)
  const resourcesDirty = useSkillEditorStore((s) => s.resourcesDirty)
  const resourcesHydrated = useSkillEditorStore((s) => s.resourcesHydrated)
  const resourcesHydrationStatus = useSkillEditorStore((s) => s.resourcesHydrationStatus)
  const resourcesHydrationError = useSkillEditorStore((s) => s.resourcesHydrationError)
  const lastConflict = useSkillEditorStore((s) => s.lastConflict)
  const validationDiagnostics = useSkillEditorStore((s) => s.validationDiagnostics)
  const expectedAggregateRevision = useSkillEditorStore((s) => s.expectedAggregateRevision)

  const setSkillMd = useSkillEditorStore((s) => s.setSkillMd)
  const setMindatlasYaml = useSkillEditorStore((s) => s.setMindatlasYaml)
  const setVersionName = useSkillEditorStore((s) => s.setVersionName)
  const setDisplayName = useSkillEditorStore((s) => s.setDisplayName)
  const setDescription = useSkillEditorStore((s) => s.setDescription)
  const upsertResource = useSkillEditorStore((s) => s.upsertResource)
  const removeResource = useSkillEditorStore((s) => s.removeResource)
  const hydrateResources = useSkillEditorStore((s) => s.hydrateResources)
  const setResourcesHydrationError = useSkillEditorStore((s) => s.setResourcesHydrationError)
  const resetFromServer = useSkillEditorStore((s) => s.resetFromServer)

  // useWorkingCopy once draft is loaded so empty working-copy (remove-all) never falls back to server.
  const useWorkingCopyResources =
    Boolean(packageDetail) && (resourcesDirty || resourcesHydrationStatus !== 'idle')
  const canMutateResources = resourcesHydrated && resourcesHydrationStatus === 'ready'

  const capabilityKeys = useMemo(
    () => extractCapabilityKeys(workingCopy.mindatlasYaml),
    [workingCopy.mindatlasYaml],
  )

  const serverResources: SkillResourceMetadata[] = draftDetail?.resources ?? []

  useEffect(() => {
    let cancelled = false
    void listPublishedCapabilityIdentities()
      .then((items) => {
        if (!cancelled) setRegistry(items)
      })
      .catch(() => {
        if (!cancelled) setRegistry([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Hydrate resource bytes once so remove/replace can send a complete CAS snapshot.
  // Failed fetch fails hydrate (does not invent empty base64 as ready).
  useEffect(() => {
    if (!packageDetail || !draftVersionId || resourcesDirty) return
    if (resourcesHydrationStatus === 'ready' || resourcesHydrationStatus === 'error') return
    if (serverResources.length === 0) return
    let cancelled = false
    void (async () => {
      try {
        const loaded: SkillResourceInput[] = []
        for (const meta of serverResources) {
          const blob = await fetchSkillPackageResourceBlob(
            packageDetail.id,
            draftVersionId,
            meta.path,
          )
          loaded.push({ path: meta.path, contentBase64: await blobToBase64(blob) })
        }
        if (!cancelled) hydrateResources(loaded)
      } catch (error) {
        if (!cancelled) {
          setResourcesHydrationError(
            error instanceof Error ? error.message : 'Failed to hydrate resource bytes',
          )
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [
    packageDetail,
    draftVersionId,
    serverResources,
    resourcesDirty,
    resourcesHydrationStatus,
    hydrateResources,
    setResourcesHydrationError,
  ])

  function replaceCapabilityKeys(keys: string[]) {
    const yaml = workingCopy.mindatlasYaml
    const lines = yaml.split(/\r?\n/)
    const out: string[] = []
    let inCaps = false
    let replaced = false
    for (const raw of lines) {
      const trimmedStart = raw.trimStart()
      if (/^capabilities\s*:/.test(trimmedStart) && !trimmedStart.startsWith('#')) {
        out.push((raw.match(/^\s*/)?.[0] ?? '') + 'capabilities:')
        for (const key of keys) {
          const [type, ...rest] = key.split(':')
          const name = rest.join(':') || key
          if (rest.length > 0 && (type === 'tool' || type === 'workflow' || type === 'agent')) {
            out.push(`${raw.match(/^\s*/)?.[0] || ''}  - type: ${type}`)
            out.push(`${raw.match(/^\s*/)?.[0] || ''}    key: ${name}`)
          } else {
            out.push(`${raw.match(/^\s*/)?.[0] || ''}  - ${key}`)
          }
        }
        inCaps = true
        replaced = true
        continue
      }
      if (inCaps) {
        if (/^\S/.test(raw) && !trimmedStart.startsWith('#')) {
          inCaps = false
          out.push(raw)
        }
        continue
      }
      out.push(raw)
    }
    if (!replaced) {
      out.push('capabilities:')
      for (const key of keys) {
        const [type, ...rest] = key.split(':')
        const name = rest.join(':') || key
        if (rest.length > 0 && (type === 'tool' || type === 'workflow' || type === 'agent')) {
          out.push(`  - type: ${type}`)
          out.push(`    key: ${name}`)
        } else {
          out.push(`  - ${key}`)
        }
      }
    }
    setMindatlasYaml(out.join('\n'))
  }

  if (!packageDetail) {
    return (
      <div className={cn('p-6 text-sm text-muted-foreground', className)}>
        {t('settings.universalSkills.loadingPackage')}
      </div>
    )
  }

  const archived = Boolean(packageDetail.archivedAt)
  // Block save while resource mutation is pending hydrate — empty seeds must never CAS.
  const resourceSaveBlocked =
    resourcesDirty && (!resourcesHydrated || resourcesHydrationStatus !== 'ready')
  const saveDisabled = saving || archived || !isDirty || resourceSaveBlocked

  return (
    <div className={cn('space-y-4', className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">{packageDetail.displayName || packageDetail.canonicalName}</h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{packageDetail.canonicalName}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full border px-2 py-0.5">rev {expectedAggregateRevision}</span>
            {packageDetail.catalogEnabled ? (
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-emerald-700 dark:text-emerald-300">
                {t('settings.universalSkills.catalogEnabled')}
              </span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5">{t('settings.universalSkills.catalogDisabled')}</span>
            )}
            {archived ? (
              <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-amber-700 dark:text-amber-300">
                {t('settings.universalSkills.archived')}
              </span>
            ) : null}
            {isDirty ? (
              <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-blue-700 dark:text-blue-300">
                {t('settings.universalSkills.dirty')}
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" disabled={!isDirty || saving} onClick={() => resetFromServer()}>
            {t('settings.universalSkills.reload')}
          </Button>
          {onSaveMetadata ? (
            <Button type="button" variant="outline" disabled={saving || archived} onClick={() => void onSaveMetadata()}>
              {t('settings.universalSkills.saveMetadata')}
            </Button>
          ) : null}
          <Button type="button" disabled={saveDisabled} onClick={() => void onSaveDraft()}>
            <Save className="mr-1.5 h-4 w-4" />
            {t('settings.universalSkills.saveDraft')}
          </Button>
        </div>
      </div>

      {resourcesHydrationStatus === 'pending' && serverResources.length > 0 ? (
        <div role="status" className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          {t('settings.universalSkills.resourcesHydrating', {
            defaultValue: 'Loading resource bytes before edit…',
          })}
        </div>
      ) : null}

      {resourcesHydrationStatus === 'error' ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <div className="font-medium">
            {t('settings.universalSkills.resourcesHydrateFailed', {
              defaultValue: 'Resource hydrate failed',
            })}
          </div>
          <p className="mt-1 text-muted-foreground">
            {resourcesHydrationError ||
              t('settings.universalSkills.resourcesHydrateFailedHint', {
                defaultValue: 'Reload the package before mutating resources to avoid wiping bytes.',
              })}
          </p>
        </div>
      ) : null}

      {lastConflict ? (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" aria-hidden />
          <div>
            <div className="font-medium">{t('settings.universalSkills.conflictTitle')}</div>
            <p className="mt-1 text-muted-foreground">{lastConflict.message}</p>
            <p className="mt-1 text-xs text-muted-foreground">{t('settings.universalSkills.conflictHint')}</p>
          </div>
        </div>
      ) : null}

      {validationDiagnostics.length > 0 ? (
        <div role="status" className="rounded-md border p-3 text-sm">
          <div className="font-medium">{t('settings.universalSkills.diagnostics')}</div>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {validationDiagnostics.map((d, i) => (
              <li key={`${d.path}-${i}`}>
                <span className="font-mono text-xs">{d.path}</span>: {d.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-1 border-b pb-2" role="tablist" aria-label={t('settings.universalSkills.editorTabs')}>
        {TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={cn(
              'rounded-md px-3 py-1.5 text-sm transition-colors',
              tab === id ? 'bg-primary text-primary-foreground' : 'hover:bg-muted',
            )}
            onClick={() => setTab(id)}
          >
            {t(`settings.universalSkills.tabs.${id}`)}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="min-h-[280px]">
        {tab === 'overview' ? (
          <div className="grid max-w-2xl gap-4">
            <label className="space-y-1 text-sm">
              <span>{t('settings.universalSkills.displayName')}</span>
              <input value={workingCopy.displayName} disabled={archived} onChange={(e: ChangeEvent<HTMLInputElement>) => setDisplayName(e.target.value)} className={uiField.input} />
            </label>
            <label className="space-y-1 text-sm">
              <span>{t('settings.universalSkills.descriptionField')}</span>
              <textarea value={workingCopy.description} disabled={archived} onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setDescription(e.target.value)} className={cn(uiField.textarea, 'min-h-[100px]')} />
            </label>
            <label className="space-y-1 text-sm">
              <span>{t('settings.universalSkills.versionName')}</span>
              <input value={workingCopy.versionName} disabled={archived} onChange={(e: ChangeEvent<HTMLInputElement>) => setVersionName(e.target.value)} className={uiField.input} />
            </label>
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <dt className="text-muted-foreground">{t('settings.universalSkills.migrationState')}</dt>
                <dd className="font-mono">{packageDetail.migrationState}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('settings.universalSkills.systemPackage')}</dt>
                <dd>{packageDetail.isSystem ? t('common.yes') : t('common.no')}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('settings.universalSkills.draftVersion')}</dt>
                <dd className="font-mono text-xs">{draftVersionId ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('settings.universalSkills.publishedVersion')}</dt>
                <dd className="font-mono text-xs">{packageDetail.publishedVersion?.id ?? '—'}</dd>
              </div>
            </dl>
          </div>
        ) : null}

        {tab === 'instructions' ? (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">{t('settings.universalSkills.skillMdHint')}</p>
            <textarea
              value={workingCopy.skillMd}
              disabled={archived}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setSkillMd(e.target.value)}
              spellCheck={false}
              className={cn(uiField.textarea, 'min-h-[360px] font-mono text-xs')}
              aria-label="SKILL.md"
            />
          </div>
        ) : null}

        {tab === 'applicability' ? (
          <SkillPolicyEditor mode="applicability" mindatlasYaml={workingCopy.mindatlasYaml} onChange={setMindatlasYaml} disabled={archived} />
        ) : null}

        {tab === 'capabilities' ? (
          <div className="space-y-4">
            <SkillCapabilityEditor
              capabilityKeys={capabilityKeys}
              onChange={replaceCapabilityKeys}
              registryKeys={registry.map((r) => r.key)}
              registry={registry.map((r) => ({
                key: r.key,
                target: r.target,
                version: r.version,
                resolution: r.resolution,
                risk: r.risk,
              }))}
              disabled={archived}
            />
            <SkillPolicyEditor mode="full" mindatlasYaml={workingCopy.mindatlasYaml} onChange={setMindatlasYaml} disabled={archived} />
          </div>
        ) : null}

        {tab === 'policy' ? (
          <SkillPolicyEditor mode="policy" mindatlasYaml={workingCopy.mindatlasYaml} onChange={setMindatlasYaml} disabled={archived} />
        ) : null}

        {tab === 'budgets' ? (
          <SkillPolicyEditor mode="budgets" mindatlasYaml={workingCopy.mindatlasYaml} onChange={setMindatlasYaml} disabled={archived} />
        ) : null}

        {tab === 'completion' ? (
          <SkillPolicyEditor mode="completion" mindatlasYaml={workingCopy.mindatlasYaml} onChange={setMindatlasYaml} disabled={archived} />
        ) : null}

        {tab === 'resources' ? (
          <SkillResourceBrowser
            packageId={packageDetail.id}
            versionId={draftVersionId}
            resources={serverResources}
            workingCopyResources={workingCopy.resources}
            useWorkingCopy={useWorkingCopyResources}
            editable={!archived}
            mutationsEnabled={canMutateResources}
            onUpsertResource={(resource) => upsertResource(resource)}
            onRemoveResource={(path) => removeResource(path)}
          />
        ) : null}

        {tab === 'versions' ? (
          <div className="space-y-4">
            <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              <p>{t('settings.universalSkills.versionsShell')}</p>
              <p className="mt-2 font-mono text-xs">
                draft={draftVersionId ?? '—'} published={packageDetail.publishedVersion?.id ?? '—'}
              </p>
            </div>
            <SkillTestWorkbench
              packageId={packageDetail.id}
              versionId={evalVersionId ?? draftVersionId}
              subjectKind={evalSubjectKind}
            />
          </div>
        ) : null}
      </div>
    </div>
  )
}
