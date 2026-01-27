import { Link2, Trash2, ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Relation } from '@/types'
import { cn } from '@/lib/utils'

interface RelationListProps {
  relations: Relation[]
  currentEntryId: string
  onDelete?: (id: string) => void
  isDeleting?: boolean
}

export function RelationList({ relations, currentEntryId, onDelete, isDeleting }: RelationListProps) {
  const { t } = useTranslation()

  if (relations.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-4 text-center">
        {t('entry.noRelations')}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {relations.map((relation) => {
        const isOutgoing = relation.sourceEntry.id === currentEntryId
        const linkedEntry = isOutgoing ? relation.targetEntry : relation.sourceEntry
        const relationName = isOutgoing
          ? relation.relationType.name
          : (relation.relationType.inverseName || relation.relationType.name)

        return (
          <div
            key={relation.id}
            className={cn(
              'flex items-center justify-between p-3 rounded-lg border bg-card',
              'hover:bg-accent/50 transition-colors'
            )}
          >
            <div className="flex items-center gap-3 min-w-0">
              <div
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: relation.relationType.color || '#6B7280' }}
              />
              <span className="text-sm text-muted-foreground flex-shrink-0">
                {relationName}
              </span>
              <ArrowRight className="w-3 h-3 text-muted-foreground flex-shrink-0" />
              <a
                href={`/entries/${linkedEntry.id}`}
                className="text-sm font-medium hover:underline truncate"
              >
                {linkedEntry.title}
              </a>
              {linkedEntry.type && (
                <span
                  className="text-xs px-1.5 py-0.5 rounded flex-shrink-0"
                  style={{
                    backgroundColor: linkedEntry.type.color ? `${linkedEntry.type.color}20` : '#6B728020',
                    color: linkedEntry.type.color || '#6B7280',
                  }}
                >
                  {linkedEntry.type.name}
                </span>
              )}
            </div>
            {onDelete && (
              <button
                type="button"
                onClick={() => onDelete(relation.id)}
                disabled={isDeleting}
                aria-label="Delete relation"
                className={cn(
                  'p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive',
                  'transition-colors disabled:opacity-50'
                )}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
