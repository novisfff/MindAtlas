import { useRef, useState } from 'react'
import { Upload, Loader2, FileUp } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Switch } from '@/components/ui/switch'

interface FileUploadProps {
  onUpload: (file: File, indexToKnowledgeGraph: boolean) => void
  isUploading?: boolean
  accept?: string
}

export function FileUpload({ onUpload, isUploading, accept }: FileUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [indexToKg, setIndexToKg] = useState(true)
  const { t } = useTranslation()

  const handleFile = (file: File) => {
    onUpload(file, indexToKg)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.[0]) {
      handleFile(e.target.files[0])
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      inputRef.current?.click()
    }
  }

  return (
    <div className="space-y-4">
      <div
        role="button"
        tabIndex={0}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        onKeyDown={handleKeyDown}
        className={cn(
          'relative border-2 border-dashed rounded-lg p-8 text-center transition-all cursor-pointer group',
          'focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary',
          dragOver
            ? 'border-primary bg-primary/5 scale-[1.01]'
            : 'border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/50',
          isUploading && 'opacity-50 pointer-events-none'
        )}
        aria-label={t('attachment.dropFile')}
      >
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          onChange={handleFileSelect}
          className="hidden"
          aria-label="Upload file"
        />

        <div className="flex flex-col items-center gap-3">
          <div className={cn(
            "p-3 rounded-full bg-muted transition-colors group-hover:bg-background shadow-sm",
            dragOver && "bg-background"
          )}>
            {isUploading ? (
              <Loader2 className="w-6 h-6 text-primary animate-spin" />
            ) : (
              <Upload className="w-6 h-6 text-muted-foreground group-hover:text-primary transition-colors" />
            )}
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium text-foreground">
              {isUploading ? t('messages.loading') : t('attachment.dropFile')}
            </p>
            <p className="text-xs text-muted-foreground">
              {t('attachment.supports')}
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between px-1">
        <div className="flex items-center space-x-2">
          <Switch
            id="index-mode"
            checked={indexToKg}
            onCheckedChange={setIndexToKg}
          />
          <label
            htmlFor="index-mode"
            className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer select-none"
          >
            {t('attachment.indexToKnowledgeGraph', 'Index to Knowledge Graph')}
          </label>
        </div>
        {/* Optional: Add help text or tooltip if needed */}
      </div>
    </div>

  )
}
