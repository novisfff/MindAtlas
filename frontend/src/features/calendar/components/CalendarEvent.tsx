import { Link } from 'react-router-dom'
import { useDraggable } from '@dnd-kit/core'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface CalendarEventProps {
  entry: Entry
  compact?: boolean
  isDragging?: boolean
}

export function CalendarEvent({ entry, compact = false, isDragging = false }: CalendarEventProps) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: entry.id })
  const bgColor = entry.type?.color ? `${entry.type.color}20` : 'rgb(var(--muted))'
  const borderColor = entry.type?.color || 'rgb(var(--border))'

  const style = {
    backgroundColor: bgColor,
    borderLeft: `2px solid ${borderColor}`,
    transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined,
    opacity: isDragging ? 0.8 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      className={cn(
        'block rounded px-1.5 text-xs truncate cursor-grab',
        'hover:opacity-80 transition-opacity',
        compact ? 'py-0.5' : 'py-1',
        isDragging && 'shadow-lg'
      )}
      style={style}
      title={entry.title}
      onClick={(e) => e.stopPropagation()}
    >
      <Link to={`/entries/${entry.id}`} className="block truncate">
        {entry.title}
      </Link>
    </div>
  )
}
