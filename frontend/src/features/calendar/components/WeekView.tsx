import { useMemo, useRef } from 'react'
import { format } from 'date-fns'
import { enUS, zhCN } from 'date-fns/locale'
import { useDroppable } from '@dnd-kit/core'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getWeekDays, isToday } from '../utils/dateUtils'
import { assignRows } from '../utils/layoutUtils'
import { CalendarEvent } from './CalendarEvent'
import { useCalendarResize } from '../hooks/useCalendarResize'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'
import type { CalendarDensity } from '../types'
import { calendarRadius, calendarSurface } from '../styles'

interface WeekViewProps {
  density: CalendarDensity
  currentDate: Date
  entries: Entry[]
  onDateSelect: (date: Date) => void
  onDateDoubleClick?: (date: Date) => void
  onEntryClick?: (entry: Entry) => void
  onEntryUpdate?: (entry: Entry, start: Date, end: Date) => void
}

const WEEK_VIEW_CONFIG = {
  comfortable: {
    rowHeight: 24,
    contentPaddingTop: 10,
    contentPaddingBottom: 12,
    minBodyHeight: 320,
    minHeightClass: 'min-h-[33rem]',
    dayBadgeClass: 'h-10 w-10 text-base',
    headerPaddingClass: 'py-3.5',
  },
  compact: {
    rowHeight: 22,
    contentPaddingTop: 8,
    contentPaddingBottom: 10,
    minBodyHeight: 280,
    minHeightClass: 'min-h-[29rem]',
    dayBadgeClass: 'h-9 w-9 text-sm',
    headerPaddingClass: 'py-2.5',
  },
} as const

export function WeekView({
  density,
  currentDate,
  entries,
  onDateSelect,
  onDateDoubleClick,
  onEntryClick,
  onEntryUpdate,
}: WeekViewProps) {
  const { i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const days = useMemo(() => getWeekDays(currentDate), [currentDate])
  const weekStart = days[0]
  const containerRef = useRef<HTMLDivElement>(null)
  const config = WEEK_VIEW_CONFIG[density]

  const { resizePreviewMeta, handleResizeStart } = useCalendarResize(
    entries,
    onEntryUpdate,
    containerRef,
  )

  const layout = useMemo(
    () => assignRows(entries, weekStart),
    [entries, weekStart],
  )
  const rowCount = useMemo(() => {
    let maxRow = -1
    for (const item of layout) {
      if (item.row > maxRow) maxRow = item.row
    }
    return Math.max(1, maxRow + 1)
  }, [layout])

  const contentHeightPx = useMemo(() => {
    const content =
      config.contentPaddingTop +
      rowCount * config.rowHeight +
      config.contentPaddingBottom
    return Math.max(config.minBodyHeight, content)
  }, [
    config.contentPaddingBottom,
    config.contentPaddingTop,
    config.minBodyHeight,
    config.rowHeight,
    rowCount,
  ])

  return (
    <div
      className={cn(
        'flex min-h-full flex-col overflow-hidden',
        calendarRadius.shell,
        calendarSurface.shell,
        config.minHeightClass,
      )}
    >
      <div className="grid grid-cols-7 border-b border-border/60 bg-background/82">
        {days.map((day) => (
          <div
            key={day.toISOString()}
            onClick={() => onDateSelect(day)}
            className={cn(
              'group relative cursor-pointer border-r border-border/60 px-2 text-center transition-colors hover:bg-background/95 last:border-r-0',
              config.headerPaddingClass,
            )}
          >
            <button
              type="button"
              aria-label="Create entry"
              className={cn(
                'absolute right-1.5 top-1.5 z-10 inline-flex h-6 w-6 items-center justify-center border border-transparent text-muted-foreground opacity-0 transition-opacity hover:border-border/60 hover:bg-background hover:text-foreground group-hover:opacity-100',
                calendarRadius.micro,
              )}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onDateDoubleClick?.(day)
              }}
            >
              <Plus className="h-4 w-4" />
            </button>

            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/75">
              {format(day, 'EEE', { locale })}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {format(day, 'MMM', { locale })}
            </div>
            <div
              className={cn(
                'mx-auto mt-2 flex items-center justify-center font-semibold shadow-sm',
                config.dayBadgeClass,
                calendarRadius.pill,
                isToday(day)
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-background/90 text-foreground ring-1 ring-border/60 dark:bg-background/70',
              )}
            >
              {format(day, 'd')}
            </div>
          </div>
        ))}
      </div>

      <div className="relative flex-1 min-h-0">
        <div
          ref={containerRef}
          className="relative min-h-full border-l border-border/60 bg-background/30"
          style={{ minHeight: `${contentHeightPx}px` }}
        >
          <div className="pointer-events-none absolute inset-0 grid grid-cols-7">
            {days.map((day) => (
              <WeekDayDropZone
                key={day.toISOString()}
                day={day}
                onDoubleClick={() => onDateDoubleClick?.(day)}
              />
            ))}
          </div>

          <div
            className="pointer-events-none absolute inset-0 grid grid-cols-7 overflow-visible"
            style={{
              paddingTop: `${config.contentPaddingTop}px`,
              paddingBottom: `${config.contentPaddingBottom}px`,
              gridAutoRows: `${config.rowHeight}px`,
            }}
          >
            {layout.map((item) => {
              const isResizing = resizePreviewMeta?.entryId === item.entry.id
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
          </div>
        </div>
      </div>
    </div>
  )
}

function WeekDayDropZone({
  day,
  onDoubleClick,
}: {
  day: Date
  onDoubleClick?: () => void
}) {
  const dateId = format(day, 'yyyy-MM-dd')
  const { setNodeRef, isOver } = useDroppable({ id: dateId })

  return (
    <div
      ref={setNodeRef}
      onDoubleClick={onDoubleClick}
      className={cn(
        'border-r border-border/60 pointer-events-auto transition-colors last:border-r-0',
        isToday(day) && 'bg-primary/[0.05]',
        isOver && 'bg-primary/10',
      )}
    />
  )
}
