import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Loader2, ArrowRight } from 'lucide-react'
import { addMonths, format, subMonths } from 'date-fns'
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

  const { data: heatmapData, isLoading } = useHeatmapQuery({ months: 3 })
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
    if (count === 0) return 'bg-muted/30 text-muted-foreground hover:bg-muted/40'
    if (count <= 2) return 'bg-emerald-200 text-emerald-950 dark:bg-emerald-900 dark:text-emerald-50'
    if (count <= 5) return 'bg-emerald-400 text-emerald-950 dark:bg-emerald-700 dark:text-emerald-50'
    return 'bg-emerald-600 text-white dark:bg-emerald-500 dark:text-emerald-950'
  }, [])

  const getSpanOnlyClass = useCallback(() => {
    return 'bg-sky-100/60 text-sky-950 hover:bg-sky-100/80 dark:bg-sky-900/35 dark:text-sky-50 dark:hover:bg-sky-900/45'
  }, [])

  const handleDayClick = (date: Date) => {
    navigate(`/calendar?date=${format(date, 'yyyy-MM-dd')}`)
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border bg-card p-4 shadow-sm">
        <div className="flex items-center justify-center h-48">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border bg-card shadow-sm h-full flex flex-col overflow-hidden">
      <div className="flex items-center justify-between p-3 pb-1 border-b bg-muted/10">
        <h3 className="text-sm font-semibold pl-1">
          {format(currentMonth, 'MMMM yyyy', { locale })}
        </h3>
        <div className="flex gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 hover:bg-background"
            aria-label={t('calendar.prev')}
            onClick={() => setCurrentMonth(subMonths(currentMonth, 1))}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 hover:bg-background"
            aria-label={t('calendar.next')}
            onClick={() => setCurrentMonth(addMonths(currentMonth, 1))}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      <div className="p-2 flex-1 min-h-0 bg-card/50">
        <Calendar
          mode="single"
          month={currentMonth}
          onMonthChange={setCurrentMonth}
          locale={locale}
          weekStartsOn={1}
          fixedWeeks
          className={cn('w-full p-0 [--cell-size:2.2rem]')}
          classNames={{
            nav: 'hidden',
            month_caption: 'hidden',
            weekdays: 'flex',
            weekday: 'text-[10px] font-medium text-muted-foreground/70 flex-1 text-center py-1',
            week: 'mt-1 flex w-full',
            day: 'group/day relative flex-1 p-0 font-normal aria-selected:opacity-100',
            today: 'bg-transparent',
          }}
          onDayClick={(date, modifiers) => {
            if (modifiers.outside) return
            handleDayClick(date)
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
                    className="text-muted-foreground/20 opacity-50"
                    {...props}
                  />
                )
              }

              const dayButton = (
                <CalendarDayButton
                  day={day}
                  modifiers={modifiers}
                  className={cn(
                    'rounded-lg text-xs font-medium relative',
                    'transition-colors',
                    startCount > 0
                      ? getColorClass(startCount)
                      : spanCount > 0
                        ? getSpanOnlyClass()
                        : 'bg-muted/20 text-muted-foreground hover:bg-muted/30',
                    spanCount > 0 &&
                    "after:content-[''] after:absolute after:left-2 after:right-2 after:bottom-1 after:h-0.5 after:rounded-full after:bg-sky-600/70 dark:after:bg-sky-300/70",
                    modifiers.today && 'ring-1.5 ring-primary ring-offset-1 ring-offset-background',
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
        <span className="block">{children}</span>
      </HoverCardTrigger>
      <HoverCardContent className="w-64 p-0 overflow-hidden border-border/50 shadow-xl" align="start" side="right">
        {/* Header */}
        <div className="px-3 py-2 flex items-center justify-between bg-muted/40 border-b border-border/40">
          <div className="flex flex-col">
            <h4 className="text-sm font-semibold tracking-tight text-foreground">
              {format(date, 'MMM d, yyyy', { locale })}
            </h4>
          </div>
          <div className="bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60 text-[10px] font-medium px-2 py-0.5 rounded-full border shadow-sm text-foreground/80">
            {t('dashboard.miniCalendar.entriesOnDate', { count: totalCount, date: '' }).replace(': ', '').trim()}
          </div>
        </div>

        {/* Content */}
        <div className="py-1">
          <ScrollArea className="h-[180px]">
            <div className="px-2">
              {isLoading ? (
                <div className="flex justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground/50" />
                </div>
              ) : (
                <div className="space-y-0.5">
                  {dayEntriesData?.entries.map((entry) => (
                    <div
                      key={entry.id}
                      className="group flex items-center gap-2 text-xs p-1.5 rounded-md hover:bg-accent/50 transition-all cursor-pointer border border-transparent hover:border-border/50"
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        setIsOpen(false)
                        onEntryOpen(entry.id)
                      }}
                    >
                      <span
                        className={cn(
                          'w-2 h-2 rounded-full shrink-0 shadow-sm ring-1 ring-background',
                          entry.coverKind === 'RANGE_SPAN' && 'ring-offset-1',
                        )}
                        style={{ backgroundColor: entry.typeColor || '#888888' }}
                      />
                      <span className="truncate flex-1 font-medium text-foreground/80 group-hover:text-foreground transition-colors">
                        {entry.title}
                      </span>
                      {entry.timeMode === 'RANGE' && (
                        <span className="text-[10px] text-muted-foreground px-1 bg-muted rounded shrink-0">
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
        <div className="p-1.5 border-t border-border/40 bg-muted/40">
          <button
            type="button"
            className="w-full flex items-center justify-center gap-1.5 text-[10px] font-medium text-muted-foreground hover:text-primary py-1.5 hover:bg-background/50 rounded transition-all group"
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
