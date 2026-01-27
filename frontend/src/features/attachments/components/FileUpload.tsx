import { useRef, useState } from 'react'
import { Upload, Loader2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

interface FileUploadProps {
  onUpload: (file: File) => void
  isUploading?: boolean
  accept?: string
}

export function FileUpload({ onUpload, isUploading, accept }: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const { t } = useTranslation()

  const handleFile = (file: File) => {
    onUpload(file)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={cn(
        'border-2 border-dashed rounded-lg p-6 text-center transition-colors',
        dragOver ? 'border-primary bg-primary/5' : 'border-muted-foreground/25',
        isUploading && 'opacity-50 pointer-events-none'
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
        className="hidden"
        id="file-upload"
        aria-label="Upload file"
      />
      <label
        htmlFor="file-upload"
        className="cursor-pointer flex flex-col items-center gap-2"
      >
        {isUploading ? (
          <Loader2 className="w-8 h-8 text-muted-foreground animate-spin" />
        ) : (
          <Upload className="w-8 h-8 text-muted-foreground" />
        )}
        <span className="text-sm text-muted-foreground">
          {isUploading ? t('messages.loading') : t('entry.dropFile')}
        </span>
      </label>
    </div>
  )
}
