import { useState } from 'react'
import type { DragEndEvent, DragStartEvent } from '@dnd-kit/core'

export function useCalendarDnd() {
  const [activeId, setActiveId] = useState<string | null>(null)

  const handleDragStart = (event: DragStartEvent) => {
    setActiveId(event.active.id as string)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    setActiveId(null)
    const { active, over } = event
    if (!over) return null

    return {
      entryId: active.id as string,
      targetDate: over.id as string,
    }
  }

  const handleDragCancel = () => {
    setActiveId(null)
  }

  return {
    activeId,
    handleDragStart,
    handleDragEnd,
    handleDragCancel,
  }
}
