import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useDroppable } from '@dnd-kit/core'
import { useTranslation } from 'react-i18next'
import { getWeekDays, isToday } from '../utils/dateUtils'
import { getEntriesForDate } from '../utils/layoutUtils'
import { CalendarEvent } from './CalendarEvent'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface WeekViewProps {
  currentDate: Date
  entries: Entry[]
  onDateSelect: (date: Date) => void
  onDateDoubleClick?: (date: Date) => void
}

export function WeekView({ currentDate, entries, onDateSelect, onDateDoubleClick }: WeekViewProps) {
  const { i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const days = getWeekDays(currentDate)

  return (
    <div className="flex flex-col h-full">
      <div className="grid grid-cols-7 border-b">
        {days.map((day) => (
          <div
            key={day.toISOString()}
            onClick={() => onDateSelect(day)}
            className={cn(
              'py-3 text-center cursor-pointer hover:bg-muted/50',
              'border-r last:border-r-0'
            )}
          >
            <div className="text-xs text-muted-foreground">
              {format(day, 'EEE', { locale })}
            </div>
            <div
              className={cn(
                'w-8 h-8 mx-auto flex items-center justify-center rounded-full',
                isToday(day) && 'bg-primary text-primary-foreground'
              )}
            >
              {format(day, 'd')}
            </div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 flex-1">
        {days.map((day) => {
          const dayEntries = getEntriesForDate(entries, day)
          const dateId = format(day, 'yyyy-MM-dd')
          return (
            <WeekDayColumn
              key={day.toISOString()}
              dateId={dateId}
              entries={dayEntries}
              onDoubleClick={() => onDateDoubleClick?.(day)}
            />
          )
        })}
      </div>
    </div>
  )
}

function WeekDayColumn({
  dateId,
  entries,
  onDoubleClick,
}: {
  dateId: string
  entries: Entry[]
  onDoubleClick?: () => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: dateId })

  return (
    <div
      ref={setNodeRef}
      onDoubleClick={onDoubleClick}
      className={cn(
        'border-r last:border-r-0 p-2 min-h-[300px] space-y-1',
        isOver && 'bg-primary/10'
      )}
    >
      {entries.map((entry) => (
        <CalendarEvent key={entry.id} entry={entry} />
      ))}
    </div>
  )
}
