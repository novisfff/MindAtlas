import type { Tag } from '@/types'
import { cn } from '@/lib/utils'

interface TagChipsProps {
  tags?: Tag[]
  className?: string
}

export function TagChips({ tags, className }: TagChipsProps) {
  if (!tags?.length) return null

  return (
    <div className={cn('flex flex-wrap gap-1.5', className)}>
      {tags.map(tag => (
        <span
          key={tag.id}
          className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-secondary text-secondary-foreground"
          style={tag.color ? { backgroundColor: tag.color + '20', color: tag.color } : undefined}
        >
          {tag.name}
        </span>
      ))}
    </div>
  )
}
