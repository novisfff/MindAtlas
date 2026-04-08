import { useMemo, useRef } from 'react'
import { addDays, format, startOfWeek } from 'date-fns'
import { enUS, zhCN } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { getMonthDays, isCurrentMonth, isToday } from '../utils/dateUtils'
import { assignRows } from '../utils/layoutUtils'
import { CalendarCell } from './CalendarCell'
import { CalendarEvent } from './CalendarEvent'
import { MoreEventsPopover } from './MoreEventsPopover'
import { useCalendarResize } from '../hooks/useCalendarResize'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'
import type { CalendarDensity } from '../types'
import { calendarRadius, calendarSurface } from '../styles'

interface MonthViewProps {
  density: CalendarDensity
  currentDate: Date
  entries: Entry[]
  onDateSelect: (date: Date) => void
  onDateDoubleClick?: (date: Date) => void
  onEntryClick?: (entry: Entry) => void
  onEntryUpdate?: (entry: Entry, start: Date, end: Date) => void
}

const MONTH_VIEW_CONFIG = {
  comfortable: {
    visibleRows: 2,
    eventRowHeight: 20,
    eventTopOffset: 42,
    weekMinHeight: 104,
    minHeightClass: 'min-h-[36rem]',
    weekdayHeaderClass: 'h-12',
  },
  compact: {
    visibleRows: 2,
    eventRowHeight: 18,
    eventTopOffset: 34,
    weekMinHeight: 90,
    minHeightClass: 'min-h-[30rem]',
    weekdayHeaderClass: 'h-10',
  },
} as const

