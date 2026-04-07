import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FileText, ArrowRight } from 'lucide-react'
import type { Entry } from '@/types'
import { useTranslation } from 'react-i18next'
import { useEntryQuery } from '@/features/entries/queries'
import { EntryDetailDialog } from '@/features/calendar/components/EntryDetailDialog'
import { cn } from '@/lib/utils'
import { uiChrome, uiRadius } from '@/components/ui/styles'

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
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null)
  const { data: selectedEntry, isLoading: isEntryLoading } = useEntryQuery(selectedEntryId ?? undefined)

  if (entries.length === 0) {
    return (
      <div
        className={cn(
          uiChrome.card,
          'h-full flex flex-col items-center justify-center p-4 text-center',
        )}
      >
        <div
          className={cn(
            uiRadius.pill,
            'mb-3 flex h-11 w-11 items-center justify-center bg-primary/10',
          )}
        >
          <FileText className="h-5 w-5 text-primary" />
        </div>
        <h3 className="font-semibold mb-2">{t('dashboard.recentEntries.noEntries')}</h3>
        <p className="text-sm text-muted-foreground mb-6 max-w-[200px]">
          {t('dashboard.recentEntries.startJourney')}
        </p>
        <Link
          to="/entries/new"
          className={cn(
            uiChrome.control,
            'inline-flex items-center justify-center bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/92',
          )}
        >
          {t('dashboard.recentEntries.createEntry')} <ArrowRight className="ml-2 w-4 h-4" />
        </Link>
      </div>
    )
  }

  return (
    <div className={cn(uiChrome.card, 'h-full flex min-h-0 flex-col')}>
      <div className="flex items-center justify-between p-3 pb-2.5">
        <h3 className="font-semibold">{t('dashboard.recentEntries.title')}</h3>
        <Link
          to="/entries"
          className="text-sm text-muted-foreground hover:text-primary flex items-center gap-1 transition-colors"
        >
          {t('dashboard.recentEntries.viewAll')} <ArrowRight className="w-3 h-3" />
        </Link>
      </div>
      <div className="flex-1 min-h-0 space-y-1 overflow-auto px-3 pb-3 pt-1 custom-scrollbar">
        {entries.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setSelectedEntryId(entry.id)}
            aria-label={t('entry.card.viewEntryAria', { title: entry.title })}
            className={cn(
              uiRadius.control,
              'group flex w-full items-center gap-2.5 border border-transparent p-2 transition-colors hover:border-border/50 hover:bg-background/88',
            )}
          >
            <div
              className={cn(
                uiRadius.inset,
                'flex h-8 w-8 shrink-0 items-center justify-center transition-transform duration-200 group-hover:scale-105',
              )}
              style={{ backgroundColor: `${entry.type.color}15` }}
            >
              <FileText className="w-3.5 h-3.5" style={{ color: entry.type.color }} />
            </div>

            <div className="flex-1 min-w-0 flex items-center gap-3">
              <span className="text-[13px] font-medium leading-tight truncate text-foreground/90 group-hover:text-primary transition-colors">
                {entry.title}
              </span>

              <div className="ml-auto flex items-center gap-1.5 shrink-0">
                <span
                  className={cn(
                    uiRadius.inset,
                    'inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium leading-none ring-1 ring-inset bg-background/50',
                  )}
                  style={{
                    color: entry.type.color,
                    boxShadow: `inset 0 0 0 1px ${entry.type.color}30`,
                  }}
                >
                  {entry.type.name}
                </span>
                <span className="text-[10px] text-muted-foreground/70 leading-none min-w-[2.75rem] text-right">
                  {formatTimeAgo(entry.createdAt, t)}
                </span>
              </div>
            </div>

            <ArrowRight className="w-3 h-3 text-muted-foreground/50 opacity-0 -translate-x-1 transition-all duration-200 group-hover:opacity-100 group-hover:translate-x-0" />
          </button>
        ))}
      </div>
      <EntryDetailDialog
        entry={selectedEntry ?? null}
        open={Boolean(selectedEntryId)}
        loading={isEntryLoading}
        onOpenChange={(open) => !open && setSelectedEntryId(null)}
      />
    </div>
  )
}
