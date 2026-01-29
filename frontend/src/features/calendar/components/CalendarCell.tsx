import { format } from 'date-fns'
import { useDroppable } from '@dnd-kit/core'
import { cn } from '@/lib/utils'
import { CalendarEvent } from './CalendarEvent'
import { MoreEventsPopover } from './MoreEventsPopover'
import type { Entry } from '@/types'

const MAX_VISIBLE_ENTRIES = 3

interface CalendarCellProps {
  date: Date
  entries: Entry[]
  isToday: boolean
  isCurrentMonth: boolean
  onClick: () => void
  onDoubleClick?: () => void
}

export function CalendarCell({
  date,
  entries,
  isToday,
  isCurrentMonth,
  onClick,
  onDoubleClick,
}: CalendarCellProps) {
  const dateId = format(date, 'yyyy-MM-dd')
  const { setNodeRef, isOver } = useDroppable({ id: dateId })

  const visibleEntries = entries.slice(0, MAX_VISIBLE_ENTRIES)
  const hiddenEntries = entries.slice(MAX_VISIBLE_ENTRIES)

  return (
    <div
      ref={setNodeRef}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      className={cn(
        'min-h-[100px] p-1 border-b border-r cursor-pointer',
        'hover:bg-muted/50 transition-colors',
        !isCurrentMonth && 'bg-muted/30',
        isOver && 'bg-primary/10'
      )}
    >
      <div
        className={cn(
          'w-7 h-7 flex items-center justify-center rounded-full text-sm',
          isToday && 'bg-primary text-primary-foreground',
          !isCurrentMonth && 'text-muted-foreground'
        )}
      >
        {format(date, 'd')}
      </div>
      <div className="mt-1 space-y-0.5">
        {visibleEntries.map((entry) => (
          <CalendarEvent key={entry.id} entry={entry} />
        ))}
        {hiddenEntries.length > 0 && (
          <MoreEventsPopover
            entries={entries}
            date={date}
            visibleCount={MAX_VISIBLE_ENTRIES}
          />
        )}
      </div>
    </div>
  )
}
