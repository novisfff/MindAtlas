import { useRef, useState, useEffect, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { addDays, differenceInDays, endOfDay, format, isSameDay, startOfDay, startOfWeek } from 'date-fns'
import { enUS, zhCN } from 'date-fns/locale'
import { getMonthDays, isToday, isCurrentMonth } from '../utils/dateUtils'
import { assignRows } from '../utils/layoutUtils'
import { CalendarCell } from './CalendarCell'
import { CalendarEvent } from './CalendarEvent'
import { MoreEventsPopover } from './MoreEventsPopover'
import type { Entry } from '@/types'

interface MonthViewProps {
  currentDate: Date
  entries: Entry[]
  onDateSelect: (date: Date) => void
  onDateDoubleClick?: (date: Date) => void
  onEntryClick?: (entry: Entry) => void
  onEntryUpdate?: (entry: Entry, start: Date, end: Date) => void
}

const MAX_VISIBLE_ROWS = 3 // Rows 0..2 always shown; row 3 used for 4th event (if only 1 hidden) or "+N more" (if >=2 hidden).

interface ResizeRefState {
  entryId: string
  direction: 'left' | 'right'
  pointerId: number
  initialX: number
  originalStart: Date
  originalEnd: Date
  newStart: Date
  newEnd: Date
  originalSpanDays: number
}

interface ResizePreviewMeta {
  entryId: string
  direction: 'left' | 'right'
}

export function MonthView({
  currentDate,
  entries,
  onDateSelect,
  onDateDoubleClick,
  onEntryClick,
  onEntryUpdate
}: MonthViewProps) {
  const { i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const days = useMemo(() => getMonthDays(currentDate), [currentDate])
  const containerRef = useRef<HTMLDivElement>(null)
  const [resizePreviewMeta, setResizePreviewMeta] = useState<ResizePreviewMeta | null>(null)
  const resizeRef = useRef<ResizeRefState | null>(null)
  const resizeDeltaXRafRef = useRef<number | null>(null)
  const resizeDeltaXPendingRef = useRef(0)
  const entriesRef = useRef(entries)
  const onEntryUpdateRef = useRef(onEntryUpdate)

  useEffect(() => {
    entriesRef.current = entries
  }, [entries])

  useEffect(() => {
    onEntryUpdateRef.current = onEntryUpdate
  }, [onEntryUpdate])

  // Chunk days into weeks
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

  const setResizeDeltaXCssVar = useCallback((deltaX: number) => {
    if (!containerRef.current) return
    containerRef.current.style.setProperty('--calendar-resize-delta-x', `${deltaX}px`)
  }, [])

  const handleResizeStart = (entry: Entry, direction: 'left' | 'right', e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()

    // Get basic time info
    let start: Date, end: Date
    if (entry.timeMode === 'POINT' && entry.timeAt) {
      start = startOfDay(new Date(entry.timeAt))
      end = endOfDay(new Date(entry.timeAt))
    } else if (entry.timeMode === 'RANGE' && entry.timeFrom && entry.timeTo) {
      start = startOfDay(new Date(entry.timeFrom))
      end = endOfDay(new Date(entry.timeTo))
    } else {
      return // Should not happen for displayable events
    }

    const originalSpanDays = differenceInDays(startOfDay(end), startOfDay(start)) + 1
    resizeRef.current = {
      entryId: entry.id,
      direction,
      pointerId: e.pointerId,
      initialX: e.clientX,
      originalStart: start,
      originalEnd: end,
      newStart: start,
      newEnd: end,
      originalSpanDays,
    }

    setResizeDeltaXCssVar(0)
    setResizePreviewMeta({ entryId: entry.id, direction })
  }

  const handleResizeMove = useCallback((e: PointerEvent) => {
    const state = resizeRef.current
    if (!state || !containerRef.current) return
    if (e.pointerId !== state.pointerId) return

    const gridWidth = containerRef.current.clientWidth
    const cellWidth = gridWidth / 7

    const rawDeltaX = e.clientX - state.initialX
    const maxShrinkPx = Math.max(0, (state.originalSpanDays - 1) * cellWidth)
    const clampedDeltaX = state.direction === 'right'
      ? Math.max(rawDeltaX, -maxShrinkPx)
      : Math.min(rawDeltaX, maxShrinkPx)

    resizeDeltaXPendingRef.current = clampedDeltaX
    if (resizeDeltaXRafRef.current == null) {
      resizeDeltaXRafRef.current = window.requestAnimationFrame(() => {
        resizeDeltaXRafRef.current = null
        setResizeDeltaXCssVar(resizeDeltaXPendingRef.current)
      })
    }

    // Round to nearest day for the committed value (snaps to date boundaries on release)
    const dayDelta = Math.round(clampedDeltaX / cellWidth)

    let newStart = state.originalStart
    let newEnd = state.originalEnd

    if (state.direction === 'right') {
      newEnd = addDays(state.originalEnd, dayDelta)
      // Constraints: End cannot be before Start
      if (differenceInDays(newEnd, state.originalStart) < 0) {
        newEnd = endOfDay(state.originalStart)
      } else {
        newEnd = endOfDay(newEnd)
      }
    } else { // direction === 'left'
      newStart = addDays(state.originalStart, dayDelta)
      // Constraints: Start cannot be after End
      if (differenceInDays(state.originalEnd, newStart) < 0) {
        newStart = startOfDay(state.originalEnd)
      } else {
        newStart = startOfDay(newStart)
      }
    }

    const didChange =
      newStart.getTime() !== state.newStart.getTime() ||
      newEnd.getTime() !== state.newEnd.getTime()

    if (didChange) {
      state.newStart = newStart
      state.newEnd = newEnd
    }
  }, [setResizeDeltaXCssVar])

  const handleResizeEnd = useCallback((e: PointerEvent) => {
    const state = resizeRef.current
    if (!state) return
    if (e.pointerId !== state.pointerId) return

    const entry = entriesRef.current.find(e => e.id === state.entryId)
    const onEntryUpdate = onEntryUpdateRef.current
    if (entry && onEntryUpdate) {
      onEntryUpdate(entry, state.newStart, state.newEnd)
    }

    resizeRef.current = null
    setResizePreviewMeta(null)
    setResizeDeltaXCssVar(0)
    if (resizeDeltaXRafRef.current != null) {
      window.cancelAnimationFrame(resizeDeltaXRafRef.current)
      resizeDeltaXRafRef.current = null
    }
  }, [setResizeDeltaXCssVar])

  // Global listeners
  useEffect(() => {
    if (resizePreviewMeta) {
      window.addEventListener('pointermove', handleResizeMove)
      window.addEventListener('pointerup', handleResizeEnd)
      window.addEventListener('pointercancel', handleResizeEnd)
    }
    return () => {
      window.removeEventListener('pointermove', handleResizeMove)
      window.removeEventListener('pointerup', handleResizeEnd)
      window.removeEventListener('pointercancel', handleResizeEnd)
    }
  }, [resizePreviewMeta, handleResizeMove, handleResizeEnd])

  const layoutsByWeek = useMemo(() => {
    return weeks.map((weekDays) => assignRows(entries, weekDays[0]))
  }, [entries, weeks])

  const hiddenCountsByWeek = useMemo(() => {
    return layoutsByWeek.map((layout) => {
      const counts = Array(7).fill(0) as number[]
      for (const item of layout) {
        if (item.row < MAX_VISIBLE_ROWS) continue
        for (let col = item.startCol; col < item.startCol + item.span; col++) {
          if (col >= 0 && col < 7) counts[col]++
        }
      }
      return counts
    })
  }, [layoutsByWeek])

  return (
    <div className="flex flex-col h-full border-l border-t bg-background" ref={containerRef}>
      <div className="grid grid-cols-7 border-b">
        {weekdayLabelDates.map((day, idx) => (
          <div
            key={idx}
            className="py-2 text-center text-sm font-medium text-muted-foreground border-r last:border-r-0"
          >
            {format(day, 'EEE', { locale })}
          </div>
        ))}
      </div>
      <div className="flex-1 flex flex-col">
        {weeks.map((weekDays, weekIndex) => {
          const weekStart = weekDays[0]

          const layout = layoutsByWeek[weekIndex] ?? []
          const hiddenCounts = hiddenCountsByWeek[weekIndex] ?? Array(7).fill(0)

          return (
            <div key={weekStart.toISOString()} className="flex-1 grid grid-cols-7 relative min-h-[132px] border-b last:border-b-0">
              {/* Background Layer: Cells */}
              {weekDays.map((day, dayIndex) => (
                <CalendarCell
                  key={day.toISOString()}
                  date={day}
                  isToday={isToday(day)}
                  isCurrentMonth={isCurrentMonth(day, currentDate)}
                  onClick={() => onDateSelect(day)}
                  onDoubleClick={() => onDateDoubleClick?.(day)}
                  onQuickCreate={() => onDateDoubleClick?.(day)}
                >
                  {/* Render 'More' button if needed in the cell flow? 
                      No, we render it in the Overlay Layer to align with the grid rows. 
                      Actually, Overlay Layer is better for alignment. 
                      But CalendarCell expects children to be in its content area (padding).
                      If we put it in Overlay, we must match the padding.
                  */}
                </CalendarCell>
              ))}

              {/* Events Layer */}
              <div
                className="absolute inset-0 grid grid-cols-7 grid-rows-[repeat(auto-fill,24px)] pt-8 pointer-events-none"
                style={{ paddingRight: '1px' }} // Adjustment for border
              >
                {layout.map((item) => {
                  if (item.row > MAX_VISIBLE_ROWS) return null
                  if (item.row === MAX_VISIBLE_ROWS) {
                    // Only show the 4th row if it's the only hidden event for every day it spans.
                    // If there are >=2 hidden events on any spanned day, keep row 3 reserved for "+N more".
                    const canShow = Array.from({ length: item.span }, (_, i) => hiddenCounts[item.startCol + i]).every((c) => c === 1)
                    if (!canShow) return null
                  }
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
                      className={`px-1 pointer-events-auto overflow-visible ${isResizing ? 'z-20' : ''}`}
                    >
                      <CalendarEvent
                        entry={item.entry}
                        compact
                        resizable={true} // Enable resizing
                        showStartIndicator={item.isStart}
                        onResizeStart={handleResizeStart}
                        isDragging={false} // Todo: Pass dragging state if needed
                        onClick={() => onEntryClick?.(item.entry)}
                      />
                    </div>
                  )
                })}

                {/* 'More' Buttons */}
                {weekDays.map((day, colIndex) => {
                  const count = hiddenCounts[colIndex] ?? 0
                  // If only 1 hidden event, we show it as the 4th row instead of "+1 more".
                  if (count <= 1) return null
                  // Filter entries for this day to pass to Popover
                  const dayEntries = entries.filter(e => {
                    // Simple check if this entry is part of the hidden set for this day
                    // We can reuse logic or just pass all day entries? 
                    // Popover expects list of hidden entries or all entries?
                    // The original code passed `entries = { dayEntries }` (all events for that day).
                    // Let's pass all events for that day.
                    const lItem = layout.find(l => l.entry.id === e.id)
                    if (!lItem) return false // not in this week layout? 
                    // Actually easier: calculate entries for that day manually or use helper?
                    // Let's reconstruct range check.
                    return colIndex >= lItem.startCol && colIndex < lItem.startCol + lItem.span
                  })

                  return (
                    <div
                      key={`more-${colIndex}`}
                      style={{
                        gridColumnStart: colIndex + 1,
                        gridRowStart: MAX_VISIBLE_ROWS + 1,
                      }}
                      className="px-1 pointer-events-auto"
                    >
                      <MoreEventsPopover
                        entries={dayEntries} // We pass all entries for the day, the popover handles showing them
                        date={day}
                        visibleCount={MAX_VISIBLE_ROWS}
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
