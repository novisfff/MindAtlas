import { Link } from 'react-router-dom'
import { FileText, ArrowRight } from 'lucide-react'
import type { Entry } from '@/types'
import { useTranslation } from 'react-i18next'

function formatTimeAgo(dateString: string, t: (key: string, options?: any) => string): string {
  const date = new Date(dateString)
  const now = new Date()
  const seconds = Math.floor((now.getTime() - date.getTime()) / 1000)

  if (seconds < 60) return t('time.justNow')
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}${t('time.ago.m')}`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}${t('time.ago.h')}`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}${t('time.ago.d')}`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}${t('time.ago.mo')}`
  return `${Math.floor(months / 12)}${t('time.ago.y')}`
}

interface RecentEntriesProps {
  entries: Entry[]
}

export function RecentEntries({ entries }: RecentEntriesProps) {
  const { t } = useTranslation()

  if (entries.length === 0) {
    return (
      <div className="rounded-xl border bg-card p-6 shadow-sm h-full flex flex-col items-center justify-center text-center">
        <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center mb-4">
          <FileText className="h-6 w-6 text-primary" />
        </div>
        <h3 className="font-semibold mb-2">{t('dashboard.recentEntries.noEntries')}</h3>
        <p className="text-sm text-muted-foreground mb-6 max-w-[200px]">
          {t('dashboard.recentEntries.startJourney')}
        </p>
        <Link
          to="/entries/new"
          className="inline-flex items-center justify-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          {t('dashboard.recentEntries.createEntry')} <ArrowRight className="ml-2 w-4 h-4" />
        </Link>
      </div>
    )
  }

  return (
    <div className="rounded-xl border bg-card shadow-sm h-full flex flex-col">
      <div className="p-6 pb-2 flex items-center justify-between">
        <h3 className="font-semibold">{t('dashboard.recentEntries.title')}</h3>
        <Link
          to="/entries"
          className="text-sm text-muted-foreground hover:text-primary flex items-center gap-1 transition-colors"
        >
          {t('dashboard.recentEntries.viewAll')} <ArrowRight className="w-3 h-3" />
        </Link>
      </div>
      <div className="p-6 pt-2 space-y-2">
        {entries.map((entry) => (
          <Link
            key={entry.id}
            to={`/entries/${entry.id}`}
            className="group flex items-start gap-4 p-3 rounded-xl hover:bg-muted/40 transition-all duration-200 border border-transparent hover:border-border"
          >
            <div
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg transition-transform duration-200 group-hover:scale-105"
              style={{ backgroundColor: `${entry.type.color}15` }}
            >
              <FileText className="w-5 h-5" style={{ color: entry.type.color }} />
            </div>
            <div className="flex-1 min-w-0 py-0.5">
              <p className="font-medium truncate group-hover:text-primary transition-colors">
                {entry.title}
              </p>
              <div className="flex items-center gap-2 mt-1">
                <span
                  className="inline-flex items-center rounded-sm px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset"
                  style={{
                    backgroundColor: `${entry.type.color}10`,
                    color: entry.type.color,
                    boxShadow: `inset 0 0 0 1px ${entry.type.color}20`,
                  }}
                >
                  {entry.type.name}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatTimeAgo(entry.createdAt, t)}
                </span>
              </div>
            </div>
            <ArrowRight className="w-4 h-4 text-muted-foreground opacity-0 -translate-x-2 transition-all duration-200 group-hover:opacity-100 group-hover:translate-x-0" />
          </Link>
        ))}
      </div>
    </div>
  )
}
