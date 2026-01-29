import { useState, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { format, parse, isValid, startOfMonth, endOfMonth, startOfWeek, endOfWeek, addDays } from 'date-fns'
import { DndContext, DragOverlay, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { CalendarHeader } from './components/CalendarHeader'
import { MonthView } from './components/MonthView'
import { WeekView } from './components/WeekView'
import { DayView } from './components/DayView'
import { CalendarEvent } from './components/CalendarEvent'
import { QuickCreateDialog } from './components/QuickCreateDialog'
import { useCalendarEntriesQuery, usePatchEntryTimeMutation } from './queries'
import { useCalendarDnd } from './hooks/useCalendarDnd'
import { useCalendarKeyboard } from './hooks/useCalendarKeyboard'

export type CalendarViewMode = 'month' | 'week' | 'day'

const VALID_VIEWS: CalendarViewMode[] = ['month', 'week', 'day']

function parseUrlDate(dateStr: string | null): Date {
  if (!dateStr) return new Date()
  const parsed = parse(dateStr, 'yyyy-MM-dd', new Date())
  return isValid(parsed) ? parsed : new Date()
}

function formatUrlDate(date: Date): string {
  return format(date, 'yyyy-MM-dd')
}

export function CalendarPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const viewParam = searchParams.get('view')
  const dateParam = searchParams.get('date')

  const [viewMode, setViewMode] = useState<CalendarViewMode>(() => {
    return VALID_VIEWS.includes(viewParam as CalendarViewMode)
      ? (viewParam as CalendarViewMode)
      : 'month'
  })
  const [currentDate, setCurrentDate] = useState(() => parseUrlDate(dateParam))
  const [quickCreateDate, setQuickCreateDate] = useState<Date | null>(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }))
  const { activeId, handleDragStart, handleDragEnd, handleDragCancel } = useCalendarDnd()
  const patchMutation = usePatchEntryTimeMutation()

  const dateRange = useMemo(() => {
    if (viewMode === 'month') {
      const monthStart = startOfMonth(currentDate)
      const gridStart = startOfWeek(monthStart, { weekStartsOn: 1 })
      const gridEnd = addDays(gridStart, 41)
      return { timeFrom: formatUrlDate(gridStart), timeTo: formatUrlDate(gridEnd) }
    }
    if (viewMode === 'week') {
      const weekStart = startOfWeek(currentDate, { weekStartsOn: 1 })
      const weekEnd = endOfWeek(currentDate, { weekStartsOn: 1 })
      return { timeFrom: formatUrlDate(weekStart), timeTo: formatUrlDate(weekEnd) }
    }
    return { timeFrom: formatUrlDate(currentDate), timeTo: formatUrlDate(currentDate) }
  }, [viewMode, currentDate])

  const { data: entries = [], isLoading } = useCalendarEntriesQuery(dateRange)

  const handleViewChange = (mode: CalendarViewMode) => {
    setViewMode(mode)
    setSearchParams({ view: mode, date: formatUrlDate(currentDate) })
  }

  const handleDateChange = (date: Date) => {
    setCurrentDate(date)
    setSearchParams({ view: viewMode, date: formatUrlDate(date) })
  }

  const handleDateDoubleClick = (date: Date) => {
    setQuickCreateDate(date)
  }

  useCalendarKeyboard({
    currentDate,
    viewMode,
    onDateChange: handleDateChange,
    onViewChange: handleViewChange,
  })

  const activeEntry = activeId ? entries.find((e) => e.id === activeId) : null

  const onDragEnd = (event: Parameters<typeof handleDragEnd>[0]) => {
    const result = handleDragEnd(event)
    if (!result) return

    const entry = entries.find((e) => e.id === result.entryId)
    if (!entry) return

    const targetDate = result.targetDate
    if (entry.timeMode === 'POINT') {
      patchMutation.mutate({ id: result.entryId, timeAt: `${targetDate}T00:00:00Z` })
    } else if (entry.timeMode === 'RANGE' && entry.timeFrom && entry.timeTo) {
      const oldStart = new Date(entry.timeFrom)
      const oldEnd = new Date(entry.timeTo)
      const duration = oldEnd.getTime() - oldStart.getTime()
      const newStart = new Date(`${targetDate}T00:00:00Z`)
      const newEnd = new Date(newStart.getTime() + duration)
      patchMutation.mutate({
        id: result.entryId,
        timeFrom: newStart.toISOString(),
        timeTo: newEnd.toISOString(),
      })
    }
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={handleDragCancel}
    >
      <div className="flex flex-col h-full">
        <CalendarHeader
          viewMode={viewMode}
          currentDate={currentDate}
          onViewChange={handleViewChange}
          onDateChange={handleDateChange}
        />
        <div className="flex-1 overflow-auto">
          {viewMode === 'month' && (
            <MonthView
              currentDate={currentDate}
              entries={entries}
              onDateSelect={handleDateChange}
              onDateDoubleClick={handleDateDoubleClick}
            />
          )}
          {viewMode === 'week' && (
            <WeekView
              currentDate={currentDate}
              entries={entries}
              onDateSelect={handleDateChange}
              onDateDoubleClick={handleDateDoubleClick}
            />
          )}
          {viewMode === 'day' && (
            <DayView
              currentDate={currentDate}
              entries={entries}
              onDateDoubleClick={handleDateDoubleClick}
            />
          )}
        </div>
      </div>
      <DragOverlay>
        {activeEntry && <CalendarEvent entry={activeEntry} isDragging />}
      </DragOverlay>
      {quickCreateDate && (
        <QuickCreateDialog
          date={quickCreateDate}
          isOpen={true}
          onClose={() => setQuickCreateDate(null)}
        />
      )}
    </DndContext>
  )
}
