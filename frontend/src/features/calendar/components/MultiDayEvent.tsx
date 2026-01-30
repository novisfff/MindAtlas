import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { Entry } from '@/types'

interface MultiDayEventProps {
  entry: Entry
  span: number
  isStart?: boolean
  isEnd?: boolean
  onClick?: () => void
}

export function MultiDayEvent({
  entry,
  span,
  isStart = true,
  isEnd = true,
  onClick,
}: MultiDayEventProps) {
  const bgColor = entry.type.color || '#6B7280'

  return (
    <div
      className={cn(
        'block h-5 text-xs text-white truncate px-1.5 leading-5 cursor-pointer',
        'hover:opacity-80 transition-opacity',
        isStart && 'rounded-l',
        isEnd && 'rounded-r'
      )}
      style={{
        backgroundColor: bgColor,
        gridColumn: `span ${span}`,
      }}
      title={entry.title}
      onClick={(e) => {
        e.stopPropagation()
        onClick?.()
      }}
    >
      {isStart && entry.title}
    </div>
  )
}