export function MonthView({
  density,
  currentDate,
  entries,
  onDateSelect,
  onDateDoubleClick,
  onEntryClick,
  onEntryUpdate,
}: MonthViewProps) {
  const { i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const days = useMemo(() => getMonthDays(currentDate), [currentDate])
  const containerRef = useRef<HTMLDivElement>(null)
  const config = MONTH_VIEW_CONFIG[density]

  const { resizePreviewMeta, handleResizeStart } = useCalendarResize(
    entries,
    onEntryUpdate,
    containerRef,
  )

  const weeks = useMemo(() => {
    const result = []
    for (let i = 0; i < days.length; i += 7) {
      result.push(days.slice(i, i + 7))
    }
    return result
  }, [days])

  const weekdayLabelDates = useMemo(() => {
    const base = startOfWeek(new Date(), { weekStartsOn: 1 })
    return Array.from({ length: 7 }, (_, i) => addDays(base, i))
  }, [])

  const layoutsByWeek = useMemo(() => {
    return weeks.map((weekDays) => assignRows(entries, weekDays[0]))
  }, [entries, weeks])

  const hiddenCountsByWeek = useMemo(() => {
    return layoutsByWeek.map((layout) => {
      const counts = Array(7).fill(0) as number[]
      for (const item of layout) {
        if (item.row < config.visibleRows) continue
        for (let col = item.startCol; col < item.startCol + item.span; col++) {
          if (col >= 0 && col < 7) counts[col]++
        }
      }
      return counts
    })
  }, [config.visibleRows, layoutsByWeek])

  return (
    <div
      ref={containerRef}
      className={cn(
        'flex min-h-full flex-col overflow-hidden',
        calendarRadius.shell,
        calendarSurface.shell,
        config.minHeightClass,
      )}
    >
      <div
        className={cn(
          'grid grid-cols-7 border-b border-border/60 bg-background/82',
          config.weekdayHeaderClass,
        )}
      >
        {weekdayLabelDates.map((day, idx) => (
          <div
            key={idx}
            className="flex items-center justify-center border-r border-border/60 px-2 text-center text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/80 last:border-r-0"
          >
            {format(day, 'EEE', { locale })}
          </div>
        ))}
      </div>

      <div
        className="grid min-h-0 flex-1"
        style={{
          gridTemplateRows: `repeat(6, minmax(${config.weekMinHeight}px, 1fr))`,
        }}
      >
        {weeks.map((weekDays, weekIndex) => {
          const weekStart = weekDays[0]
          const layout = layoutsByWeek[weekIndex] ?? []
          const hiddenCounts = hiddenCountsByWeek[weekIndex] ?? Array(7).fill(0)

          return (
            <div
              key={weekStart.toISOString()}
              className="relative grid min-h-0 grid-cols-7"
            >
              {weekDays.map((day, dayIndex) => (
                <CalendarCell
                  key={day.toISOString()}
                  date={day}
                  density={density}
                  isToday={isToday(day)}
                  isCurrentMonth={isCurrentMonth(day, currentDate)}
                  onClick={() => onDateSelect(day)}
                  onDoubleClick={() => onDateDoubleClick?.(day)}
                  onQuickCreate={() => onDateDoubleClick?.(day)}
                  className={cn(
                    dayIndex === 6 && 'border-r-0',
                    weekIndex === weeks.length - 1 && 'border-b-0',
                  )}
                />
              ))}

              <div
                className="pointer-events-none absolute inset-0 grid grid-cols-7 content-start overflow-hidden"
                style={{
                  paddingTop: `${config.eventTopOffset}px`,
                  gridAutoRows: `${config.eventRowHeight}px`,
                }}
              >
                {layout.map((item) => {
                  if (item.row > config.visibleRows) return null
                  if (item.row === config.visibleRows) {
                    const canShow = Array.from(
                      { length: item.span },
                      (_, i) => hiddenCounts[item.startCol + i],
                    ).every((count) => count === 1)
                    if (!canShow) return null
                  }

                  const isResizing =
                    resizePreviewMeta?.entryId === item.entry.id
                  const resizeDirection = resizePreviewMeta?.direction

                  return (
                    <div
                      key={item.entry.id}
                      style={{
                        gridColumnStart: item.startCol + 1,
                        gridColumnEnd: `span ${item.span}`,
                        gridRowStart: item.row + 1,
                        ...(isResizing
                          ? resizeDirection === 'right'
                            ? {
                                width:
                                  'calc(100% + var(--calendar-resize-delta-x))',
                                willChange: 'width',
                              }
                            : {
                                transform:
                                  'translateX(var(--calendar-resize-delta-x))',
                                width:
                                  'calc(100% - var(--calendar-resize-delta-x))',
                                willChange: 'transform, width',
                              }
                          : {}),
                      }}
                      className={cn(
                        'pointer-events-auto overflow-visible px-1',
                        isResizing && 'z-20',
                      )}
                    >
                      <CalendarEvent
                        entry={item.entry}
                        density={density}
                        compact
                        resizable
                        showStartIndicator={item.isStart}
                        onResizeStart={handleResizeStart}
                        onClick={() => onEntryClick?.(item.entry)}
                      />
                    </div>
                  )
                })}

                {weekDays.map((day, colIndex) => {
                  const count = hiddenCounts[colIndex] ?? 0
                  if (count <= 1) return null

                  const dayEntries = entries.filter((entry) => {
                    const layoutItem = layout.find(
                      (candidate) => candidate.entry.id === entry.id,
                    )
                    if (!layoutItem) return false
                    return (
                      colIndex >= layoutItem.startCol &&
                      colIndex < layoutItem.startCol + layoutItem.span
                    )
                  })

                  return (
                    <div
                      key={`more-${colIndex}`}
                      style={{
                        gridColumnStart: colIndex + 1,
                        gridRowStart: config.visibleRows + 1,
                      }}
                      className="pointer-events-auto flex items-start px-1 pt-0.5"
                    >
                      <MoreEventsPopover
                        date={day}
                        density={density}
                        entries={dayEntries}
                        visibleCount={config.visibleRows}
                        onEntryClick={onEntryClick}
                      />
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
