import { Link2, Trash2, ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Relation } from '@/types'
import { cn } from '@/lib/utils'

interface RelationListProps {
  relations: Relation[]
  currentEntryId: string
  compact?: boolean
  onDelete?: (id: string) => void
  isDeleting?: boolean
}

export function RelationList({
  relations,
  currentEntryId,
  compact = false,
  onDelete,
  isDeleting,
}: RelationListProps) {
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
        const linkedEntry = isOutgoing
          ? relation.targetEntry
          : relation.sourceEntry
        const relationName = isOutgoing
          ? relation.relationType.name
          : relation.relationType.inverseName || relation.relationType.name

        return (
          <div
            key={relation.id}
            className={cn(
              'flex items-center justify-between overflow-hidden rounded-lg border bg-card',
              compact ? 'p-2.5' : 'p-3',
              'hover:bg-accent/50 transition-colors',
            )}
          >
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <div
                className={cn(
                  'rounded-full flex-shrink-0',
                  compact ? 'w-1.5 h-1.5' : 'w-2 h-2',
                )}
                style={{
                  backgroundColor: relation.relationType.color || '#6B7280',
                }}
              />
              <span
                className={cn(
                  'text-muted-foreground flex-shrink-0',
                  compact ? 'text-[12px]' : 'text-sm',
                )}
              >
                {relationName}
              </span>
              <ArrowRight className="w-3 h-3 text-muted-foreground flex-shrink-0" />
              <a
                href={`/entries/${linkedEntry.id}`}
                className={cn(
                  'min-w-0 flex-1 truncate font-medium hover:underline',
                  compact ? 'text-[13px]' : 'text-sm',
                )}
              >
                {linkedEntry.title}
              </a>
              {linkedEntry.type && (
                <span
                  className={cn(
                    'rounded flex-shrink-0',
                    compact
                      ? 'text-[10px] px-1.5 py-0.5'
                      : 'text-xs px-1.5 py-0.5',
                  )}
                  style={{
                    backgroundColor: linkedEntry.type.color
                      ? `${linkedEntry.type.color}20`
                      : '#6B728020',
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
                  compact
                    ? 'p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive'
                    : 'p-1.5 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive',
                  'transition-colors disabled:opacity-50',
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
