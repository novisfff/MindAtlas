import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Loader2, ArrowRight } from 'lucide-react'
import { addMonths, endOfMonth, format, startOfMonth, subMonths } from 'date-fns'
import { enUS, zhCN } from 'date-fns/locale'
import { DayButton } from 'react-day-picker'
import { useHeatmapQuery, useDayEntriesQuery } from '../queries'
import { useEntryQuery } from '@/features/entries/queries'
import { EntryDetailDialog } from '@/features/calendar/components/EntryDetailDialog'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Calendar, CalendarDayButton } from '@/components/ui/calendar'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { ScrollArea } from '@/components/ui/scroll-area'

export function MiniCalendar() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const [currentMonth, setCurrentMonth] = useState(new Date())
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null)
  const locale = i18n.language === 'zh' ? zhCN : enUS

  const heatmapParams = useMemo(
    () => ({
      startDate: format(startOfMonth(currentMonth), 'yyyy-MM-dd'),
      endDate: format(endOfMonth(currentMonth), 'yyyy-MM-dd'),
    }),
    [currentMonth]
  )
  const { data: heatmapData, isLoading } = useHeatmapQuery(heatmapParams)
  const { data: selectedEntry, isLoading: isEntryLoading } = useEntryQuery(selectedEntryId ?? undefined)

  const dayInfoByDate = useMemo(() => {
    const map = new Map<string, { totalCount: number; startCount: number; spanCount: number }>()
    heatmapData?.data.forEach((day) => {
      const totalCount = day.count ?? 0
      const startCount = (day.pointCount ?? 0) + (day.rangeStartCount ?? 0)
      const spanCount = Math.max(0, (day.rangeActiveCount ?? 0) - (day.rangeStartCount ?? 0))
      map.set(day.date, { totalCount, startCount, spanCount })
    })
    return map
  }, [heatmapData])

  const getColorClass = useCallback((count: number) => {
    // Restored Green/Emerald theme as requested, but keeping modern feel
    if (count === 0) return 'bg-transparent text-muted-foreground hover:bg-accent/40'
    if (count <= 2) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300 border border-transparent'
    if (count <= 5) return 'bg-emerald-300 text-emerald-950 dark:bg-emerald-600/50 dark:text-emerald-100 border border-transparent'
    return 'bg-emerald-500 text-white shadow-sm dark:bg-emerald-600 dark:text-white border border-transparent'
  }, [])

  const getSpanOnlyClass = useCallback(() => {
    return 'bg-teal-50/80 text-teal-700 hover:bg-teal-100/80 dark:bg-teal-900/20 dark:text-teal-300 dark:hover:bg-teal-900/30'
  }, [])

  const handleDayClick = (date: Date) => {
    navigate(`/calendar?date=${format(date, 'yyyy-MM-dd')}`)
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border bg-card/60 p-4 shadow-sm backdrop-blur-sm">
        <div className="flex items-center justify-center h-52">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border bg-card shadow-sm h-full flex flex-col overflow-hidden transition-all duration-300 hover:shadow-md">
      <div className="flex items-center justify-between p-3.5 pb-2 border-b border-border/40 bg-muted/5">
        <h3 className="text-[0.925rem] font-semibold pl-1 tracking-tight text-foreground/90">
          {format(currentMonth, 'MMMM yyyy', { locale })}
        </h3>
        <div className="flex gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground hover:bg-accent/50"
            aria-label={t('calendar.prev')}
            onClick={() => setCurrentMonth(subMonths(currentMonth, 1))}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground hover:bg-accent/50"
            aria-label={t('calendar.next')}
            onClick={() => setCurrentMonth(addMonths(currentMonth, 1))}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="p-3 flex-1 min-h-0 bg-card/30">
        <Calendar
          mode="single"
          month={currentMonth}
          onMonthChange={setCurrentMonth}
          locale={locale}
          weekStartsOn={1}
          fixedWeeks
          className={cn('w-full p-0 [--cell-size:1.8rem]')}
          classNames={{
            nav: 'hidden',
            month_caption: 'hidden',
            weekdays: 'flex mb-2',
            weekday: 'text-[11px] font-medium text-muted-foreground/60 w-full text-center uppercase tracking-wider',
            week: 'flex w-full mt-0.5',
            day: 'group/day relative flex-1 p-0.5 font-normal aria-selected:opacity-100 focus-within:relative focus-within:z-20',
            today: 'bg-transparent',
          }}
          components={{
            DayButton: ({ day, modifiers, className, ...props }: React.ComponentProps<typeof DayButton>) => {
              const dateStr = format(day.date, 'yyyy-MM-dd')
              const info = dayInfoByDate.get(dateStr)
              const totalCount = info?.totalCount ?? 0
              const startCount = info?.startCount ?? 0
              const spanCount = info?.spanCount ?? 0
              const inMonth = !modifiers.outside

              if (!inMonth) {
                return (
                  <CalendarDayButton
                    day={day}
                    modifiers={modifiers}
                    className="text-muted-foreground/10 opacity-30 cursor-default"
                    disabled
                    {...props}
                  />
                )
              }

              const dayButton = (
                <CalendarDayButton
                  day={day}
                  modifiers={modifiers}
                  className={cn(
                    'rounded-lg text-xs font-medium relative w-full h-full aspect-square flex items-center justify-center',
                    'transition-all duration-200 ease-in-out',
                    startCount > 0
                      ? getColorClass(startCount)
                      : spanCount > 0
                        ? getSpanOnlyClass()
                        : 'text-foreground/70 hover:bg-accent/50 hover:text-foreground hover:scale-105 hover:shadow-sm',
                    spanCount > 0 &&
                    "after:content-[''] after:absolute after:left-2 after:right-2 after:bottom-1.5 after:h-[2px] after:rounded-full after:bg-emerald-400/60 dark:after:bg-emerald-400/50",
                    modifiers.today && !startCount && 'ring-1 ring-emerald-500/40 text-emerald-600 font-semibold bg-emerald-500/5',
                    className
                  )}
                  {...props}
                />
              )

              if (totalCount === 0) {
                return dayButton
              }

              return (
                <DayHoverCard
                  date={day.date}
                  totalCount={totalCount}
                  locale={locale}
                  onNavigate={() => handleDayClick(day.date)}
                  onEntryOpen={(id) => setSelectedEntryId(id)}
                >
                  {dayButton}
                </DayHoverCard>
              )
            },
          }}
        />
      </div>
      <EntryDetailDialog
        entry={selectedEntry ?? null}
        open={Boolean(selectedEntryId)}
        loading={isEntryLoading}
        onOpenChange={(open) => !open && setSelectedEntryId(null)}
      />
    </div>
  )
}

