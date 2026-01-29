import { useTranslation } from 'react-i18next'
import { getMonthDays, isToday, isCurrentMonth } from '../utils/dateUtils'
import { getEntriesForDate } from '../utils/layoutUtils'
import { CalendarCell } from './CalendarCell'
import type { Entry } from '@/types'

interface MonthViewProps {
  currentDate: Date
  entries: Entry[]
  onDateSelect: (date: Date) => void
  onDateDoubleClick?: (date: Date) => void
}

const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

export function MonthView({ currentDate, entries, onDateSelect, onDateDoubleClick }: MonthViewProps) {
  const { t } = useTranslation()
  const days = getMonthDays(currentDate)

  return (
    <div className="flex flex-col h-full">
      <div className="grid grid-cols-7 border-b">
        {WEEKDAYS.map((day) => (
          <div
            key={day}
            className="py-2 text-center text-sm font-medium text-muted-foreground"
          >
            {t(`calendar.weekday.${day}`)}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 flex-1">
        {days.map((day) => {
          const dayEntries = getEntriesForDate(entries, day)
          return (
            <CalendarCell
              key={day.toISOString()}
              date={day}
              entries={dayEntries}
              isToday={isToday(day)}
              isCurrentMonth={isCurrentMonth(day, currentDate)}
              onClick={() => onDateSelect(day)}
              onDoubleClick={() => onDateDoubleClick?.(day)}
            />
          )
        })}
      </div>
    </div>
  )
}
