import { cn } from '@/lib/utils'

interface TimelineFilterProps {
  year: number | null
  onYearChange: (year: number | null) => void
  availableYears: number[]
}

export function TimelineFilter({ year, onYearChange, availableYears }: TimelineFilterProps) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <button
        onClick={() => onYearChange(null)}
        className={cn(
          'px-3 py-1.5 text-sm rounded-full transition-colors',
          year === null
            ? 'bg-primary text-primary-foreground'
            : 'bg-muted hover:bg-muted/80'
        )}
      >
        All
      </button>
      {availableYears.map((y) => (
        <button
          key={y}
          onClick={() => onYearChange(y)}
          className={cn(
            'px-3 py-1.5 text-sm rounded-full transition-colors',
            year === y
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted hover:bg-muted/80'
          )}
        >
          {y}
        </button>
      ))}
    </div>
  )
}
