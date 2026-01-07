import { useState } from 'react'
import { Plus, Search, X, Loader2 } from 'lucide-react'
import type { Entry, RelationType } from '@/types'
import { useRelationTypesQuery } from '../queries'
import { useEntriesQuery } from '@/features/entries/queries'
import { useDebounce } from '@/hooks/useDebounce'
import { cn } from '@/lib/utils'

interface RelationSelectorProps {
  currentEntryId: string
  onAdd: (targetEntryId: string, relationTypeId: string) => void
  isAdding?: boolean
}

export function RelationSelector({ currentEntryId, onAdd, isAdding }: RelationSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedEntry, setSelectedEntry] = useState<Entry | null>(null)
  const [selectedTypeId, setSelectedTypeId] = useState<string>('')

  const { data: relationTypes = [] } = useRelationTypesQuery()
  const debouncedSearch = useDebounce(search, 300)
  const { data: entriesPage } = useEntriesQuery({ q: debouncedSearch, size: 10 })
  const entries = entriesPage?.content?.filter(e => e.id !== currentEntryId) ?? []

  const handleSubmit = () => {
    if (selectedEntry && selectedTypeId) {
      onAdd(selectedEntry.id, selectedTypeId)
      setIsOpen(false)
      setSelectedEntry(null)
      setSelectedTypeId('')
      setSearch('')
    }
  }

  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className={cn(
          'inline-flex items-center gap-1.5 px-3 py-1.5 text-sm',
          'border border-dashed rounded-lg',
          'text-muted-foreground hover:text-foreground hover:border-foreground/50',
          'transition-colors'
        )}
      >
        <Plus className="w-4 h-4" />
        Add Relation
      </button>
    )
  }

  return (
    <div className="border rounded-lg p-4 space-y-4 bg-card">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">Add Relation</h4>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          aria-label="Close"
          className="p-1 hover:bg-accent rounded"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Entry Search */}
      <div className="space-y-2">
        <label className="text-sm text-muted-foreground">Target Entry</label>
        {selectedEntry ? (
          <div className="flex items-center justify-between p-2 border rounded-lg bg-accent/50">
            <span className="text-sm">{selectedEntry.title}</span>
            <button
              type="button"
              onClick={() => setSelectedEntry(null)}
              aria-label="Remove selected entry"
              className="p-1 hover:bg-accent rounded"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ) : (
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search entries..."
              className={cn(
                'w-full pl-9 pr-3 py-2 text-sm border rounded-lg bg-background',
                'focus:outline-none focus:ring-2 focus:ring-ring'
              )}
            />
            {search && entries.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-1 border rounded-lg bg-popover shadow-lg z-10 max-h-48 overflow-auto">
                {entries.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => {
                      setSelectedEntry(entry)
                      setSearch('')
                    }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-accent"
                  >
                    {entry.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Relation Type Select */}
      <div className="space-y-2">
        <label className="text-sm text-muted-foreground">Relation Type</label>
        <select
          value={selectedTypeId}
          onChange={(e) => setSelectedTypeId(e.target.value)}
          className={cn(
            'w-full px-3 py-2 text-sm border rounded-lg bg-background',
            'focus:outline-none focus:ring-2 focus:ring-ring'
          )}
        >
          <option value="">Select type...</option>
          {relationTypes.map((type) => (
            <option key={type.id} value={type.id}>
              {type.name}
            </option>
          ))}
        </select>
      </div>

      {/* Submit */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={!selectedEntry || !selectedTypeId || isAdding}
        className={cn(
          'w-full px-4 py-2 text-sm font-medium rounded-lg',
          'bg-primary text-primary-foreground',
          'hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed',
          'transition-colors'
        )}
      >
        {isAdding ? (
          <span className="inline-flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            Adding...
          </span>
        ) : (
          'Add Relation'
        )}
      </button>
    </div>
  )
}
