import { Link } from 'react-router-dom'
import { Calendar, CalendarRange } from 'lucide-react'
import type { Entry } from '@/types'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'

interface TimelineItemProps {
  entry: Entry
  isLast?: boolean
}


function formatDateI18n(dateString: string, locale: string): string {
  const date = new Date(dateString)
  return date.toLocaleDateString(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function TimelineItem({ entry, isLast }: TimelineItemProps) {
  const { t, i18n } = useTranslation()
  const timeDisplay = entry.timeMode === 'POINT' && entry.timeAt
    ? formatDateI18n(entry.timeAt, i18n.language)
    : entry.timeMode === 'RANGE' && entry.timeFrom
      ? `${formatDateI18n(entry.timeFrom, i18n.language)} - ${entry.timeTo ? formatDateI18n(entry.timeTo, i18n.language) : t('time.present')}`
      : null

  return (
    <div className="relative flex gap-4">
      {/* Timeline line */}
      {!isLast && (
        <div className="absolute left-[19px] top-10 bottom-0 w-0.5 bg-border" />
      )}

      {/* Timeline dot */}
      <div
        className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 bg-background"
        style={{ borderColor: entry.type.color || '#6B7280' }}
      >
        {entry.timeMode === 'RANGE' ? (
          <CalendarRange className="w-4 h-4" style={{ color: entry.type.color }} />
        ) : (
          <Calendar className="w-4 h-4" style={{ color: entry.type.color }} />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 pb-8">
        <Link
          to={`/entries/${entry.id}`}
          className={cn(
            'block p-4 rounded-lg border bg-card',
            'hover:border-primary/50 hover:shadow-sm transition-all'
          )}
        >
          <div className="flex items-start justify-between gap-2 mb-2">
            <h3 className="font-semibold">{entry.title}</h3>
            <span
              className="px-2 py-0.5 text-xs rounded-full shrink-0"
              style={{
                backgroundColor: `${entry.type.color}20`,
                color: entry.type.color,
              }}
            >
              {entry.type.name}
            </span>
          </div>

          {timeDisplay && (
            <p className="text-sm text-muted-foreground mb-2">{timeDisplay}</p>
          )}

          {entry.summary && (
            <p className="text-sm text-muted-foreground line-clamp-2">
              {entry.summary}
            </p>
          )}

          {entry.tags && entry.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-3">
              {entry.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag.id}
                  className="px-2 py-0.5 text-xs rounded-full bg-muted"
                >
                  {tag.name}
                </span>
              ))}
              {entry.tags.length > 3 && (
                <span className="text-xs text-muted-foreground">
                  +{entry.tags.length - 3}
                </span>
              )}
            </div>
          )}
        </Link>
      </div>
    </div>
  )
}
