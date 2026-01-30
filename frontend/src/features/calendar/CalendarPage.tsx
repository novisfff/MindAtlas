import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { format, parse, isValid, startOfMonth, startOfWeek, endOfWeek, addDays } from 'date-fns'
import { DndContext, DragOverlay, PointerSensor, useSensor, useSensors } from '@dnd-kit/core'
import { toast } from 'sonner'
import { CalendarHeader } from './components/CalendarHeader'
import { MonthView } from './components/MonthView'
import { WeekView } from './components/WeekView'
import { DayView } from './components/DayView'
import { CalendarEvent } from './components/CalendarEvent'
import { QuickCreateDialog } from './components/QuickCreateDialog'
import { EntryDetailDialog } from './components/EntryDetailDialog'
import { useCalendarEntriesQuery, usePatchEntryTimeMutation } from './queries'
import type { Entry } from '@/types'
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

type EntryTimeSnapshot = Pick<Entry, 'timeMode' | 'timeAt' | 'timeFrom' | 'timeTo'>

interface UndoAction {
  id: string
  entryId: string
  label: string
  before: EntryTimeSnapshot
  after: EntryTimeSnapshot
  timestamp: number
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
  const [selectedEntry, setSelectedEntry] = useState<Entry | null>(null)

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }))
  const { activeId, handleDragStart, handleDragEnd, handleDragCancel } = useCalendarDnd()
  const patchMutation = usePatchEntryTimeMutation()

  const undoStackRef = useRef<UndoAction[]>([])
  const [canUndo, setCanUndo] = useState(false)

  const snapshotEntryTime = useCallback((entry: Entry): EntryTimeSnapshot => {
    return {
      timeMode: entry.timeMode,
      timeAt: entry.timeAt,
      timeFrom: entry.timeFrom,
      timeTo: entry.timeTo,
    }
  }, [])

  const isSameSnapshot = useCallback((a: EntryTimeSnapshot, b: EntryTimeSnapshot) => {
    return a.timeMode === b.timeMode &&
      a.timeAt === b.timeAt &&
      a.timeFrom === b.timeFrom &&
      a.timeTo === b.timeTo
  }, [])

  const removeUndoAction = useCallback((id: string) => {
    const next = undoStackRef.current.filter((a) => a.id !== id)
    undoStackRef.current = next
    setCanUndo(next.length > 0)
  }, [])

  const undoLatest = useCallback(() => {
    const action = undoStackRef.current.pop()
    setCanUndo(undoStackRef.current.length > 0)
    if (!action) return

    patchMutation.mutate(
      {
        id: action.entryId,
        timeMode: action.before.timeMode,
        timeAt: action.before.timeAt,
        timeFrom: action.before.timeFrom,
        timeTo: action.before.timeTo,
      },
      {
        onSuccess: () => {
          toast.success('已撤回', { description: action.label })
        },
        onError: (error) => {
          undoStackRef.current.push(action)
          setCanUndo(true)
          toast.error('撤回失败', {
            description: error instanceof Error ? error.message : 'Failed to undo',
          })
        },
      }
    )
  }, [patchMutation])

  const undoSpecific = useCallback((id: string) => {
    const latest = undoStackRef.current.at(-1)
    if (!latest || latest.id !== id) {
      toast('只能撤回最新操作')
      return
    }
    undoLatest()
  }, [undoLatest])

  const recordUndoAndPatch = useCallback((args: {
    entry: Entry
    after: EntryTimeSnapshot
    label: string
  }) => {
    const before = snapshotEntryTime(args.entry)
    const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random()}`

    const action: UndoAction = {
      id,
      entryId: args.entry.id,
      label: args.label,
      before,
      after: args.after,
      timestamp: Date.now(),
    }

    if (isSameSnapshot(before, args.after)) return

    undoStackRef.current.push(action)
    setCanUndo(true)

    patchMutation.mutate(
      {
        id: args.entry.id,
        timeMode: args.after.timeMode,
        timeAt: args.after.timeAt,
        timeFrom: args.after.timeFrom,
        timeTo: args.after.timeTo,
      },
      {
        onSuccess: () => {
          toast('已更新时间', {
            description: args.label,
            action: {
              label: '撤回',
              onClick: () => undoSpecific(action.id),
            },
          })
        },
        onError: (error) => {
          removeUndoAction(action.id)
          toast.error('更新时间失败', {
            description: error instanceof Error ? error.message : 'Failed to update',
          })
        },
      }
    )
  }, [isSameSnapshot, patchMutation, removeUndoAction, snapshotEntryTime, undoSpecific])

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

  const handleEntryClick = (entry: Entry) => {
    setSelectedEntry(entry)
  }

  const handleEntryUpdate = useCallback((entry: Entry, start: Date, end: Date) => {
    const actuallySingleDay = start.getDate() === end.getDate() &&
      start.getMonth() === end.getMonth() &&
      start.getFullYear() === end.getFullYear()

    const after: EntryTimeSnapshot = actuallySingleDay
      ? { timeMode: 'POINT', timeAt: start.toISOString() }
      : { timeMode: 'RANGE', timeFrom: start.toISOString(), timeTo: end.toISOString() }

    recordUndoAndPatch({ entry, after, label: '伸缩调整时间' })
  }, [recordUndoAndPatch])

  useCalendarKeyboard({
    currentDate,
    viewMode,
    onDateChange: handleDateChange,
    onViewChange: handleViewChange,
  })

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isUndo = (e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z'
      if (!isUndo) return

      const target = e.target as HTMLElement | null
      const isEditable = !!target && (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        (target as HTMLElement).isContentEditable
      )
      if (isEditable) return

      if (undoStackRef.current.length === 0) return
      e.preventDefault()
      undoLatest()
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [undoLatest])

  const activeEntry = activeId ? entries.find((e) => e.id === activeId) : null

  const onDragEnd = (event: Parameters<typeof handleDragEnd>[0]) => {
    const result = handleDragEnd(event)
    if (!result) return

    const entry = entries.find((e) => e.id === result.entryId)
    if (!entry) return

    const targetDate = result.targetDate
    if (entry.timeMode === 'POINT') {
      recordUndoAndPatch({
        entry,
        after: { timeMode: 'POINT', timeAt: `${targetDate}T00:00:00Z` },
        label: '拖拽移动时间',
      })
    } else if (entry.timeMode === 'RANGE' && entry.timeFrom && entry.timeTo) {
      const oldStart = new Date(entry.timeFrom)
      const oldEnd = new Date(entry.timeTo)
      const duration = oldEnd.getTime() - oldStart.getTime()
      const newStart = new Date(`${targetDate}T00:00:00Z`)
      const newEnd = new Date(newStart.getTime() + duration)
      recordUndoAndPatch({
        entry,
        after: { timeMode: 'RANGE', timeFrom: newStart.toISOString(), timeTo: newEnd.toISOString() },
        label: '拖拽移动时间',
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
              onEntryClick={handleEntryClick}
              onEntryUpdate={handleEntryUpdate}
            />
          )}
          {viewMode === 'week' && (
            <WeekView
              currentDate={currentDate}
              entries={entries}
              onDateSelect={handleDateChange}
              onDateDoubleClick={handleDateDoubleClick}
              onEntryClick={handleEntryClick}
              onEntryUpdate={handleEntryUpdate}
            />
          )}
          {viewMode === 'day' && (
            <DayView
              currentDate={currentDate}
              entries={entries}
              onDateDoubleClick={handleDateDoubleClick}
              onEntryClick={handleEntryClick}
            />
          )}
        </div>
      </div>
      <DragOverlay>
        {activeEntry && (
          <div className="pointer-events-none">
            <CalendarEvent entry={activeEntry} isDragging />
          </div>
        )}
      </DragOverlay>
      {quickCreateDate && (
        <QuickCreateDialog
          date={quickCreateDate}
          isOpen={true}
          onClose={() => setQuickCreateDate(null)}
        />
      )}
      <EntryDetailDialog
        entry={selectedEntry}
        open={!!selectedEntry}
        onOpenChange={(open) => !open && setSelectedEntry(null)}
      />
    </DndContext>
  )
}
