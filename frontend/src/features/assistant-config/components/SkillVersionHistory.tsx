/**
 * Immutable skill package version history (Plan 09 Task 7).
 * Restore always creates a new draft — never rewinds the published pointer.
 */
import { useTranslation } from 'react-i18next'
import { Download, GitCompare, History, RotateCcw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import {
  exportSkillPackageVersionUrl,
  type SkillVersionSummary,
} from '../api/skill-packages'

export interface SkillVersionHistoryProps {
  packageId: string
  versions: SkillVersionSummary[]
  draftVersionId?: string | null
  publishedVersionId?: string | null
  selectedLeftId?: string | null
  selectedRightId?: string | null
  onSelectLeft?: (id: string) => void
  onSelectRight?: (id: string) => void
  onRestore?: (versionId: string) => void
  onCompare?: () => void
  restoring?: boolean
  className?: string
}

export function SkillVersionHistory({
  packageId,
  versions,
  draftVersionId,
  publishedVersionId,
  selectedLeftId,
  selectedRightId,
  onSelectLeft,
  onSelectRight,
  onRestore,
  onCompare,
  restoring = false,
  className,
}: SkillVersionHistoryProps) {
  const { t } = useTranslation()

  if (versions.length === 0) {
    return (
      <div className={cn('rounded-md border border-dashed p-4 text-sm text-muted-foreground', className)}>
        {t('settings.universalSkills.noVersions')}
      </div>
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <History className="h-4 w-4" aria-hidden />
          {t('settings.universalSkills.versionHistory')}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" disabled={!selectedLeftId || !selectedRightId} onClick={onCompare}>
            <GitCompare className="mr-1.5 h-3.5 w-3.5" />
            {t('settings.universalSkills.compare')}
          </Button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">{t('settings.universalSkills.restoreIsNewDraft')}</p>

      <ul className="divide-y rounded-md border" role="list">
        {versions.map((version) => {
          const isDraft = version.id === draftVersionId
          const isPublished = version.id === publishedVersionId
          return (
            <li key={version.id} className="flex flex-wrap items-start gap-3 p-3">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-sm">
                    #{version.sequenceNo} {version.versionName}
                  </span>
                  <span className="rounded-full border px-2 py-0.5 text-xs">{version.versionSource}</span>
                  {isDraft ? (
                    <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-xs text-blue-700 dark:text-blue-300">
                      {t('settings.universalSkills.draftMarker')}
                    </span>
                  ) : null}
                  {isPublished ? (
                    <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300">
                      {t('settings.universalSkills.publishedMarker')}
                    </span>
                  ) : null}
                </div>
                <div className="font-mono text-xs text-muted-foreground break-all">
                  id={version.id}
                </div>
                <div className="font-mono text-xs text-muted-foreground">
                  content={version.contentDigest.slice(0, 16)}… origin={version.origin}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    name="diff-left"
                    checked={selectedLeftId === version.id}
                    onChange={() => onSelectLeft?.(version.id)}
                  />
                  L
                </label>
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="radio"
                    name="diff-right"
                    checked={selectedRightId === version.id}
                    onChange={() => onSelectRight?.(version.id)}
                  />
                  R
                </label>
                <Button type="button" size="sm" variant="outline" asChild>
                  <a href={exportSkillPackageVersionUrl(packageId, version.id)}>
                    <Download className="mr-1 h-3.5 w-3.5" />
                    {t('common.export')}
                  </a>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={restoring || !onRestore}
                  onClick={() => onRestore?.(version.id)}
                >
                  <RotateCcw className="mr-1 h-3.5 w-3.5" />
                  {t('settings.universalSkills.restoreDraft')}
                </Button>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
