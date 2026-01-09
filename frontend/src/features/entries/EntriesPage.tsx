import { useEffect, useMemo, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { EntriesList } from './components/EntriesList'
import { EntriesToolbar } from './components/EntriesToolbar'
import { EntriesPagination } from './components/EntriesPagination'
import { useEntriesQuery } from './queries'
import { useEntryTypesQuery } from '@/features/entry-types/queries'
import { useDebounce } from '@/hooks/useDebounce'
import { Entry } from '@/types'

const PAGE_SIZE = 10

export default function EntriesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  // URL state
  const page = Number(searchParams.get('page')) || 1
  const typeId = searchParams.get('type') || ''
  const tagIds = searchParams.get('tags')?.split(',').filter(Boolean) || []
  const timeFrom = searchParams.get('from') || ''
  const timeTo = searchParams.get('to') || ''

  // Local search state with debounce
  const [searchInput, setSearchInput] = useState(searchParams.get('q') || '')
  const debouncedSearch = useDebounce(searchInput, 300)

  // Queries
  const { data: entryTypes = [], isLoading: isTypesLoading } = useEntryTypesQuery()
  const {
    data: entriesPage,
    isLoading: isEntriesLoading,
    isError,
    error,
  } = useEntriesQuery({
    q: debouncedSearch,
    typeId: typeId || undefined,
    tagIds: tagIds.length > 0 ? tagIds : undefined,
    timeFrom: timeFrom || undefined,
    timeTo: timeTo || undefined,
    page,
    size: PAGE_SIZE,
  })

  // Sync search to URL when debounced value changes
  useEffect(() => {
    const currentQ = searchParams.get('q') || ''
    if (debouncedSearch !== currentQ) {
      updateParams({ q: debouncedSearch, page: 1 })
    }
  }, [debouncedSearch, searchParams])

  const updateParams = (updates: Record<string, string | number | string[] | undefined | null>) => {
    const newParams = new URLSearchParams(searchParams)
    
    Object.entries(updates).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)) {
        newParams.delete(key)
      } else if (Array.isArray(value)) {
        newParams.set(key, value.join(','))
      } else {
        newParams.set(key, String(value))
      }
    })

    setSearchParams(newParams, { replace: true })
  }

  const handleTypeChange = (id: string) => {
    updateParams({ type: id, page: 1 })
  }

  const handleTagsChange = (tags: string[]) => {
    updateParams({ tags, page: 1 })
  }

  const handleTimeFromChange = (val: string) => {
    updateParams({ from: val, page: 1 })
  }

  const handleTimeToChange = (val: string) => {
    updateParams({ to: val, page: 1 })
  }

  const handlePageChange = (p: number) => {
    updateParams({ page: p })
  }

  const handleEntryClick = (entry: Entry) => {
    navigate(`/entries/${encodeURIComponent(entry.id)}`)
  }

  const handleCreateClick = () => {
    navigate('/entries/new')
  }

  if (isError) {
    return (
      <div className="container mx-auto py-8 px-4 max-w-7xl">
        <div className="text-center py-16">
          <h2 className="text-xl font-semibold text-destructive mb-2">{t('messages.failedToLoad')}</h2>
          <p className="text-muted-foreground">
            {error instanceof Error ? error.message : t('messages.error')}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto py-8 px-4 max-w-7xl">
      <div className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight mb-2">{t('pages.entries.title')}</h1>
        <p className="text-muted-foreground">{t('pages.entries.subtitle')}</p>
      </div>

      <EntriesToolbar
        searchTerm={searchInput}
        onSearchChange={setSearchInput}
        selectedType={typeId}
        onTypeChange={handleTypeChange}
        selectedTags={tagIds}
        onTagsChange={handleTagsChange}
        timeFrom={timeFrom}
        onTimeFromChange={handleTimeFromChange}
        timeTo={timeTo}
        onTimeToChange={handleTimeToChange}
        entryTypes={entryTypes}
        isTypesLoading={isTypesLoading}
        onCreateClick={handleCreateClick}
      />

      <EntriesList
        isLoading={isEntriesLoading}
        entries={entriesPage?.content || []}
        onEntryClick={handleEntryClick}
      />

      {!isEntriesLoading && entriesPage && (
        <EntriesPagination
          currentPage={page}
          totalPages={entriesPage.totalPages}
          onPageChange={handlePageChange}
        />
      )}
    </div>
  )
}
