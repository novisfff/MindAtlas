import { useTranslation } from 'react-i18next'
import { Entry } from '@/types'
import { EntryCard } from './EntryCard'
import { FileText } from 'lucide-react'

interface EntriesListProps {
  isLoading: boolean
  entries: Entry[]
  onEntryClick: (entry: Entry) => void
}

function EntrySkeleton() {
  return (
    <div className="h-[200px] rounded-lg border bg-card text-card-foreground shadow-sm animate-pulse p-4">
      <div className="h-6 w-2/3 bg-muted rounded mb-4" />
      <div className="h-4 w-full bg-muted rounded mb-2" />
      <div className="h-4 w-full bg-muted rounded mb-2" />
      <div className="h-4 w-1/2 bg-muted rounded" />
    </div>
  )
}

export function EntriesList({ isLoading, entries, onEntryClick }: EntriesListProps) {
  const { t } = useTranslation()

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <EntrySkeleton key={i} />
        ))}
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center border rounded-lg border-dashed">
        <FileText className="w-12 h-12 text-muted-foreground mb-4" />
        <h3 className="text-lg font-semibold text-foreground">{t('pages.entries.noEntries')}</h3>
        <p className="mt-2 text-sm text-muted-foreground max-w-sm">
          {t('pages.entries.emptyStateDesc')}
        </p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {entries.map((entry) => (
        <EntryCard key={entry.id} entry={entry} onClick={onEntryClick} />
      ))}
    </div>
  )
}
