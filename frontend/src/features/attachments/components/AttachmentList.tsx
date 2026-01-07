import { File, Download, Trash2, Image, FileText } from 'lucide-react'
import type { Attachment } from '@/types'
import { getDownloadUrl } from '../api/attachments'
import { cn } from '@/lib/utils'

interface AttachmentListProps {
  attachments: Attachment[]
  onDelete?: (id: string) => void
  isDeleting?: boolean
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

export function AttachmentList({ attachments, onDelete, isDeleting }: AttachmentListProps) {
  if (attachments.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-4 text-center">
        No attachments yet
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
                <p className="text-sm font-medium truncate">
                  {attachment.originalFilename}
                </p>
                <p className="text-xs text-muted-foreground">
                  {formatFileSize(attachment.size)}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
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
