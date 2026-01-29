import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface MultiDayEventProps {
  entry: Entry
  span: number
  isStart?: boolean
  isEnd?: boolean
}

export function MultiDayEvent({
  entry,
  span,
  isStart = true,
  isEnd = true,
}: MultiDayEventProps) {
  const bgColor = entry.type.color || '#6B7280'

  return (
    <Link
      to={`/entries/${entry.id}`}
      className={cn(
        'block h-5 text-xs text-white truncate px-1.5 leading-5',
        'hover:opacity-80 transition-opacity',
        isStart && 'rounded-l',
        isEnd && 'rounded-r'
      )}
      style={{
        backgroundColor: bgColor,
        gridColumn: `span ${span}`,
      }}
      title={entry.title}
    >
      {isStart && entry.title}
    </Link>
  )
}
