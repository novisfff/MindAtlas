import { useRef, type CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { useDraggable } from '@dnd-kit/core'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface CalendarEventProps {
  entry: Entry
  compact?: boolean
  isDragging?: boolean
  resizable?: boolean
  showStartIndicator?: boolean
  onClick?: () => void
  onResizeStart?: (entry: Entry, direction: 'left' | 'right', e: React.PointerEvent) => void
}

export function CalendarEvent({
  entry,
  compact = false,
  isDragging = false,
  resizable = false,
  showStartIndicator = true,
  onClick,
  onResizeStart
}: CalendarEventProps) {
  const suppressClickRef = useRef(false)
  const draggableId = isDragging ? `overlay-${entry.id}` : entry.id

  const { attributes, listeners, setNodeRef, transform, isDragging: isDraggingInternal } = useDraggable({
    id: draggableId,
    disabled: isDragging,
  })
  const bgColor = entry.type?.color ? `${entry.type.color}20` : 'rgb(var(--muted))'
  const borderColor = entry.type?.color || 'rgb(var(--border))'

  const hideOriginalWhileDragging = isDraggingInternal && !isDragging

  const style: CSSProperties = {
    backgroundColor: bgColor,
    borderLeft: `3px solid ${showStartIndicator ? borderColor : 'transparent'}`,
    transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined,
    opacity: hideOriginalWhileDragging ? 0 : (isDragging ? 0.8 : 1),
    visibility: hideOriginalWhileDragging ? 'hidden' : undefined,
    pointerEvents: hideOriginalWhileDragging ? 'none' : undefined,
  }

  const handleResize = (direction: 'left' | 'right') => (e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    e.nativeEvent.stopImmediatePropagation?.()
    e.currentTarget.setPointerCapture?.(e.pointerId)
    suppressClickRef.current = true
    onResizeStart?.(entry, direction, e)
  }

  const handleResizePointerUp = (e: React.PointerEvent) => {
    window.setTimeout(() => {
      suppressClickRef.current = false
    }, 0)
  }

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      className={cn(
        'group block rounded-r px-2 text-xs truncate cursor-grab font-medium relative', // added group, relative
        'transition-shadow',
        compact ? 'py-0.5' : 'py-1',
        isDragging && 'shadow-lg'
      )}
      style={style}
      title={entry.title}
      onClick={(e) => {
        e.stopPropagation()
        if (hideOriginalWhileDragging) return
        if (suppressClickRef.current) {
          suppressClickRef.current = false
          e.preventDefault()
          return
        }
        onClick?.()
      }}
    >
      <div className="absolute inset-0 bg-black/5 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
      <div className="relative z-10 block truncate">
        {entry.title}
      </div>

      {/* Resize Handles - Only visible on hover and if resizable */}
      {resizable && !isDragging && (
        <>
          {/* Left Handle */}
          <div
            className="absolute left-0 top-0 bottom-0 w-2 cursor-ew-resize opacity-0 group-hover:opacity-100 hover:bg-black/10 transition-opacity z-10 select-none touch-none"
            onPointerDown={handleResize('left')}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
          />
          {/* Right Handle */}
          <div
            className="absolute right-0 top-0 bottom-0 w-2 cursor-ew-resize opacity-0 group-hover:opacity-100 hover:bg-black/10 transition-opacity z-10 select-none touch-none"
            onPointerDown={handleResize('right')}
            onPointerUp={handleResizePointerUp}
            onPointerCancel={handleResizePointerUp}
          />
        </>
      )}
    </div>
  )
}
