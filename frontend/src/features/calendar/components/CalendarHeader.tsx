import { ChevronLeft, ChevronRight } from 'lucide-react'
import { format, addMonths, subMonths, addWeeks, subWeeks, addDays, subDays } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { CalendarViewMode } from '../CalendarPage'

interface CalendarHeaderProps {
  viewMode: CalendarViewMode
  currentDate: Date
  onViewChange: (mode: CalendarViewMode) => void
  onDateChange: (date: Date) => void
}

export function CalendarHeader({
  viewMode,
  currentDate,
  onViewChange,
  onDateChange,
}: CalendarHeaderProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS

  const handlePrev = () => {
    if (viewMode === 'month') onDateChange(subMonths(currentDate, 1))
    else if (viewMode === 'week') onDateChange(subWeeks(currentDate, 1))
    else onDateChange(subDays(currentDate, 1))
  }

  const handleNext = () => {
    if (viewMode === 'month') onDateChange(addMonths(currentDate, 1))
    else if (viewMode === 'week') onDateChange(addWeeks(currentDate, 1))
    else onDateChange(addDays(currentDate, 1))
  }

  const handleToday = () => onDateChange(new Date())

  const getTitle = () => {
    if (viewMode === 'month') return format(currentDate, 'yyyy MMMM', { locale })
    if (viewMode === 'week') return format(currentDate, 'yyyy MMMM', { locale })
    return format(currentDate, 'yyyy MMMM d EEEE', { locale })
  }

  return (
    <div className="flex items-center justify-between p-4 border-b">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-semibold">{getTitle()}</h1>
        <div className="flex items-center gap-1">
          <button
            onClick={handlePrev}
            className="p-1.5 rounded-md hover:bg-muted"
            aria-label={t('calendar.prev')}
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <button
            onClick={handleToday}
            className="px-3 py-1.5 text-sm rounded-md hover:bg-muted"
          >
            {t('calendar.today')}
          </button>
          <button
            onClick={handleNext}
            className="p-1.5 rounded-md hover:bg-muted"
            aria-label={t('calendar.next')}
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      </div>

      <div className="flex rounded-lg border overflow-hidden">
        {(['month', 'week', 'day'] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => onViewChange(mode)}
            className={cn(
              'px-3 py-1.5 text-sm transition-colors',
              viewMode === mode
                ? 'bg-primary text-primary-foreground'
                : 'hover:bg-muted'
            )}
          >
            {t(`calendar.view.${mode}`)}
          </button>
        ))}
      </div>
    </div>
  )
}
