import { useTranslation } from 'react-i18next'
import { Filter, Calendar, Layers } from 'lucide-react'

interface GraphFiltersProps {
  showFilters: boolean
  filterDateRange: { start: string; end: string }
  onFilterDateRangeChange?: (range: { start: string; end: string }) => void
  availableTypes: string[]
  selectedTypes: Set<string>
  onSelectedTypesChange: (types: Set<string>) => void
}

export function GraphFilters({
  showFilters,
  filterDateRange,
  onFilterDateRangeChange,
  availableTypes,
  selectedTypes,
  onSelectedTypesChange
}: GraphFiltersProps) {
  const { t } = useTranslation()

  if (!showFilters) return null

  return (
    <div className="bg-white/95 dark:bg-zinc-900/95 backdrop-blur p-4 rounded-md shadow-lg border border-zinc-200 dark:border-zinc-800 w-64 flex flex-col gap-4 animate-in fade-in slide-in-from-top-2">
      {/* Time Filter */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-500 uppercase tracking-wider">
          <Calendar className="w-3 h-3" />
          <span>{t('pages.graph.filterTime')}</span>
        </div>
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-muted-foreground">{t('labels.from')}</span>
            <input
              type="date"
              className="w-full text-sm bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1"
              value={filterDateRange.start}
              onChange={(e) => onFilterDateRangeChange?.({ ...filterDateRange, start: e.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] text-muted-foreground">{t('labels.to')}</span>
            <input
              type="date"
              className="w-full text-sm bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1"
              value={filterDateRange.end}
              onChange={(e) => onFilterDateRangeChange?.({ ...filterDateRange, end: e.target.value })}
            />
          </label>
        </div>
      </div>

      <div className="h-px bg-zinc-100 dark:bg-zinc-800" />

      {/* Type Filter */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold text-zinc-500 uppercase tracking-wider">
            <Layers className="w-3 h-3" />
            <span>{t('pages.graph.filterType')}</span>
          </div>
          <button
            className="text-[10px] text-blue-500 hover:underline"
            onClick={() => onSelectedTypesChange(new Set(availableTypes))}
          >
            {t('actions.reset')}
          </button>
        </div>
        <div className="max-h-40 overflow-y-auto flex flex-col gap-1 pr-1">
          {availableTypes.map(type => (
            <label key={type} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-zinc-50 dark:hover:bg-zinc-800/50 p-1 rounded">
              <input
                type="checkbox"
                checked={selectedTypes.has(type)}
                onChange={(e) => {
                  const next = new Set(selectedTypes)
                  if (e.target.checked) next.add(type)
                  else next.delete(type)
                  onSelectedTypesChange(next)
                }}
                className="rounded border-zinc-300 text-blue-600 focus:ring-blue-500"
              />
              <span className="truncate" title={type}>{type}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}
