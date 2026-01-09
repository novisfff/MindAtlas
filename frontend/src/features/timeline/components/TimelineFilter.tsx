import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'

interface TimelineFilterProps {
  year: number | null
  onYearChange: (year: number | null) => void
  availableYears: number[]
}

export function TimelineFilter({ year, onYearChange, availableYears }: TimelineFilterProps) {
  const { t } = useTranslation()
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
        {t('timeline.filter.all')}
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
