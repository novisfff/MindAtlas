import { Search, Plus, Filter, Calendar } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { EntryType } from '@/types'
import { TagSelector } from '@/features/tags/components/TagSelector'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { uiChrome, uiField, uiRadius } from '@/components/ui/styles'

interface EntriesToolbarProps {
  searchTerm: string
  onSearchChange: (term: string) => void
  selectedType: string
  onTypeChange: (typeId: string) => void
  selectedTags: string[]
  onTagsChange: (tags: string[]) => void
  timeFrom: string
  onTimeFromChange: (val: string) => void
  timeTo: string
  onTimeToChange: (val: string) => void
  entryTypes: EntryType[]
  isTypesLoading?: boolean
  onCreateClick: () => void
}

export function EntriesToolbar({
  searchTerm,
  onSearchChange,
  selectedType,
  onTypeChange,
  selectedTags,
  onTagsChange,
  timeFrom,
  onTimeFromChange,
  timeTo,
  onTimeToChange,
  entryTypes,
  isTypesLoading,
  onCreateClick,
}: EntriesToolbarProps) {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col gap-4 mb-6">
      <div className="flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center">
        <div className="flex flex-1 w-full sm:w-auto gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <input
              type="text"
              placeholder={t('pages.entries.searchPlaceholder')}
              aria-label={t('actions.search')}
              className={cn(uiField.input, 'pl-9')}
              value={searchTerm}
              onChange={(e) => onSearchChange(e.target.value)}
            />
          </div>

          <div className="relative w-[180px]">
            <Filter className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
            <select
              aria-label={t('labels.allTypes')}
              className={cn(uiField.select, 'cursor-pointer pl-9')}
              value={selectedType}
              onChange={(e) => onTypeChange(e.target.value)}
              disabled={isTypesLoading}
            >
              <option value="">{t('labels.allTypes')}</option>
              {entryTypes.map((type) => (
                <option key={type.id} value={type.id}>
                  {type.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <Button
          onClick={onCreateClick}
          className="w-full sm:w-auto"
        >
          <Plus className="mr-2 h-4 w-4" />
          {t('actions.newEntry')}
        </Button>
      </div>

      <div className={cn(uiChrome.card, 'flex flex-col gap-4 p-4 sm:flex-row sm:items-center')}>
        <div className="flex-1 space-y-2 w-full">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{t('labels.tags')}</span>
          <TagSelector value={selectedTags} onChange={onTagsChange} />
        </div>

        <div className="flex gap-2 items-end">
          <div className="space-y-1">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider block">{t('labels.from')}</span>
            <div className="relative">
              <Calendar className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
              <input
                type="date"
                className={cn(uiField.input, 'w-[150px] pl-9')}
                value={timeFrom}
                onChange={(e) => onTimeFromChange(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider block">{t('labels.to')}</span>
            <div className="relative">
              <Calendar className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
              <input
                type="date"
                className={cn(uiField.input, 'w-[150px] pl-9')}
                value={timeTo}
                onChange={(e) => onTimeToChange(e.target.value)}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
