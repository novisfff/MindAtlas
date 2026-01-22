import { Check, X } from 'lucide-react'
import { toast } from 'sonner'
import { useEntryQuery } from '@/features/entries/queries'
import { useCreateRelationMutation, useRelationTypesQuery } from '../queries'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'

interface SuggestedRelationItemProps {
  targetId: string
  relationType?: string
  score: number
  sourceId: string
  onIgnore: (id: string) => void
}

export function SuggestedRelationItem({
  targetId,
  relationType,
  score,
  sourceId,
  onIgnore,
}: SuggestedRelationItemProps) {
  const { t } = useTranslation()
  const { data: targetEntry, isLoading } = useEntryQuery(targetId)
  const { data: relationTypes = [] } = useRelationTypesQuery()
  const createRelationMutation = useCreateRelationMutation()

  const handleAccept = () => {
    // Use AI-predicted relation type if available, otherwise use first enabled type
    let selectedType = relationTypes.find((t) => t.code === relationType && t.enabled)
    if (!selectedType) {
      selectedType = relationTypes.find((t) => t.enabled)
    }
    if (!selectedType) {
      toast.error(t('messages.error'), {
        description: 'No relation types available. Please configure relation types first.',
      })
      return
    }

    createRelationMutation.mutate(
      {
        sourceEntryId: sourceId,
        targetEntryId: targetId,
        relationTypeId: selectedType.id,
      },
      {
        onSuccess: () => {
          toast.success(t('messages.success'), {
            description: `Added relation to "${targetEntry?.title}"`,
          })
        },
        onError: (error) => {
          toast.error(t('messages.error'), {
            description: error instanceof Error ? error.message : 'Failed to create relation',
          })
        },
      }
    )
  }

  if (isLoading) {
    return <div className="animate-pulse h-10 bg-gray-200 rounded-lg" />
  }

  if (!targetEntry) {
    return null
  }

  // Find relation type info for display
  const relationTypeInfo = relationType
    ? relationTypes.find((t) => t.code === relationType)
    : null

  return (
    <div
      className={cn(
        'group flex items-center justify-between p-3 rounded-lg',
        'hover:bg-white hover:shadow-sm transition-all duration-200'
      )}
    >
      {/* Left: Entry info */}
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {targetEntry.type && (
          <div
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: targetEntry.type.color || '#6B7280' }}
          />
        )}
        <span className="text-sm font-medium truncate">{targetEntry.title}</span>
        {targetEntry.type && (
          <span
            className="text-xs px-1.5 py-0.5 rounded flex-shrink-0"
            style={{
              backgroundColor: targetEntry.type.color ? `${targetEntry.type.color}20` : '#6B728020',
              color: targetEntry.type.color || '#6B7280',
            }}
          >
            {targetEntry.type.name}
          </span>
        )}
      </div>

      {/* Middle: Relation type + Score */}
      <div className="flex items-center gap-2">
        {relationTypeInfo && (
          <span
            className="text-xs px-2 py-0.5 rounded-full flex-shrink-0"
            style={{
              backgroundColor: relationTypeInfo.color ? `${relationTypeInfo.color}20` : '#8B5CF620',
              color: relationTypeInfo.color || '#8B5CF6',
            }}
          >
            {relationTypeInfo.name}
          </span>
        )}
        <span className="text-xs font-medium text-purple-600 bg-purple-50 px-2 py-0.5 rounded-full">
          {t('relations.suggestions.matchScore', { score: Math.round(score * 100) })}
        </span>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-1 ml-2">
        <button
          type="button"
          onClick={handleAccept}
          disabled={createRelationMutation.isPending}
          aria-label={t('relations.suggestions.acceptTooltip')}
          title={t('relations.suggestions.acceptTooltip')}
          className={cn(
            'p-1.5 rounded hover:bg-green-100 text-gray-500 hover:text-green-600',
            'transition-colors disabled:opacity-50'
          )}
        >
          <Check className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => onIgnore(targetId)}
          aria-label={t('relations.suggestions.ignoreTooltip')}
          title={t('relations.suggestions.ignoreTooltip')}
          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-gray-700 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
