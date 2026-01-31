import { format } from 'date-fns'
import { useDroppable } from '@dnd-kit/core'
import { Plus } from 'lucide-react'
import { cn } from '@/lib/utils'

const MAX_VISIBLE_ENTRIES = 3

interface CalendarCellProps {
  date: Date
  isToday: boolean
  isCurrentMonth: boolean
  onClick: () => void
  onDoubleClick?: () => void
  onQuickCreate?: () => void
  children?: React.ReactNode
  className?: string
}

export function CalendarCell({
  date,
  isToday,
  isCurrentMonth,
  onClick,
  onDoubleClick,
  onQuickCreate,
  children,
  className,
}: CalendarCellProps) {
  const dateId = format(date, 'yyyy-MM-dd')
  const { setNodeRef, isOver } = useDroppable({ id: dateId })



  return (
    <div
      ref={setNodeRef}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      className={cn(
        'group relative min-h-[104px] p-1.5 border-b border-r border-border/60 cursor-pointer',
        'bg-background/60 hover:bg-muted/40 transition-colors',
        !isCurrentMonth && 'bg-muted/20',
        isOver && 'bg-primary/10',
        className
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
          onQuickCreate?.()
        }}
      >
        <Plus className="h-4 w-4" />
      </button>
      <div
        className={cn(
          'w-7 h-7 flex items-center justify-center rounded-full text-xs font-medium',
          isToday && 'bg-primary text-primary-foreground',
          !isCurrentMonth && 'text-muted-foreground'
        )}
      >
        {format(date, 'd')}
      </div>
      <div className="mt-1 space-y-0.5">
        {/* Events are now rendered by the parent MonthView */}
        {children}
      </div>
    </div>
  )
}
