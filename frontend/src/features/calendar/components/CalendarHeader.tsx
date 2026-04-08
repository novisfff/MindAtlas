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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Calendar } from '@/components/ui/calendar'
import { cn } from '@/lib/utils'
import type { CalendarDensity, CalendarViewMode } from '../types'
import { calendarRadius, calendarSurface } from '../styles'

interface CalendarHeaderProps {
  density: CalendarDensity
  viewMode: CalendarViewMode
  currentDate: Date
  onViewChange: (mode: CalendarViewMode) => void
  onDateChange: (date: Date) => void
}

export function CalendarHeader({
  density,
  viewMode,
  currentDate,
  onViewChange,
  onDateChange,
}: CalendarHeaderProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const [pickerOpen, setPickerOpen] = useState(false)
  const controlShellClass = cn(calendarRadius.control, calendarSurface.control)

  const normalizedCurrentDate = useMemo(() => {
    if (viewMode === 'month') return startOfMonth(currentDate)
    if (viewMode === 'week')
      return startOfWeek(currentDate, { weekStartsOn: 1 })
    return currentDate
  }, [currentDate, viewMode])

  const handlePrev = () => {
    if (viewMode === 'month') onDateChange(subMonths(normalizedCurrentDate, 1))
    else if (viewMode === 'week')
      onDateChange(subWeeks(normalizedCurrentDate, 1))
    else onDateChange(subDays(normalizedCurrentDate, 1))
  }

  const handleNext = () => {
    if (viewMode === 'month') onDateChange(addMonths(normalizedCurrentDate, 1))
    else if (viewMode === 'week')
      onDateChange(addWeeks(normalizedCurrentDate, 1))
    else onDateChange(addDays(normalizedCurrentDate, 1))
  }

  const handleToday = () => {
    const today = new Date()
    if (viewMode === 'month') onDateChange(startOfMonth(today))
    else if (viewMode === 'week')
      onDateChange(startOfWeek(today, { weekStartsOn: 1 }))
    else onDateChange(today)
  }

  const getTitle = () => {
    if (viewMode === 'month')
      return format(normalizedCurrentDate, 'yyyy MMMM', { locale })
    if (viewMode === 'week') {
      const weekStart = startOfWeek(normalizedCurrentDate, { weekStartsOn: 1 })
      const weekEnd = endOfWeek(normalizedCurrentDate, { weekStartsOn: 1 })
      return `${format(weekStart, 'PP', { locale })} - ${format(weekEnd, 'PP', { locale })}`
    }
    return format(normalizedCurrentDate, 'PPPP', { locale })
  }

  const handlePickerSelect = (date: Date) => {
    if (viewMode === 'month') onDateChange(startOfMonth(date))
    else if (viewMode === 'week')
      onDateChange(startOfWeek(date, { weekStartsOn: 1 }))
    else onDateChange(date)
    setPickerOpen(false)
  }

  return (
    <div
      className={cn(
        calendarRadius.shell,
        calendarSurface.shell,
        density === 'compact' ? 'px-4 py-3' : 'px-4 py-4 md:px-5',
      )}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2.5 md:gap-3">
          <button
            type="button"
            onClick={() => {
              handleToday()
              setPickerOpen(false)
            }}
            className={cn(
              'inline-flex items-center font-medium text-foreground transition-all hover:-translate-y-0.5 hover:bg-muted/40 hover:shadow-md active:translate-y-0',
              controlShellClass,
              density === 'compact' ? 'h-10 px-4 text-sm' : 'h-11 px-4 text-sm',
            )}
          >
            {t('calendar.today')}
          </button>
          <div className={cn('inline-flex items-center p-1', controlShellClass)}>
            <button
              type="button"
              onClick={handlePrev}
              className={cn(
                calendarRadius.control,
                'p-2 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground',
              )}
              aria-label={t('calendar.prev')}
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={handleNext}
              className={cn(
                calendarRadius.control,
                'p-2 text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground',
              )}
              aria-label={t('calendar.next')}
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                className={cn(
                  'group flex min-w-[216px] flex-1 items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/40 sm:flex-none md:min-w-[260px]',
                  controlShellClass,
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-medium uppercase tracking-[0.24em] text-muted-foreground/75">
                    {t(`calendar.view.${viewMode}`)}
                  </div>
                  <h1
                    className={cn(
                      'mt-1 break-words font-semibold tracking-tight text-foreground',
                      density === 'compact'
                        ? 'text-xl leading-tight'
                        : 'text-2xl leading-tight',
                    )}
                  >
                    {getTitle()}
                  </h1>
                </div>
                <div
                  className={cn(
                    'flex h-9 w-9 shrink-0 items-center justify-center text-muted-foreground transition-colors group-hover:bg-background group-hover:text-foreground',
                    calendarRadius.micro,
                    calendarSurface.inset,
                  )}
                >
                  <ChevronDown className="h-4 w-4" />
                </div>
              </button>
            </PopoverTrigger>
            <PopoverContent
              className={cn(
                'w-auto p-0',
                calendarRadius.shell,
                calendarSurface.popover,
              )}
              align="start"
              sideOffset={10}
            >
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
                    from: startOfWeek(normalizedCurrentDate, {
                      weekStartsOn: 1,
                    }),
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

        <div
          className={cn(
            'inline-flex w-full bg-muted/55 p-1 sm:w-auto',
            controlShellClass,
          )}
        >
          {(['day', 'week', 'month'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => onViewChange(mode)}
              className={cn(
                'flex-1 px-4 text-sm font-medium transition-all sm:flex-none',
                calendarRadius.control,
                density === 'compact' ? 'py-2' : 'py-2.5',
                viewMode === mode
                  ? 'bg-background text-foreground shadow-sm ring-1 ring-border/60'
                  : 'text-muted-foreground hover:bg-background/70 hover:text-foreground',
              )}
            >
              {t(`calendar.view.${mode}`)}
            </button>
          ))}
        </div>
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
    return Array.from(
      { length: 12 },
      (_, month) => new Date(displayYear, month, 1),
    )
  }, [displayYear])

  return (
    <div className="w-[280px] p-4">
      <div className="flex items-center justify-between">
        <button
          type="button"
          className={cn(
            calendarRadius.micro,
            calendarSurface.inset,
            'p-2 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground',
          )}
          aria-label={t('calendar.prevYear')}
          onClick={() =>
            setDisplayYear((y) => subYears(new Date(y, 0, 1), 1).getFullYear())
          }
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div className="select-none text-sm font-semibold tracking-wide">
          {displayYear}
        </div>
        <button
          type="button"
          className={cn(
            calendarRadius.micro,
            calendarSurface.inset,
            'p-2 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground',
          )}
          aria-label={t('calendar.nextYear')}
          onClick={() =>
            setDisplayYear((y) => addYears(new Date(y, 0, 1), 1).getFullYear())
          }
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2">
        {months.map((date) => {
          const selected =
            value.getFullYear() === date.getFullYear() &&
            value.getMonth() === date.getMonth()
          return (
            <button
              key={date.toISOString()}
              type="button"
              className={cn(
                'h-10 text-sm font-medium transition-all',
                calendarRadius.control,
                selected
                  ? 'bg-primary text-primary-foreground shadow-sm'
                  : cn(
                      calendarSurface.inset,
                      'text-muted-foreground hover:bg-muted/70 hover:text-foreground',
                    ),
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
