import { ChevronLeft, ChevronRight, ChevronDown } from 'lucide-react'
import {
  addDays,
  addMonths,
  addWeeks,
  addYears,
  endOfWeek,
  format,
  startOfMonth,
  startOfWeek,
  subDays,
  subMonths,
  subWeeks,
  subYears,
} from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Calendar } from '@/components/ui/calendar'
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
  const [pickerOpen, setPickerOpen] = useState(false)

  const normalizedCurrentDate = useMemo(() => {
    if (viewMode === 'month') return startOfMonth(currentDate)
    if (viewMode === 'week') return startOfWeek(currentDate, { weekStartsOn: 1 })
    return currentDate
  }, [currentDate, viewMode])

  const handlePrev = () => {
    if (viewMode === 'month') onDateChange(subMonths(normalizedCurrentDate, 1))
    else if (viewMode === 'week') onDateChange(subWeeks(normalizedCurrentDate, 1))
    else onDateChange(subDays(normalizedCurrentDate, 1))
  }

  const handleNext = () => {
    if (viewMode === 'month') onDateChange(addMonths(normalizedCurrentDate, 1))
    else if (viewMode === 'week') onDateChange(addWeeks(normalizedCurrentDate, 1))
    else onDateChange(addDays(normalizedCurrentDate, 1))
  }

  const handleToday = () => {
    const today = new Date()
    if (viewMode === 'month') onDateChange(startOfMonth(today))
    else if (viewMode === 'week') onDateChange(startOfWeek(today, { weekStartsOn: 1 }))
    else onDateChange(today)
  }

  const getTitle = () => {
    if (viewMode === 'month') return format(normalizedCurrentDate, 'yyyy MMMM', { locale })
    if (viewMode === 'week') {
      const weekStart = startOfWeek(normalizedCurrentDate, { weekStartsOn: 1 })
      const weekEnd = endOfWeek(normalizedCurrentDate, { weekStartsOn: 1 })
      return `${format(weekStart, 'PP', { locale })} - ${format(weekEnd, 'PP', { locale })}`
    }
    return format(normalizedCurrentDate, 'PPPP', { locale })
  }

  const handlePickerSelect = (date: Date) => {
    if (viewMode === 'month') onDateChange(startOfMonth(date))
    else if (viewMode === 'week') onDateChange(startOfWeek(date, { weekStartsOn: 1 }))
    else onDateChange(date)
    setPickerOpen(false)
  }

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          <button
            onClick={() => {
              handleToday()
              setPickerOpen(false)
            }}
            className="px-3 py-1.5 text-sm bg-white border rounded-md shadow-sm hover:bg-gray-50 active:scale-95 transition-all"
          >
            {t('calendar.today')}
          </button>
          <div className="flex items-center">
            <button
              onClick={handlePrev}
              className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              aria-label={t('calendar.prev')}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              onClick={handleNext}
              className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              aria-label={t('calendar.next')}
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>

        <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
          <PopoverTrigger asChild>
            <div className="flex items-center gap-1 cursor-pointer hover:bg-muted px-2 py-1 rounded-md transition-colors">
              <h1 className="text-lg font-semibold">{getTitle()}</h1>
              <ChevronDown className="w-4 h-4 text-muted-foreground" />
            </div>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            {viewMode === 'month' ? (
              <MonthPicker
                value={normalizedCurrentDate}
                locale={locale}
                onChange={handlePickerSelect}
                onRequestClose={() => setPickerOpen(false)}
              />
            ) : viewMode === 'week' ? (
              <Calendar
                mode="range"
                selected={{
                  from: startOfWeek(normalizedCurrentDate, { weekStartsOn: 1 }),
                  to: endOfWeek(normalizedCurrentDate, { weekStartsOn: 1 }),
                }}
                onDayClick={(date) => handlePickerSelect(date)}
                locale={locale}
                weekStartsOn={1}
                initialFocus
              />
            ) : (
              <Calendar
                mode="single"
                selected={normalizedCurrentDate}
                onSelect={(date) => date && handlePickerSelect(date)}
                locale={locale}
                weekStartsOn={1}
                initialFocus
              />
            )}
          </PopoverContent>
        </Popover>
      </div>

      <div className="flex bg-muted p-1 rounded-lg">
        {(['day', 'week', 'month'] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => onViewChange(mode)}
            className={cn(
              'px-4 py-1.5 text-sm font-medium rounded-md transition-all',
              viewMode === mode
                ? 'bg-white text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t(`calendar.view.${mode}`)}
          </button>
        ))}
      </div>
    </div>
  )
}

function MonthPicker({
  value,
  locale,
  onChange,
  onRequestClose,
}: {
  value: Date
  locale: typeof zhCN
  onChange: (date: Date) => void
  onRequestClose: () => void
}) {
  const { t } = useTranslation()
  const [displayYear, setDisplayYear] = useState(() => value.getFullYear())

  useEffect(() => {
    setDisplayYear(value.getFullYear())
  }, [value])

  const months = useMemo(() => {
    return Array.from({ length: 12 }, (_, month) => new Date(displayYear, month, 1))
  }, [displayYear])

  return (
    <div className="p-3 w-[260px]">
      <div className="flex items-center justify-between">
        <button
          type="button"
          className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          aria-label={t('calendar.prevYear')}
          onClick={() => setDisplayYear((y) => subYears(new Date(y, 0, 1), 1).getFullYear())}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div className="text-sm font-medium select-none">{displayYear}</div>
        <button
          type="button"
          className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          aria-label={t('calendar.nextYear')}
          onClick={() => setDisplayYear((y) => addYears(new Date(y, 0, 1), 1).getFullYear())}
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-1">
        {months.map((date) => {
          const selected = value.getFullYear() === date.getFullYear() && value.getMonth() === date.getMonth()
          return (
            <button
              key={date.toISOString()}
              type="button"
              className={cn(
                'h-9 rounded-md text-sm font-medium transition-colors',
                selected ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
              )}
              onClick={() => {
                onChange(date)
                onRequestClose()
              }}
            >
              {format(date, 'MMM', { locale })}
            </button>
          )
        })}
      </div>
    </div>
  )
}
