import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import type { EntryTimeMode } from '../api/entries'

const inputClass = cn(
  'w-full px-3 py-2 rounded-lg border bg-background',
  'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
)

interface EntryTimeSectionProps {
  timeMode: EntryTimeMode
  timeAt: string
  onTimeAtChange: (value: string) => void
  timeFrom: string
  onTimeFromChange: (value: string) => void
  timeTo: string
  onTimeToChange: (value: string) => void
}

export function EntryTimeSection({
  timeMode,
  timeAt, onTimeAtChange,
  timeFrom, onTimeFromChange,
  timeTo, onTimeToChange,
}: EntryTimeSectionProps) {
  const { t } = useTranslation()

  if (timeMode === 'POINT') {
    return (
      <div>
        <label htmlFor="entry-time-at" className="block text-sm font-medium mb-1.5">
          {t('labels.date')}
        </label>
        <input
          id="entry-time-at"
          type="date"
          value={timeAt}
          onChange={(e) => onTimeAtChange(e.target.value)}
          required
          className={inputClass}
        />
      </div>
    )
  }

  if (timeMode === 'RANGE') {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label htmlFor="entry-time-from" className="block text-sm font-medium mb-1.5">
            {t('labels.from')}
          </label>
          <input
            id="entry-time-from"
            type="date"
            value={timeFrom}
            onChange={(e) => onTimeFromChange(e.target.value)}
            required
            className={inputClass}
          />
        </div>
        <div>
          <label htmlFor="entry-time-to" className="block text-sm font-medium mb-1.5">
            {t('labels.to')}
          </label>
          <input
            id="entry-time-to"
            type="date"
            value={timeTo}
            onChange={(e) => onTimeToChange(e.target.value)}
            required
            className={inputClass}
          />
        </div>
      </div>
    )
  }

  return null
}
