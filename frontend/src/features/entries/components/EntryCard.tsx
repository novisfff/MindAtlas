import { Calendar, Clock } from 'lucide-react'
import { KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Entry } from '@/types'
import { cn } from '@/lib/utils'

interface EntryCardProps {
  entry: Entry
  onClick?: (entry: Entry) => void
}

export function EntryCard({ entry, onClick }: EntryCardProps) {
  const { t } = useTranslation()
  const ariaTitle = entry.title || t('labels.unknown')

  const formatDate = (dateString?: string) => {
    if (!dateString) return ''
    const date = new Date(dateString)
    if (isNaN(date.getTime())) return '—'
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
    }
  }

  const renderTimeInfo = () => {
    if (entry.timeMode === 'NONE') return null

    // ... (renderTimeInfo body unchanged until we get to return) ...

    let timeText = ''
    if (entry.timeMode === 'POINT' && entry.timeAt) {
      timeText = formatDate(entry.timeAt)
    } else if (entry.timeMode === 'RANGE') {
      const from = entry.timeFrom ? formatDate(entry.timeFrom) : t('labels.unknown')
      const to = entry.timeTo ? formatDate(entry.timeTo) : t('time.present')
      timeText = `${from} - ${to}`
    }

    if (!timeText) return null

    return (
      <div className="flex items-center text-xs text-muted-foreground">
        <Clock className="w-3 h-3 mr-1" />
        <span>{timeText}</span>
      </div>
    )
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={t('entry.card.viewEntryAria', { title: ariaTitle })}
      className={cn(
        'group relative flex flex-col justify-between rounded-lg border bg-card text-card-foreground shadow-sm hover:shadow-md transition-shadow cursor-pointer overflow-hidden h-[200px] focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
      )}
      onClick={() => onClick?.(entry)}
      onKeyDown={handleKeyDown}
    >
      <div
        className="absolute left-0 top-0 bottom-0 w-1.5"
        style={{ backgroundColor: entry.type?.color || '#cbd5e1' }}
      />

      <div className="p-4 pl-5 flex flex-col h-full">
        <div className="flex justify-between items-start mb-2 gap-2">
          <h3 className="font-semibold text-lg line-clamp-1 group-hover:text-primary transition-colors">
            {entry.title}
          </h3>
          <span
            className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold transition-colors shrink-0"
            style={{
              backgroundColor: entry.type?.color ? `${entry.type.color}20` : undefined,
              borderColor: entry.type?.color || undefined,
              color: entry.type?.color || undefined,
            }}
          >
            {entry.type?.name || t('labels.unknown')}
          </span>
        </div>

        <p className="text-sm text-muted-foreground line-clamp-3 mb-auto">
          {entry.summary || entry.content || t('messages.noContent')}
        </p>

        <div className="mt-4 pt-2 border-t flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center">
            <Calendar className="w-3 h-3 mr-1" />
            <span>{formatDate(entry.createdAt)}</span>
          </div>
          {renderTimeInfo()}
        </div>
      </div>
    </div>
  )
}
