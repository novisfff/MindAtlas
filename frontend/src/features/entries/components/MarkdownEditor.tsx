import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { remarkCitation } from '@/features/assistant/components/remark-citation'
import { CitationMarker } from '@/features/assistant/components/citation'

interface MarkdownEditorProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
}

export function MarkdownEditor({
  value,
  onChange,
  placeholder,
  className,
  disabled,
}: MarkdownEditorProps) {
  const [tab, setTab] = useState<'write' | 'preview'>('write')
  const { t } = useTranslation()

  // Use prop placeholder if provided, otherwise default to translated
  const displayPlaceholder = placeholder || t('entry.form.contentPlaceholder')

  return (
    <div className={cn('border rounded-lg overflow-hidden', className)}>
      <div className="flex border-b bg-muted/30">
        <button
          type="button"
          className={cn(
            'px-4 py-2 text-sm font-medium transition-colors',
            tab === 'write'
              ? 'bg-background text-foreground border-b-2 border-primary'
              : 'text-muted-foreground hover:text-foreground'
          )}
          onClick={() => setTab('write')}
        >
          {t('entry.form.write')}
        </button>
        <button
          type="button"
          className={cn(
            'px-4 py-2 text-sm font-medium transition-colors',
            tab === 'preview'
              ? 'bg-background text-foreground border-b-2 border-primary'
              : 'text-muted-foreground hover:text-foreground'
          )}
          onClick={() => setTab('preview')}
        >
          {t('entry.form.preview')}
        </button>
      </div>

      {tab === 'write' ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={displayPlaceholder}
          disabled={disabled}
          className={cn(
            'w-full min-h-[300px] p-4 resize-y bg-background text-foreground',
            'focus:outline-none focus:ring-0',
            'placeholder:text-muted-foreground',
            'font-mono text-sm'
          )}
        />
      ) : (
        <div className="min-h-[300px] p-4 prose prose-sm dark:prose-invert max-w-none">
          {value ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkCitation]}
              components={
                {
                  'citation-marker': ({ identifier }: { identifier: string }) => (
                    <CitationMarker identifier={identifier} label={identifier} />
                  ),
                } as any
              }
            >
              {value}
            </ReactMarkdown>
          ) : (
            <p className="text-muted-foreground italic">{t('entry.form.nothingToPreview')}</p>
          )}
        </div>
      )}
    </div>
  )
}
