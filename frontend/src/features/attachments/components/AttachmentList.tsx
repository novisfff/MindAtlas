import { File, Download, Trash2, Image, FileText, Loader2, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Attachment } from '@/types'
import { getDownloadUrl } from '../api/attachments'
import { cn } from '@/lib/utils'

interface AttachmentListProps {
  attachments: Attachment[]
  onDelete?: (id: string) => void
  onRetry?: (id: string) => void
  isDeleting?: boolean
  isRetrying?: boolean
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function getFileIcon(contentType: string) {
  if (contentType.startsWith('image/')) return Image
  if (contentType.includes('pdf') || contentType.includes('document')) return FileText
  return File
}

type KgUiStatusTone = 'muted' | 'info' | 'success' | 'danger'

function KnowledgeStatusBadge({ attachment }: { attachment: Attachment }) {
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
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] leading-none',
        toneClass
      )}
    >
      <span className={cn(spin && 'animate-spin')}>{icon}</span>
      <span className="whitespace-nowrap">{label}</span>
    </span>
  )
}

export function AttachmentList({ attachments, onDelete, onRetry, isDeleting, isRetrying }: AttachmentListProps) {
  const { t } = useTranslation()

  if (attachments.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-4 text-center">
        {t('entry.noAttachments')}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {attachments.map((attachment) => {
        const Icon = getFileIcon(attachment.contentType)
        return (
          <div
            key={attachment.id}
            className={cn(
              'flex items-center justify-between p-3 rounded-lg border bg-card',
              'hover:bg-accent/50 transition-colors'
            )}
          >
            <div className="flex items-center gap-3 min-w-0">
              <Icon className="w-5 h-5 text-muted-foreground flex-shrink-0" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium truncate">
                    {attachment.originalFilename}
                  </p>
                  <KnowledgeStatusBadge attachment={attachment} />
                </div>
                <p className="text-xs text-muted-foreground">
                  {formatFileSize(attachment.size)}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              {attachment.parseStatus === 'failed' && onRetry && (
                <button
                  type="button"
                  onClick={() => onRetry(attachment.id)}
                  disabled={isRetrying}
                  aria-label="Retry parse"
                  className="p-1.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
                >
                  <RefreshCw className="w-4 h-4" />
                </button>
              )}
              <a
                href={getDownloadUrl(attachment.id)}
                download
                className="p-1.5 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
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
                    'p-1.5 rounded hover:bg-destructive/10',
                    'text-muted-foreground hover:text-destructive',
                    'transition-colors disabled:opacity-50'
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
  )
}
