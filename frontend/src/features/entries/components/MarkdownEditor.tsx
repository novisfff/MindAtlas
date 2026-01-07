import { useState } from 'react'
import { cn } from '@/lib/utils'

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
  placeholder = 'Write your content here... (Markdown supported)',
  className,
  disabled,
}: MarkdownEditorProps) {
  const [tab, setTab] = useState<'write' | 'preview'>('write')

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
          Write
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
          Preview
        </button>
      </div>

      {tab === 'write' ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
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
            <div className="whitespace-pre-wrap">{value}</div>
          ) : (
            <p className="text-muted-foreground italic">Nothing to preview</p>
          )}
        </div>
      )}
    </div>
  )
}
