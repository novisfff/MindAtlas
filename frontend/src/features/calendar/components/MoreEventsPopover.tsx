import { useState } from 'react'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { useTranslation } from 'react-i18next'
import { CalendarEvent } from './CalendarEvent'
import type { Entry } from '@/types'

interface MoreEventsPopoverProps {
  date: Date
  entries: Entry[]
  visibleCount: number
}

export function MoreEventsPopover({
  date,
  entries,
  visibleCount,
}: MoreEventsPopoverProps) {
  const [isOpen, setIsOpen] = useState(false)
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'zh' ? zhCN : enUS
  const hiddenCount = entries.length - visibleCount

  if (hiddenCount <= 0) return null

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        {t('calendar.moreEvents', { count: hiddenCount })}
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute left-0 top-full z-50 mt-1 w-64 rounded-lg border bg-background p-3 shadow-lg">
            <div className="mb-2 font-medium">
              {format(date, 'MMMM d', { locale })}
            </div>
            <div className="space-y-1 max-h-48 overflow-auto">
              {entries.map((entry) => (
                <CalendarEvent key={entry.id} entry={entry} />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