interface DayHoverCardProps {
  date: Date
  totalCount: number
  locale: typeof zhCN | typeof enUS
  onNavigate: () => void
  onEntryOpen: (id: string) => void
  children: React.ReactNode
}

function DayHoverCard({ date, totalCount, locale, onNavigate, onEntryOpen, children }: DayHoverCardProps) {
  const { t } = useTranslation()
  const [isOpen, setIsOpen] = useState(false)
  const dateStr = format(date, 'yyyy-MM-dd')

  const { data: dayEntriesData, isLoading } = useDayEntriesQuery(dateStr, {
    enabled: isOpen && totalCount > 0,
  })

  return (
    <HoverCard onOpenChange={setIsOpen} openDelay={200}>
      <HoverCardTrigger asChild>
        <span className="block w-full h-full">{children}</span>
      </HoverCardTrigger>
      <HoverCardContent className="w-72 p-0 overflow-hidden border-border/60 shadow-xl bg-card/95 backdrop-blur-sm" align="center" side="top">
        {/* Header */}
        <div className="px-3.5 py-2.5 flex items-center justify-between bg-muted/30 border-b border-border/40">
          <div className="flex flex-col gap-0.5">
            <h4 className="text-sm font-semibold tracking-tight text-foreground">
              {format(date, 'MMM d, yyyy', { locale })}
            </h4>
            <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">
              {format(date, 'EEEE', { locale })}
            </span>
          </div>
          <div className="bg-emerald-500/10 text-emerald-600 text-[10px] font-bold px-2 py-0.5 rounded-full border border-emerald-500/20 shadow-sm">
            {t('dashboard.miniCalendar.entriesOnDate', { count: totalCount, date: '' }).replace(': ', '').trim()}
          </div>
        </div>

        {/* Content */}
        <div className="py-1">
          <ScrollArea className="h-[200px]">
            <div className="px-2 py-1">
              {isLoading ? (
                <div className="flex justify-center py-10">
                  <Loader2 className="h-5 w-5 animate-spin text-emerald-500/50" />
                </div>
              ) : (
                <div className="space-y-1">
                  {dayEntriesData?.entries.map((entry) => (
                    <div
                      key={entry.id}
                      className="group flex items-center gap-2.5 text-xs p-2 rounded-md hover:bg-muted/50 transition-all cursor-pointer border border-transparent hover:border-border/40"
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        setIsOpen(false)
                        onEntryOpen(entry.id)
                      }}
                    >
                      <span
                        className={cn(
                          'w-2 h-2 rounded-full shrink-0 shadow-sm ring-1 ring-background/50',
                          entry.coverKind === 'RANGE_SPAN' && 'ring-offset-1',
                        )}
                        style={{ backgroundColor: entry.typeColor || '#888888' }}
                      />
                      <span className="truncate flex-1 font-medium text-foreground/90 group-hover:text-emerald-600 transition-colors">
                        {entry.title}
                      </span>
                      {entry.timeMode === 'RANGE' && (
                        <span className="text-[10px] text-muted-foreground/70 px-1.5 py-0.5 bg-muted rounded-sm shrink-0 border border-border/30">
                          {entry.coverKind === 'RANGE_START' ? '▶' : '↔'}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* Footer */}
        <div className="p-1 border-t border-border/40 bg-muted/20">
          <button
            type="button"
            className="w-full flex items-center justify-center gap-1 text-[10px] font-medium text-muted-foreground hover:text-emerald-600 py-1 hover:bg-background/80 rounded transition-all group border border-transparent hover:border-border/30 hover:shadow-sm"
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onNavigate()
            }}
          >
            {t('actions.viewAll')} <ArrowRight className="h-2.5 w-2.5 transition-transform group-hover:translate-x-0.5" />
          </button>
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
