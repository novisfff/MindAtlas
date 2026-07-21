/**
 * Safe resource browser for skill package versions.
 * - Text: escaped plain text only
 * - Raster images: object URL with cleanup
 * - HTML/SVG/binary: download metadata only
 * - scripts/: inert badge, no execute control
 * - Working-copy mode: add/replace/remove for draft CAS save
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, FileCode2, FileText, Image as ImageIcon, Plus, ShieldAlert, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import {
  fetchSkillPackageResourceBlob,
  isDangerousMarkupMediaType,
  isRasterImageMediaType,
  isScriptResourcePath,
  isTextPreviewMediaType,
  type SkillResourceInput,
  type SkillResourceMetadata,
} from '../api/skill-packages'

const TEXT_PREVIEW_MAX_CHARS = 64_000

export interface SkillResourceBrowserProps {
  packageId: string
  versionId: string | null
  resources: SkillResourceMetadata[]
  /** Working-copy resource bytes for draft save (complete snapshot). */
  workingCopyResources?: SkillResourceInput[]
  /** When true, show add/replace/remove controls that mutate the working copy. */
  editable?: boolean
  onUpsertResource?: (resource: SkillResourceInput) => void
  onRemoveResource?: (path: string) => void
  className?: string
}

function resourceIcon(resource: SkillResourceMetadata) {
  if (isScriptResourcePath(resource.path) || resource.resourceKind === 'scripts') {
    return FileCode2
  }
  if (isRasterImageMediaType(resource.mediaType)) return ImageIcon
  return FileText
}

function guessMediaType(path: string): string {
  const lower = path.toLowerCase()
  if (lower.endsWith('.md')) return 'text/markdown'
  if (lower.endsWith('.txt')) return 'text/plain'
  if (lower.endsWith('.json')) return 'application/json'
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'application/yaml'
  if (lower.endsWith('.py')) return 'text/x-python'
  if (lower.endsWith('.js') || lower.endsWith('.ts')) return 'text/plain'
  if (lower.endsWith('.png')) return 'image/png'
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg'
  if (lower.endsWith('.gif')) return 'image/gif'
  if (lower.endsWith('.webp')) return 'image/webp'
  if (lower.endsWith('.svg')) return 'image/svg+xml'
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'text/html'
  return 'application/octet-stream'
}

function guessResourceKind(path: string): SkillResourceMetadata['resourceKind'] {
  const normalized = path.replace(/^\/+/, '')
  if (normalized.startsWith('scripts/')) return 'scripts'
  if (normalized.startsWith('references/')) return 'references'
  if (normalized.startsWith('assets/')) return 'assets'
  return 'other'
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read file'))
    reader.readAsDataURL(file)
  })
}

export function SkillResourceBrowser({
  packageId,
  versionId,
  resources,
  workingCopyResources = [],
  editable = false,
  onUpsertResource,
  onRemoveResource,
  className,
}: SkillResourceBrowserProps) {
  const { t } = useTranslation()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const replacePathRef = useRef<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [previewText, setPreviewText] = useState<string | null>(null)
  const [previewObjectUrl, setPreviewObjectUrl] = useState<string | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [pathDraft, setPathDraft] = useState('references/new.txt')

  const displayResources: SkillResourceMetadata[] = useMemo(() => {
    if (!editable || workingCopyResources.length === 0) return resources
    // Prefer working-copy paths once the user has mutated resources.
    const byPath = new Map(resources.map((r) => [r.path, r]))
    return workingCopyResources.map((wc) => {
      const existing = byPath.get(wc.path)
      if (existing) return existing
      return {
        path: wc.path,
        resourceKind: guessResourceKind(wc.path),
        mediaType: guessMediaType(wc.path),
        byteSize: Math.floor((wc.contentBase64.length * 3) / 4),
        sha256: 'working-copy',
      }
    })
  }, [editable, resources, workingCopyResources])

  const selected = useMemo(
    () => displayResources.find((r) => r.path === selectedPath) ?? null,
    [displayResources, selectedPath],
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
    // Working-copy only entries without server sha cannot be fetched.
    if (resource.sha256 === 'working-copy') {
      const wc = workingCopyResources.find((r) => r.path === resource.path)
      if (wc && isTextPreviewMediaType(resource.mediaType)) {
        try {
          const text = atob(wc.contentBase64)
          setPreviewText(
            text.length > TEXT_PREVIEW_MAX_CHARS
              ? `${text.slice(0, TEXT_PREVIEW_MAX_CHARS)}\n…`
              : text,
          )
          setPreviewError(null)
          return
        } catch {
          setPreviewError(t('settings.universalSkills.resourceDownloadOnly'))
          return
        }
      }
      setPreviewError(t('settings.universalSkills.resourceDownloadOnly'))
      return
    }

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
      if (resource.sha256 === 'working-copy') {
        const wc = workingCopyResources.find((r) => r.path === resource.path)
        if (!wc) return
        const binary = atob(wc.contentBase64)
        const bytes = new Uint8Array(binary.length)
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
        const blob = new Blob([bytes])
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = resource.path.split('/').pop() || 'resource'
        a.rel = 'noopener'
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
        return
      }
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

  async function handleFileSelected(fileList: FileList | null) {
    if (!fileList || fileList.length === 0 || !onUpsertResource) return
    const file = fileList[0]
    const contentBase64 = await fileToBase64(file)
    const path = replacePathRef.current || pathDraft.trim() || file.name
    replacePathRef.current = null
    onUpsertResource({ path, contentBase64 })
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  if (!versionId) {
    return (
      <div className={cn('rounded-md border border-dashed p-4 text-sm text-muted-foreground', className)}>
        {t('settings.universalSkills.noDraftVersion')}
      </div>
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      {editable ? (
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[220px] flex-1 space-y-1 text-sm">
            <span className="text-muted-foreground">{t('settings.universalSkills.workingCopyResources')}</span>
            <input
              value={pathDraft}
              onChange={(e) => setPathDraft(e.target.value)}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              placeholder="references/example.md"
            />
          </label>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => {
              replacePathRef.current = null
              fileInputRef.current?.click()
            }}
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            {t('settings.universalSkills.addResource')}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={(e) => void handleFileSelected(e.target.files)}
          />
        </div>
      ) : null}

      {displayResources.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {t('settings.universalSkills.noResources')}
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <ul className="space-y-2" role="list" aria-label={t('settings.universalSkills.resources')}>
            {displayResources.map((resource) => {
              const Icon = resourceIcon(resource)
              const isScript = isScriptResourcePath(resource.path) || resource.resourceKind === 'scripts'
              const active = resource.path === selectedPath
              return (
                <li key={resource.path} className="space-y-1">
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
                  {editable && onRemoveResource ? (
                    <div className="flex flex-wrap gap-2 pl-1">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          replacePathRef.current = resource.path
                          fileInputRef.current?.click()
                        }}
                      >
                        {t('settings.universalSkills.replaceResource')}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        aria-label={t('settings.universalSkills.removeResource')}
                        onClick={() => onRemoveResource(resource.path)}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden />
                        {t('settings.universalSkills.removeResource')}
                      </Button>
                    </div>
                  ) : null}
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
      )}
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
