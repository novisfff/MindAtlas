import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

interface TagSuggestionPanelProps {
  suggestedTags: string[]
  onAddTag: (tagName: string) => void
}

export function TagSuggestionPanel({ suggestedTags, onAddTag }: TagSuggestionPanelProps) {
  const { t } = useTranslation()

  if (suggestedTags.length === 0) return null

  return (
    <div className="mt-2 text-sm animate-in fade-in slide-in-from-top-1">
      <span className="text-muted-foreground mr-2">{t('entry.form.aiSuggestions')}:</span>
      <div className="inline-flex flex-wrap gap-1.5">
        {suggestedTags.map((tagName) => (
          <button
            key={tagName}
            type="button"
            onClick={() => onAddTag(tagName)}
            className={cn(
              "inline-flex items-center px-2 py-0.5 rounded-full text-xs transition-colors",
              "bg-purple-100 text-purple-700 hover:bg-purple-200",
              "dark:bg-purple-900/30 dark:text-purple-300 dark:hover:bg-purple-900/50"
            )}
          >
            <Plus className="w-3 h-3 mr-1" />
            {tagName}
          </button>
        ))}
      </div>
    </div>
  )
}
