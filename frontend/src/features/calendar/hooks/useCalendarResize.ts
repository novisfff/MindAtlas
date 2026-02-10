import { useState, useEffect, useRef, useCallback } from 'react'
import { addDays, differenceInDays, endOfDay, startOfDay } from 'date-fns'
import type { Entry } from '@/types'

export interface ResizePreviewMeta {
  entryId: string
  direction: 'left' | 'right'
}

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

export function useCalendarResize(
  entries: Entry[],
  onEntryUpdate: ((entry: Entry, start: Date, end: Date) => void) | undefined,
  containerRef: React.RefObject<HTMLElement>
) {
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

  const setResizeDeltaXCssVar = useCallback((deltaX: number) => {
    if (!containerRef.current) return
    containerRef.current.style.setProperty('--calendar-resize-delta-x', `${deltaX}px`)
  }, [containerRef])

  const handleResizeStart = useCallback((entry: Entry, direction: 'left' | 'right', e: React.PointerEvent) => {
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
  }, [setResizeDeltaXCssVar])

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
  }, [setResizeDeltaXCssVar, containerRef])

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

  return {
    resizePreviewMeta,
    handleResizeStart
  }
}
