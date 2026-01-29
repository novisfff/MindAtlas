import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { isToday } from '../utils/dateUtils'
import { getEntriesForDate } from '../utils/layoutUtils'
import { CalendarEvent } from './CalendarEvent'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface DayViewProps {
  currentDate: Date
  entries: Entry[]
  onDateDoubleClick?: (date: Date) => void
}

export function DayView({ currentDate, entries, onDateDoubleClick }: DayViewProps) {
  const { i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const dayEntries = getEntriesForDate(entries, currentDate)

  return (
    <div className="flex flex-col h-full p-4">
      <div className="text-center mb-4">
        <div
          className={cn(
            'w-12 h-12 mx-auto flex items-center justify-center rounded-full text-xl',
            isToday(currentDate) && 'bg-primary text-primary-foreground'
          )}
        >
          {format(currentDate, 'd')}
        </div>
        <div className="text-sm text-muted-foreground mt-1">
          {format(currentDate, 'EEEE', { locale })}
        </div>
      </div>
      <div
        className="flex-1 border rounded-lg p-4 space-y-2 cursor-pointer"
        onDoubleClick={() => onDateDoubleClick?.(currentDate)}
      >
        {dayEntries.length === 0 ? (
          <div className="text-center text-muted-foreground py-8">
            No entries for this day
          </div>
        ) : (
          dayEntries.map((entry) => (
            <CalendarEvent key={entry.id} entry={entry} />
          ))
        )}
      </div>
    </div>
  )
}
