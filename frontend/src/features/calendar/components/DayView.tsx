import { format } from 'date-fns'
import { enUS, zhCN } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { isToday } from '../utils/dateUtils'
import { getEntriesForDate } from '../utils/layoutUtils'
import { CalendarEvent } from './CalendarEvent'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'
import type { CalendarDensity } from '../types'
import { calendarRadius, calendarSurface } from '../styles'

interface DayViewProps {
  density: CalendarDensity
  currentDate: Date
  entries: Entry[]
  onDateDoubleClick?: (date: Date) => void
  onEntryClick?: (entry: Entry) => void
}

export function DayView({
  density,
  currentDate,
  entries,
  onDateDoubleClick,
  onEntryClick,
}: DayViewProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const dayEntries = getEntriesForDate(entries, currentDate)
  const isDense = density === 'compact'

  return (
    <div className="grid min-h-full gap-4 xl:grid-cols-[260px_minmax(0,1fr)]">
      <section
        className={cn(
          'p-5',
          calendarRadius.shell,
          calendarSurface.shell,
        )}
      >
        <div className="text-[11px] font-semibold uppercase tracking-[0.22em] text-muted-foreground/75">
          {format(currentDate, 'MMM', { locale })}
        </div>
        <div
          className={cn(
            'mt-4 flex items-center justify-center font-semibold shadow-sm',
            calendarRadius.panel,
            isDense ? 'h-20 text-4xl' : 'h-24 text-5xl',
            isToday(currentDate)
              ? 'bg-primary text-primary-foreground'
              : 'bg-background/90 text-foreground ring-1 ring-border/60 dark:bg-background/70',
          )}
        >
          {format(currentDate, 'd')}
        </div>
        <div className="mt-4 text-lg font-semibold tracking-tight text-foreground">
          {format(currentDate, 'EEEE', { locale })}
        </div>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          {dayEntries.length > 0
            ? format(currentDate, 'PPPP', { locale })
            : t('calendar.noEntries')}
        </p>
      </section>

      <section
        className={cn(
          calendarRadius.shell,
          calendarSurface.shell,
          isDense ? 'p-4' : 'p-5',
        )}
        onDoubleClick={() => onDateDoubleClick?.(currentDate)}
      >
        {dayEntries.length === 0 ? (
          <div
            className={cn(
              'flex min-h-[20rem] items-center justify-center px-6 text-center text-sm text-muted-foreground',
              calendarRadius.control,
              calendarSurface.inset,
            )}
          >
            {t('calendar.noEntries')}
          </div>
        ) : (
          <div className="space-y-3">
            {dayEntries.map((entry) => (
              <CalendarEvent
                key={entry.id}
                entry={entry}
                density={density}
                onClick={() => onEntryClick?.(entry)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
