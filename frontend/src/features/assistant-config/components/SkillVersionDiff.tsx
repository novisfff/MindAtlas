/**
 * Bounded skill version diff viewer (metadata + text hunks only).
 */
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

export interface SkillVersionDiffHunk {
  path: string
  kind: 'added' | 'removed' | 'changed' | 'unchanged_meta'
  leftPreview?: string | null
  rightPreview?: string | null
  summary?: string | null
}

export interface SkillVersionDiffResult {
  leftVersionId: string
  rightVersionId: string
  hunks?: SkillVersionDiffHunk[]
  leftMeta?: Record<string, unknown>
  rightMeta?: Record<string, unknown>
  [key: string]: unknown
}

export interface SkillVersionDiffProps {
  diff: SkillVersionDiffResult | null
  className?: string
}

export function SkillVersionDiff({ diff, className }: SkillVersionDiffProps) {
  const { t } = useTranslation()

  if (!diff) {
    return (
      <div className={cn('rounded-md border border-dashed p-4 text-sm text-muted-foreground', className)}>
        {t('settings.universalSkills.selectVersionsToCompare')}
      </div>
    )
  }

  const hunks = Array.isArray(diff.hunks) ? diff.hunks : []

  return (
    <div className={cn('space-y-3', className)}>
      <div className="text-sm font-medium">{t('settings.universalSkills.diffTitle')}</div>
      <div className="font-mono text-xs text-muted-foreground">
        {diff.leftVersionId} → {diff.rightVersionId}
      </div>
      {hunks.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('settings.universalSkills.diffEmpty')}</p>
      ) : (
        <ul className="space-y-2">
          {hunks.map((hunk, index) => (
            <li key={`${hunk.path}-${index}`} className="rounded-md border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs">{hunk.path}</span>
                <span className="rounded-full border px-2 py-0.5 text-xs">{hunk.kind}</span>
              </div>
              {hunk.summary ? <p className="mt-1 text-muted-foreground">{hunk.summary}</p> : null}
              {(hunk.leftPreview || hunk.rightPreview) && (
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-xs">
                    {hunk.leftPreview || '—'}
                  </pre>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-xs">
                    {hunk.rightPreview || '—'}
                  </pre>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
