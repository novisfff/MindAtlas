import { useState } from 'react'
import { Loader2, Download, X, AlertCircle, Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { Attachment } from '@/types'
import { getDownloadUrl, getPreviewUrl } from '../api/attachments'
import { useAttachmentMarkdownQuery } from '../queries'

interface AttachmentPreviewProps {
  attachment: Attachment | null
  isOpen: boolean
  onClose: () => void
}

function isImageFile(contentType: string): boolean {
  return contentType.startsWith('image/')
}

function isPdfFile(contentType: string): boolean {
  return contentType === 'application/pdf'
}

function isTextFile(filename: string): boolean {
  const ext = filename.toLowerCase().split('.').pop() || ''
  return ['txt', 'md', 'markdown'].includes(ext)
}

function needsMarkdownPreview(attachment: Attachment): boolean {
  if (isImageFile(attachment.contentType) || isPdfFile(attachment.contentType)) {
    return false
  }
  return isTextFile(attachment.originalFilename) || attachment.parseStatus === 'completed'
}

export function AttachmentPreview({ attachment, isOpen, onClose }: AttachmentPreviewProps) {
  if (!attachment) return null

  const previewUrl = getPreviewUrl(attachment.id)
  const downloadUrl = getDownloadUrl(attachment.id)

  const isImage = isImageFile(attachment.contentType)
  const isPdf = isPdfFile(attachment.contentType)
  const showMarkdown = needsMarkdownPreview(attachment)

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="max-w-4xl h-[85vh] flex flex-col p-0 gap-0">
        <PreviewHeader
          filename={attachment.originalFilename}
          downloadUrl={downloadUrl}
          onClose={onClose}
        />

        <div className="flex-1 overflow-auto bg-muted/30 relative min-h-0">
          {isImage && <ImagePreview url={previewUrl} filename={attachment.originalFilename} />}
          {isPdf && <PdfPreview url={previewUrl} />}
          {showMarkdown && <MarkdownPreview attachmentId={attachment.id} />}
          {!isImage && !isPdf && !showMarkdown && (
            <FallbackView downloadUrl={downloadUrl} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function PreviewHeader({
  filename,
  downloadUrl,
  onClose,
}: {
  filename: string
  downloadUrl: string
  onClose: () => void
}) {
  return (
    <div className="px-4 py-3 border-b flex items-center justify-between bg-background">
      <DialogTitle className="truncate pr-4 text-base">{filename}</DialogTitle>
      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" asChild className="h-8 w-8">
          <a href={downloadUrl} download title="Download">
            <Download className="w-4 h-4" />
          </a>
        </Button>
        <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
          <X className="w-4 h-4" />
        </Button>
      </div>
    </div>
  )
}

function ImagePreview({ url, filename }: { url: string; filename: string }) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  return (
    <div className="flex h-full items-center justify-center p-4">
      {loading && !error && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        </div>
      )}
      {error ? (
        <div className="text-center text-muted-foreground">
          <AlertCircle className="w-12 h-12 mx-auto mb-2" />
          <p>Failed to load image</p>
        </div>
      ) : (
        <img
          src={url}
          alt={filename}
          className={cn(
            'max-w-full max-h-full object-contain rounded shadow-sm',
            loading && 'opacity-0'
          )}
          onLoad={() => setLoading(false)}
          onError={() => {
            setLoading(false)
            setError(true)
          }}
        />
      )}
    </div>
  )
}

function PdfPreview({ url }: { url: string }) {
  return (
    <iframe
      src={url}
      className="w-full h-full border-0"
      title="PDF Preview"
    />
  )
}

function MarkdownPreview({ attachmentId }: { attachmentId: string }) {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useAttachmentMarkdownQuery(attachmentId, true)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-destructive">
        <AlertCircle className="w-12 h-12 mb-2" />
        <p>Failed to load content</p>
      </div>
    )
  }

  if (data?.state === 'processing') {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <Clock className="w-12 h-12 mb-2" />
        <p>Document is being processed...</p>
      </div>
    )
  }

  if (data?.state === 'failed') {
    return (
      <div className="flex flex-col items-center justify-center h-full text-destructive">
        <AlertCircle className="w-12 h-12 mb-2" />
        <p>Document parsing failed</p>
        {data.parseLastError && (
          <p className="text-sm mt-1 max-w-md text-center">{data.parseLastError}</p>
        )}
      </div>
    )
  }

  if (data?.state === 'unsupported' || !data?.markdown) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <AlertCircle className="w-12 h-12 mb-2" />
        <p>Preview not available for this file type</p>
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="prose prose-sm dark:prose-invert max-w-none bg-background p-6 rounded-lg shadow-sm">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {data.markdown}
        </ReactMarkdown>
      </div>
    </div>
  )
}

function FallbackView({ downloadUrl }: { downloadUrl: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-4">
      <AlertCircle className="w-12 h-12" />
      <p>Preview not available for this file type</p>
      <Button variant="outline" asChild>
        <a href={downloadUrl} download>
          <Download className="w-4 h-4 mr-2" />
          Download file
        </a>
      </Button>
    </div>
  )
}
