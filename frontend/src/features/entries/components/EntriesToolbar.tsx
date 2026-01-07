import { Search, Plus, Filter, Calendar } from 'lucide-react'
import { EntryType } from '@/types'
import { TagSelector } from '@/features/tags/components/TagSelector'

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
  return (
    <div className="flex flex-col gap-4 mb-6">
      <div className="flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center">
        <div className="flex flex-1 w-full sm:w-auto gap-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <input
              type="text"
              placeholder="Search entries..."
              aria-label="Search entries"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 pl-9"
              value={searchTerm}
              onChange={(e) => onSearchChange(e.target.value)}
            />
          </div>

          <div className="relative w-[180px]">
            <Filter className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
            <select
              aria-label="Filter by type"
              className="flex h-9 w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50 pl-9 appearance-none cursor-pointer"
              value={selectedType}
              onChange={(e) => onTypeChange(e.target.value)}
              disabled={isTypesLoading}
            >
              <option value="">All Types</option>
              {entryTypes.map((type) => (
                <option key={type.id} value={type.id}>
                  {type.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          onClick={onCreateClick}
          className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2 w-full sm:w-auto"
        >
          <Plus className="mr-2 h-4 w-4" />
          New Entry
        </button>
      </div>

      <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center p-4 bg-muted/20 rounded-lg border">
        <div className="flex-1 space-y-2 w-full">
           <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Tags</span>
           <TagSelector value={selectedTags} onChange={onTagsChange} />
        </div>
        
        <div className="flex gap-2 items-end">
          <div className="space-y-1">
             <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider block">From</span>
             <div className="relative">
                <Calendar className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
                <input
                  type="date"
                  className="flex h-9 w-[150px] rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 pl-9"
                  value={timeFrom}
                  onChange={(e) => onTimeFromChange(e.target.value)}
                />
             </div>
          </div>
          <div className="space-y-1">
             <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider block">To</span>
             <div className="relative">
                <Calendar className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
                <input
                  type="date"
                  className="flex h-9 w-[150px] rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 pl-9"
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
