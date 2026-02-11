import { useMemo, useRef } from 'react'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useDroppable } from '@dnd-kit/core'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getWeekDays, isToday } from '../utils/dateUtils'
import { assignRows } from '../utils/layoutUtils'
import { CalendarEvent } from './CalendarEvent'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'
import { useCalendarResize } from '../hooks/useCalendarResize'

interface WeekViewProps {
  currentDate: Date
  entries: Entry[]
  onDateSelect: (date: Date) => void
  onDateDoubleClick?: (date: Date) => void
  onEntryClick?: (entry: Entry) => void
  onEntryUpdate?: (entry: Entry, start: Date, end: Date) => void
}

const ROW_HEIGHT_PX = 24
const CONTENT_PADDING_TOP_PX = 8
const CONTENT_PADDING_BOTTOM_PX = 8
const MIN_BODY_HEIGHT_PX = 300

export function WeekView({
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

  const { resizePreviewMeta, handleResizeStart } = useCalendarResize(
    entries,
    onEntryUpdate,
    containerRef
  )

  const layout = useMemo(() => assignRows(entries, weekStart), [entries, weekStart])
  const rowCount = useMemo(() => {
    let maxRow = -1
    for (const item of layout) {
      if (item.row > maxRow) maxRow = item.row
    }
    return Math.max(1, maxRow + 1)
  }, [layout])

  const contentHeightPx = useMemo(() => {
    const content = CONTENT_PADDING_TOP_PX + rowCount * ROW_HEIGHT_PX + CONTENT_PADDING_BOTTOM_PX
    return Math.max(MIN_BODY_HEIGHT_PX, content)
  }, [rowCount])

  return (
    <div className="flex flex-col h-full">
      <div className="grid grid-cols-7 border-b">
        {days.map((day) => (
          <div
            key={day.toISOString()}
            onClick={() => onDateSelect(day)}
            className={cn(
              'group relative py-3 text-center cursor-pointer hover:bg-muted/50',
              'border-r last:border-r-0'
            )}
          >
            <button
              type="button"
              aria-label="Create entry"
              className={cn(
                'absolute right-1 top-1 z-10',
                'inline-flex h-6 w-6 items-center justify-center rounded-md',
                'opacity-0 group-hover:opacity-100 transition-opacity',
                'text-muted-foreground hover:text-foreground',
                'hover:bg-muted'
              )}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                onDateDoubleClick?.(day)
              }}
            >
              <Plus className="h-4 w-4" />
            </button>
            <div className="text-xs text-muted-foreground">
              {format(day, 'EEE', { locale })}
            </div>
            <div className="text-[11px] text-muted-foreground leading-none">
              {format(day, 'MMM', { locale })}
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
      <div className="flex-1 overflow-auto">
        <div
          ref={containerRef}
          className="relative border-l bg-background"
          style={{ height: `max(100%, ${contentHeightPx}px)` }}
        >
          {/* Background Layer: Droppable day columns */}
          <div className="absolute inset-0 grid grid-cols-7 pointer-events-none">
            {days.map((day) => (
              <WeekDayDropZone
                key={day.toISOString()}
                day={day}
                onDoubleClick={() => onDateDoubleClick?.(day)}
              />
            ))}
          </div>

          {/* Events Layer */}
          <div
            className="absolute inset-0 grid grid-cols-7 pointer-events-none overflow-visible"
            style={{
              paddingTop: `${CONTENT_PADDING_TOP_PX}px`,
              paddingBottom: `${CONTENT_PADDING_BOTTOM_PX}px`,
              gridAutoRows: `${ROW_HEIGHT_PX}px`,
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
                      ? (resizeDirection === 'right'
                        ? {
                            width: 'calc(100% + var(--calendar-resize-delta-x))',
                            willChange: 'width',
                          }
                        : {
                            transform: 'translateX(var(--calendar-resize-delta-x))',
                            width: 'calc(100% - var(--calendar-resize-delta-x))',
                            willChange: 'transform, width',
                          })
                      : {}),
                  }}
                  className={cn('pointer-events-auto overflow-visible', isResizing && 'z-20')}
                >
                  <CalendarEvent
                    entry={item.entry}
                    compact
                    resizable={true}
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

function WeekDayDropZone({ day, onDoubleClick }: { day: Date; onDoubleClick?: () => void }) {
  const dateId = format(day, 'yyyy-MM-dd')
  const { setNodeRef, isOver } = useDroppable({ id: dateId })

  return (
    <div
      ref={setNodeRef}
      onDoubleClick={onDoubleClick}
      className={cn(
        'border-r last:border-r-0 pointer-events-auto',
        isToday(day) && 'bg-primary/5',
        isOver && 'bg-primary/10'
      )}
    />
  )
}
