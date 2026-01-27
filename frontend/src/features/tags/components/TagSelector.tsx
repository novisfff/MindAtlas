import { Check } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTagsQuery } from '../queries'
import { cn } from '@/lib/utils'

interface TagSelectorProps {
  value: string[]
  onChange: (value: string[]) => void
  disabled?: boolean
}

export function TagSelector({ value, onChange, disabled }: TagSelectorProps) {
  const { t } = useTranslation()
  const { data: tags = [], isLoading } = useTagsQuery()

  const toggleTag = (tagId: string) => {
    if (disabled) return
    const next = value.includes(tagId)
      ? value.filter(id => id !== tagId)
      : [...value, tagId]
    onChange(next)
  }

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">{t('messages.loading')}</div>
  }

  return (
    <div className="flex flex-wrap gap-2">
      {tags.map(tag => {
        const selected = value.includes(tag.id)
        return (
          <button
            key={tag.id}
            type="button"
            aria-pressed={selected}
            onClick={() => toggleTag(tag.id)}
            disabled={disabled}
            className={cn(
              'inline-flex items-center px-3 py-1 rounded-full text-sm border transition-colors',
              selected
                ? 'border-transparent bg-primary/10 text-primary hover:bg-primary/20'
                : 'border-input bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              disabled && 'opacity-50 cursor-not-allowed'
            )}
            style={
              selected && tag.color
                ? { backgroundColor: tag.color + '20', color: tag.color, borderColor: tag.color }
                : undefined
            }
          >
            {tag.name}
            {selected && <Check className="ml-1.5 h-3.5 w-3.5" aria-hidden="true" />}
          </button>
        )
      })}
      {tags.length === 0 && (
        <span className="text-sm text-muted-foreground italic">{t('messages.noTags')}</span>
      )}
    </div>
  )
}
