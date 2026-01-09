import { useState, useMemo } from 'react'
import { Loader2, Clock } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useEntriesQuery } from '@/features/entries/queries'
import { TimelineItem } from './components/TimelineItem'
import { TimelineFilter } from './components/TimelineFilter'
import { useTranslation } from 'react-i18next'

export function TimelinePage() {
  const [selectedYear, setSelectedYear] = useState<number | null>(null)
  const { data, isLoading } = useEntriesQuery({ size: 100 })
  const { t } = useTranslation()

  // Filter entries with time info
  const timelineEntries = useMemo(() => {
    if (!data?.content) return []
    return data.content
      .filter((e) => e.timeMode !== 'NONE')
      .sort((a, b) => {
        const dateA = a.timeAt || a.timeFrom || ''
        const dateB = b.timeAt || b.timeFrom || ''
        return dateB.localeCompare(dateA)
      })
  }, [data])

  // Get available years
  const availableYears = useMemo(() => {
    const years = new Set<number>()
    timelineEntries.forEach((e) => {
      const date = e.timeAt || e.timeFrom
      if (date) years.add(new Date(date).getFullYear())
    })
    return Array.from(years).sort((a, b) => b - a)
  }, [timelineEntries])

  // Filter by year
  const filteredEntries = useMemo(() => {
    if (!selectedYear) return timelineEntries
    return timelineEntries.filter((e) => {
      const date = e.timeAt || e.timeFrom
      return date && new Date(date).getFullYear() === selectedYear
    })
  }, [timelineEntries, selectedYear])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t('pages.timeline.title')}</h1>
          <p className="text-muted-foreground">{t('timeline.description')}</p>
        </div>
      </div>

      {timelineEntries.length === 0 ? (
        <div className="text-center py-16 border rounded-lg bg-card">
          <Clock className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
          <h3 className="font-semibold mb-2">{t('timeline.noEntries')}</h3>
          <p className="text-sm text-muted-foreground mb-4">
            {t('timeline.createPrompt')}
          </p>
          <Link
            to="/entries/new"
            className="inline-flex items-center px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm"
          >
            {t('timeline.createAction')}
          </Link>
        </div>
      ) : (
        <>
          {availableYears.length > 1 && (
            <TimelineFilter
              year={selectedYear}
              onYearChange={setSelectedYear}
              availableYears={availableYears}
            />
          )}

          <div className="max-w-2xl">
            {filteredEntries.map((entry, index) => (
              <TimelineItem
                key={entry.id}
                entry={entry}
                isLast={index === filteredEntries.length - 1}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
