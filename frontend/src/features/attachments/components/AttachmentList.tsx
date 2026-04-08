import { useState } from 'react'
import {
  File,
  Download,
  Trash2,
  Image,
  FileText,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  Eye,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Attachment } from '@/types'
import { getDownloadUrl } from '../api/attachments'
import { cn } from '@/lib/utils'
import { AttachmentPreview } from './AttachmentPreview'

interface AttachmentListProps {
  attachments: Attachment[]
  compact?: boolean
  onDelete?: (id: string) => void
  onRetry?: (id: string) => void
  onRetryIndex?: (id: string) => void
  isDeleting?: boolean
  isRetrying?: boolean
  isRetryingIndex?: boolean
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function getFileIcon(contentType: string) {
  if (contentType.startsWith('image/')) return Image
  if (contentType.includes('pdf') || contentType.includes('document'))
    return FileText
  return File
}

type KgUiStatusTone = 'muted' | 'info' | 'success' | 'danger'

function KnowledgeStatusBadge({
  attachment,
  compact = false,
}: {
  attachment: Attachment
  compact?: boolean
}) {
  const { t } = useTranslation()

  if (!attachment.indexToKnowledgeGraph) return null

  let label = t('attachment.kgStatus.queuedForParse', 'Queued for parsing')
  let tone: KgUiStatusTone = 'muted'
  let icon = <Loader2 className="w-3.5 h-3.5" />
  let spin = true
  let title: string | undefined

  const parse = attachment.parseStatus
  if (parse === 'processing') {
    label = t('attachment.kgStatus.parsing', 'Parsing')
    tone = 'info'
  } else if (parse === 'failed') {
    label = t('attachment.kgStatus.parseFailed', 'Parse failed')
    tone = 'danger'
    icon = <AlertCircle className="w-3.5 h-3.5" />
    spin = false
    title = attachment.parseLastError || undefined
  } else if (parse === 'completed') {
    const idx = attachment.kgIndexStatus
    if (!idx) {
      label = t('attachment.kgStatus.waitingIndex', 'Waiting to index')
      tone = 'muted'
    } else if (idx === 'pending') {
      label = t('attachment.kgStatus.indexQueued', 'Index queued')
      tone = 'info'
    } else if (idx === 'processing') {
      label = t('attachment.kgStatus.indexing', 'Indexing')
      tone = 'info'
    } else if (idx === 'succeeded') {
      label = t('attachment.kgStatus.indexed', 'Indexed')
      tone = 'success'
      icon = <CheckCircle className="w-3.5 h-3.5" />
      spin = false
    } else if (idx === 'dead') {
      label = t('attachment.kgStatus.indexFailed', 'Index failed')
      tone = 'danger'
      icon = <AlertCircle className="w-3.5 h-3.5" />
      spin = false
      title = attachment.kgIndexLastError || undefined
    }
  } else if (parse === 'pending') {
    label = t('attachment.kgStatus.queuedForParse', 'Queued for parsing')
    tone = 'muted'
  }

  const toneClass =
    tone === 'success'
      ? 'bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20'
      : tone === 'danger'
        ? 'bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20'
        : tone === 'info'
          ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20'
          : 'bg-muted/50 text-muted-foreground border-border/60'

  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center rounded-full border leading-none',
        compact
          ? 'gap-1 px-1.5 py-0.5 text-[10px]'
          : 'gap-1.5 px-2 py-0.5 text-[11px]',
        toneClass,
      )}
    >
      <span className={cn(spin && 'animate-spin')}>{icon}</span>
      <span className="whitespace-nowrap">{label}</span>
    </span>
  )
}

export function AttachmentList({
  attachments,
  compact = false,
  onDelete,
  onRetry,
  onRetryIndex,
  isDeleting,
  isRetrying,
  isRetryingIndex,
}: AttachmentListProps) {
  const { t } = useTranslation()
  const [previewAttachment, setPreviewAttachment] = useState<Attachment | null>(
    null,
  )

  if (attachments.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-4 text-center">
        {t('entry.noAttachments')}
      </div>
    )
  }

  return (
    <>
      <div className="space-y-2">
        {attachments.map((attachment) => {
          const Icon = getFileIcon(attachment.contentType)
          return (
            <div
              key={attachment.id}
              className={cn(
                'flex items-center justify-between overflow-hidden rounded-lg border bg-card',
                compact ? 'p-2.5' : 'p-3',
                'hover:bg-accent/50 transition-colors',
              )}
            >
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <Icon
                  className={cn(
                    'text-muted-foreground flex-shrink-0',
                    compact ? 'h-[18px] w-[18px]' : 'h-5 w-5',
                  )}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center gap-2">
                    <p
                      className={cn(
                        'min-w-0 flex-1 truncate font-medium',
                        compact ? 'text-[13px]' : 'text-sm',
                      )}
                    >
                      {attachment.originalFilename}
                    </p>
                    <KnowledgeStatusBadge
                      attachment={attachment}
                      compact={compact}
                    />
                  </div>
                  <p
                    className={cn(
                      'text-muted-foreground',
                      compact ? 'text-[11px]' : 'text-xs',
                    )}
                  >
                    {formatFileSize(attachment.size)}
                  </p>
                </div>
              </div>
              <div className="ml-2 flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPreviewAttachment(attachment)}
                  aria-label="Preview attachment"
                  className={cn(
                    'rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors',
                    compact ? 'p-1' : 'p-1.5',
                  )}
                >
                  <Eye className="w-4 h-4" />
                </button>
                {attachment.parseStatus === 'failed' && onRetry && (
                  <button
                    type="button"
                    onClick={() => onRetry(attachment.id)}
                    disabled={isRetrying}
                    aria-label="Retry parse"
                    className={cn(
                      'rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50',
                      compact ? 'p-1' : 'p-1.5',
                    )}
                  >
                    <RefreshCw
                      className={cn('w-4 h-4', isRetrying && 'animate-spin')}
                    />
                  </button>
                )}
                {attachment.parseStatus === 'completed' &&
                  attachment.kgIndexStatus === 'dead' &&
                  onRetryIndex && (
                    <button
                      type="button"
                      onClick={() => onRetryIndex(attachment.id)}
                      disabled={isRetryingIndex}
                      aria-label="Retry index"
                      className={cn(
                        'rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50',
                        compact ? 'p-1' : 'p-1.5',
                      )}
                    >
                      <RefreshCw
                        className={cn(
                          'w-4 h-4',
                          isRetryingIndex && 'animate-spin',
                        )}
                      />
                    </button>
                  )}
                <a
                  href={getDownloadUrl(attachment.id)}
                  download
                  className={cn(
                    'rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors',
                    compact ? 'p-1' : 'p-1.5',
                  )}
                  aria-label="Download attachment"
                >
                  <Download className="w-4 h-4" />
                </a>
                {onDelete && (
                  <button
                    type="button"
                    onClick={() => onDelete(attachment.id)}
                    disabled={isDeleting}
                    aria-label="Delete attachment"
                    className={cn(
                      compact
                        ? 'p-1 rounded hover:bg-destructive/10'
                        : 'p-1.5 rounded hover:bg-destructive/10',
                      'text-muted-foreground hover:text-destructive',
                      'transition-colors disabled:opacity-50',
                    )}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <AttachmentPreview
        attachment={previewAttachment}
        isOpen={!!previewAttachment}
        onClose={() => setPreviewAttachment(null)}
      />
    </>
  )
}
