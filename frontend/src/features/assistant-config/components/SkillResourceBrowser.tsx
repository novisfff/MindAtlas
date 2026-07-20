/**
 * Safe resource browser for immutable skill package versions.
 * - Text: escaped plain text only
 * - Raster images: object URL with cleanup
 * - HTML/SVG/binary: download metadata only
 * - scripts/: inert badge, no execute control
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, FileCode2, FileText, Image as ImageIcon, ShieldAlert } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import {
  fetchSkillPackageResourceBlob,
  isDangerousMarkupMediaType,
  isRasterImageMediaType,
  isScriptResourcePath,
  isTextPreviewMediaType,
  type SkillResourceMetadata,
} from '../api/skill-packages'

const TEXT_PREVIEW_MAX_CHARS = 64_000

export interface SkillResourceBrowserProps {
  packageId: string
  versionId: string | null
  resources: SkillResourceMetadata[]
  className?: string
}

function resourceIcon(resource: SkillResourceMetadata) {
  if (isScriptResourcePath(resource.path) || resource.resourceKind === 'scripts') {
    return FileCode2
  }
  if (isRasterImageMediaType(resource.mediaType)) return ImageIcon
  return FileText
}

export function SkillResourceBrowser({
  packageId,
  versionId,
  resources,
  className,
}: SkillResourceBrowserProps) {
  const { t } = useTranslation()
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [previewText, setPreviewText] = useState<string | null>(null)
  const [previewObjectUrl, setPreviewObjectUrl] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const selected = useMemo(
    () => resources.find((r) => r.path === selectedPath) ?? null,
    [resources, selectedPath],
  )

  useEffect(() => {
    return () => {
      if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl)
    }
  }, [previewObjectUrl])

  useEffect(() => {
    setSelectedPath(null)
    setPreviewText(null)
    setPreviewError(null)
    setPreviewObjectUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })
  }, [packageId, versionId])

  async function loadPreview(resource: SkillResourceMetadata) {
    if (!versionId) return
    setLoading(true)
    setPreviewError(null)
    setPreviewText(null)
    setPreviewObjectUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev)
      return null
    })

    try {
      if (isDangerousMarkupMediaType(resource.mediaType)) {
        setPreviewError(t('settings.universalSkills.resourceDownloadOnly'))
        return
      }

      const blob = await fetchSkillPackageResourceBlob(packageId, versionId, resource.path)

      if (isRasterImageMediaType(resource.mediaType)) {
        const url = URL.createObjectURL(blob)
        setPreviewObjectUrl(url)
        return
      }

      if (isTextPreviewMediaType(resource.mediaType) || resource.resourceKind === 'scripts') {
        const text = await blob.text()
        setPreviewText(
          text.length > TEXT_PREVIEW_MAX_CHARS
            ? `${text.slice(0, TEXT_PREVIEW_MAX_CHARS)}\n…`
            : text,
        )
        return
      }

      setPreviewError(t('settings.universalSkills.resourceDownloadOnly'))
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : t('messages.error'))
    } finally {
      setLoading(false)
    }
  }

  async function downloadResource(resource: SkillResourceMetadata) {
    if (!versionId) return
    try {
      const blob = await fetchSkillPackageResourceBlob(packageId, versionId, resource.path)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = resource.path.split('/').pop() || 'resource'
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : t('messages.error'))
    }
  }

  if (!versionId) {
    return (
      <div className={cn('rounded-md border border-dashed p-4 text-sm text-muted-foreground', className)}>
        {t('settings.universalSkills.noDraftVersion')}
      </div>
    )
  }

  if (resources.length === 0) {
    return (
      <div className={cn('rounded-md border border-dashed p-4 text-sm text-muted-foreground', className)}>
        {t('settings.universalSkills.noResources')}
      </div>
    )
  }

  return (
    <div className={cn('grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]', className)}>
      <ul className="space-y-2" role="list" aria-label={t('settings.universalSkills.resources')}>
        {resources.map((resource) => {
          const Icon = resourceIcon(resource)
          const isScript = isScriptResourcePath(resource.path) || resource.resourceKind === 'scripts'
          const active = resource.path === selectedPath
          return (
            <li key={resource.path}>
              <button
                type="button"
                className={cn(
                  'flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors',
                  active ? 'border-primary bg-primary/5' : 'hover:bg-muted/40',
                )}
                onClick={() => {
                  setSelectedPath(resource.path)
                  void loadPreview(resource)
                }}
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-sm">{resource.path}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {resource.mediaType} · {resource.byteSize} B
                  </div>
                  {isScript ? (
                    <div className="mt-2 inline-flex items-center gap-1 rounded bg-amber-500/10 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-300">
                      <ShieldAlert className="h-3 w-3" aria-hidden />
                      {t('settings.universalSkills.scriptInertBadge')}
                    </div>
                  ) : null}
                </div>
              </button>
            </li>
          )
        })}
      </ul>

      <div className="min-h-[220px] rounded-md border p-4">
        {!selected ? (
          <p className="text-sm text-muted-foreground">{t('settings.universalSkills.selectResource')}</p>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-medium text-sm">{selected.path}</div>
                <div className="text-xs text-muted-foreground">
                  {selected.mediaType} · sha256:{selected.sha256.slice(0, 12)}…
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void downloadResource(selected)}
              >
                <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {t('common.download')}
              </Button>
            </div>

            {(isScriptResourcePath(selected.path) || selected.resourceKind === 'scripts') && (
              <p className="text-xs text-amber-700 dark:text-amber-300">
                {t('settings.universalSkills.scriptInertHint')}
              </p>
            )}

            {loading ? <p className="text-sm text-muted-foreground">{t('messages.loading')}</p> : null}
            {previewError ? <p className="text-sm text-destructive">{previewError}</p> : null}

            {previewObjectUrl ? (
              <img
                src={previewObjectUrl}
                alt={selected.path}
                className="max-h-80 max-w-full rounded border object-contain"
              />
            ) : null}

            {previewText != null ? (
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-3 text-xs">
                {previewText}
              </pre>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}

export function resourcePreviewMode(resource: SkillResourceMetadata): 'text' | 'image' | 'download' {
  if (isDangerousMarkupMediaType(resource.mediaType)) return 'download'
  if (isRasterImageMediaType(resource.mediaType)) return 'image'
  if (isTextPreviewMediaType(resource.mediaType) || resource.resourceKind === 'scripts') return 'text'
  return 'download'
}

export function hasExecuteControl(): boolean {
  return false
}
