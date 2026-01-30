import { useEffect, useCallback } from 'react'
import { addDays, subDays, addWeeks, subWeeks } from 'date-fns'
import type { CalendarViewMode } from '../CalendarPage'

interface UseCalendarKeyboardProps {
  currentDate: Date
  viewMode: CalendarViewMode
  onDateChange: (date: Date) => void
  onViewChange: (mode: CalendarViewMode) => void
}

function isEditableElement(target: EventTarget | null): boolean {
  if (!target) return false
  if (target instanceof HTMLInputElement) return true
  if (target instanceof HTMLTextAreaElement) return true
  if (target instanceof HTMLSelectElement) return true
  if (target instanceof HTMLElement && target.isContentEditable) return true
  return false
}

function isInteractiveElement(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tagName = target.tagName.toLowerCase()
  return tagName === 'button' || tagName === 'a' || target.getAttribute('role') === 'button'
}

export function useCalendarKeyboard({
  currentDate,
  viewMode,
  onDateChange,
  onViewChange,
}: UseCalendarKeyboardProps) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (isEditableElement(e.target)) return

      const isInteractive = isInteractiveElement(e.target)

      switch (e.key) {
        case 'ArrowLeft':
          e.preventDefault()
          onDateChange(subDays(currentDate, 1))
          break
        case 'ArrowRight':
          e.preventDefault()
          onDateChange(addDays(currentDate, 1))
          break
        case 'ArrowUp':
          e.preventDefault()
          if (viewMode === 'day') {
            onDateChange(subDays(currentDate, 1))
          } else {
            onDateChange(subWeeks(currentDate, 1))
          }
          break
        case 'ArrowDown':
          e.preventDefault()
          if (viewMode === 'day') {
            onDateChange(addDays(currentDate, 1))
          } else {
            onDateChange(addWeeks(currentDate, 1))
          }
          break
        case 'Enter':
          if (isInteractive) return
          e.preventDefault()
          if (viewMode === 'month') onViewChange('week')
          else if (viewMode === 'week') onViewChange('day')
          break
        case 'Escape':
          if (isInteractive) return
          e.preventDefault()
          if (viewMode === 'day') onViewChange('week')
          else if (viewMode === 'week') onViewChange('month')
          break
        case 't':
        case 'T':
          e.preventDefault()
          onDateChange(new Date())
          break
        case 'm':
        case 'M':
          e.preventDefault()
          onViewChange('month')
          break
        case 'w':
        case 'W':
          e.preventDefault()
          onViewChange('week')
          break
        case 'd':
        case 'D':
          e.preventDefault()
          onViewChange('day')
          break
      }
    },
    [currentDate, viewMode, onDateChange, onViewChange]
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])
}
