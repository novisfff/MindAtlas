import { format } from 'date-fns'
import { useDroppable } from '@dnd-kit/core'
import { Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { CalendarDensity } from '../types'
import { calendarRadius, calendarTone } from '../styles'

interface CalendarCellProps {
  date: Date
  density: CalendarDensity
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
  density,
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
  const isCompact = density === 'compact'

  return (
    <div
      ref={setNodeRef}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      className={cn(
        'group relative h-full min-h-0 cursor-pointer border-b border-r border-border/60 transition-colors',
        isCompact ? 'p-1.5' : 'p-2',
        calendarTone.cell,
        !isCurrentMonth && calendarTone.cellMuted,
        isToday && calendarTone.cellToday,
        isOver &&
          'bg-primary/10 shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.18)]',
        className,
      )}
    >
      <button
        type="button"
        aria-label="Create entry"
        className={cn(
          'absolute right-1.5 top-1.5 z-10',
          'inline-flex items-center justify-center border border-transparent',
          calendarRadius.micro,
          isCompact ? 'h-[22px] w-[22px]' : 'h-6 w-6',
          'opacity-0 group-hover:opacity-100 transition-opacity',
          'text-muted-foreground hover:border-border/60 hover:bg-background hover:text-foreground',
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
          'flex items-center justify-center font-semibold transition-colors',
          isCompact ? 'h-6 w-6 text-[11px]' : 'h-7 w-7 text-sm',
          calendarRadius.pill,
          isToday
            ? 'bg-primary text-primary-foreground'
            : 'bg-background/92 text-foreground/90 ring-1 ring-border/50 dark:bg-background/72',
          !isCurrentMonth && 'text-muted-foreground',
        )}
      >
        {format(date, 'd')}
      </div>
      <div className={cn(isCompact ? 'mt-1' : 'mt-1.5')}>
        {/* Events are now rendered by the parent MonthView */}
        {children}
      </div>
    </div>
  )
}
