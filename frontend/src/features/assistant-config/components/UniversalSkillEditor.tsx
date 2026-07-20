import type { ChangeEvent } from 'react'
/**
 * Universal Skill package editor sections (Plan 09 Task 6).
 * Working copy is local; save posts a complete normalized snapshot.
 */
import { useMemo, useState } from 'react'
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
import type { SkillResourceMetadata } from '../api/skill-packages'

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
}

export function UniversalSkillEditor({
  onSaveDraft,
  onSaveMetadata,
  saving = false,
  className,
}: UniversalSkillEditorProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<EditorTab>('overview')

  const packageDetail = useSkillEditorStore((s) => s.packageDetail)
  const draftDetail = useSkillEditorStore((s) => s.draftDetail)
  const draftVersionId = useSkillEditorStore((s) => s.draftVersionId)
  const workingCopy = useSkillEditorStore((s) => s.workingCopy)
  const isDirty = useSkillEditorStore((s) => s.isDirty)
  const lastConflict = useSkillEditorStore((s) => s.lastConflict)
  const validationDiagnostics = useSkillEditorStore((s) => s.validationDiagnostics)
  const expectedAggregateRevision = useSkillEditorStore((s) => s.expectedAggregateRevision)

  const setSkillMd = useSkillEditorStore((s) => s.setSkillMd)
  const setMindatlasYaml = useSkillEditorStore((s) => s.setMindatlasYaml)
  const setVersionName = useSkillEditorStore((s) => s.setVersionName)
  const setDisplayName = useSkillEditorStore((s) => s.setDisplayName)
  const setDescription = useSkillEditorStore((s) => s.setDescription)
  const resetFromServer = useSkillEditorStore((s) => s.resetFromServer)

  const capabilityKeys = useMemo(
    () => extractCapabilityKeys(workingCopy.mindatlasYaml),
    [workingCopy.mindatlasYaml],
  )

  const serverResources: SkillResourceMetadata[] = draftDetail?.resources ?? []

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
          out.push(`${raw.match(/^\s*/)?.[0] || ''}  - ${key}`)
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
      for (const key of keys) out.push(`  - ${key}`)
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
          <Button type="button" disabled={saving || archived || !isDirty} onClick={() => void onSaveDraft()}>
            <Save className="mr-1.5 h-4 w-4" />
            {t('settings.universalSkills.saveDraft')}
          </Button>
        </div>
      </div>

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
              <textarea value={workingCopy.description} disabled={archived} onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setDescription(e.target.value)} className={cn(uiField.textarea, "min-h-[100px]")} />
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
            <SkillCapabilityEditor capabilityKeys={capabilityKeys} onChange={replaceCapabilityKeys} disabled={archived} />
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
          <SkillResourceBrowser packageId={packageDetail.id} versionId={draftVersionId} resources={serverResources} />
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
              versionId={draftVersionId}
              contentDigest={draftDetail?.contentDigest ?? packageDetail.draftVersion?.contentDigest ?? null}
              bindingDigest={draftDetail?.bindingSetDigest ?? packageDetail.draftVersion?.bindingSetDigest ?? null}
            />
          </div>
        ) : null}
      </div>
    </div>
  )
}
