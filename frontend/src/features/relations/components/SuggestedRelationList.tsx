import { useState, useEffect } from 'react'
import { Sparkles, AlertCircle, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useRelationRecommendationsQuery, useEntryRelationsQuery } from '../queries'
import { SuggestedRelationItem } from './SuggestedRelationItem'
import { SuggestionSkeleton } from './SuggestionSkeleton'

interface SuggestedRelationListProps {
  entryId: string
  autoTrigger?: boolean
}

export function SuggestedRelationList({ entryId, autoTrigger = false }: SuggestedRelationListProps) {
  const { t } = useTranslation()
  const [ignoredIds, setIgnoredIds] = useState<Set<string>>(new Set())
  const [loadingMessage, setLoadingMessage] = useState<string | null>(null)
  const [manualTrigger, setManualTrigger] = useState(false)

  const shouldFetch = autoTrigger || manualTrigger

  const {
    data: recommendations,
    isLoading: loadingRecommendations,
    isError,
    refetch,
    isFetching,
  } = useRelationRecommendationsQuery(entryId, { enabled: shouldFetch })

  const { data: existingRelations = [] } = useEntryRelationsQuery(entryId)

  // Progressive loading messages
  useEffect(() => {
    if (!loadingRecommendations && !isFetching) {
      setLoadingMessage(null)
      return
    }

    setLoadingMessage(null)
    const timer1 = setTimeout(() => {
      setLoadingMessage(t('relations.suggestions.loadingSlow'))
    }, 3000)

    const timer2 = setTimeout(() => {
      setLoadingMessage(t('relations.suggestions.loadingVerySlow'))
    }, 15000)

    return () => {
      clearTimeout(timer1)
      clearTimeout(timer2)
    }
  }, [loadingRecommendations, isFetching, t])

  // Filter out existing relations (bidirectional check)
  const existingRelationIds = new Set(
    existingRelations.flatMap((rel) => [rel.sourceEntry.id, rel.targetEntry.id])
  )

  // Filter candidates
  const candidates =
    recommendations?.filter(
      (item) =>
        !existingRelationIds.has(item.targetEntryId) && !ignoredIds.has(item.targetEntryId)
    ) || []

  // Initial state: Not triggered yet
  if (!shouldFetch) {
    return (
      <div className="mb-6 p-6 bg-gray-50/50 border-2 border-dashed border-gray-200 rounded-xl flex flex-col items-center justify-center text-center">
        <div className="p-3 bg-purple-100 rounded-full mb-3">
          <Sparkles className="w-5 h-5 text-purple-600" />
        </div>
        <h3 className="text-sm font-semibold text-gray-900 mb-1">
          {t('relations.suggestions.title')}
        </h3>
        <p className="text-sm text-gray-500 mb-4 max-w-xs">
          {t('relations.suggestions.intro')}
        </p>
        <button
          onClick={() => setManualTrigger(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 hover:text-gray-900 transition-colors shadow-sm"
        >
          <Sparkles className="w-4 h-4 text-purple-500" />
          {t('relations.suggestions.getSuggestions')}
        </button>
      </div>
    )
  }

  // Loading state
  if (loadingRecommendations || isFetching) {
    return (
      <div className="mb-6 p-4 bg-gray-50/50 border-2 border-dashed border-gray-200 rounded-xl">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles className="w-4 h-4 text-purple-500" />
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">
            {t('relations.suggestions.title')}
          </h3>
        </div>
        <SuggestionSkeleton />
        {loadingMessage && (
          <div className="text-xs text-gray-400 mt-2 flex items-center gap-2 animate-in fade-in slide-in-from-bottom-1">
            <div className="w-1 h-1 rounded-full bg-gray-400 animate-pulse" />
            {loadingMessage}
          </div>
        )}
      </div>
    )
  }

  // Error state
  if (isError) {
    return (
      <div className="mb-6 p-4 bg-gray-50/50 border-2 border-dashed border-gray-200 rounded-xl">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles className="w-4 h-4 text-purple-500" />
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">
            {t('relations.suggestions.title')}
          </h3>
        </div>
        <div className="flex items-center justify-between p-3 rounded-lg bg-white/50">
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <AlertCircle className="w-4 h-4" />
            {t('relations.suggestions.error')}
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            {t('relations.suggestions.retry')}
          </button>
        </div>
      </div>
    )
  }

  // Empty state
  if (candidates.length === 0) {
    return (
      <div className="mb-6 p-4 bg-gray-50/50 border-2 border-dashed border-gray-200 rounded-xl text-center">
        <p className="text-sm text-gray-500">{t('relations.suggestions.noSuggestions')}</p>
        <button
          type="button"
          onClick={() => refetch()}
          className="mt-2 text-xs text-purple-600 hover:underline"
        >
          {t('relations.suggestions.tryAgain')}
        </button>
      </div>
    )
  }

  return (
    <div className="mb-6 p-4 bg-gray-50/50 border-2 border-dashed border-gray-200 rounded-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-purple-500" />
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">
            {t('relations.suggestions.title')}
          </h3>
        </div>
        <button
          onClick={() => refetch()}
          className="p-1 rounded-full hover:bg-gray-200 text-gray-400 hover:text-gray-600 transition-colors"
          title={t('relations.suggestions.retry')}
        >
          <RefreshCw className="w-3 h-3" />
        </button>
      </div>

      {/* List */}
      <div className="space-y-1">
        {candidates.map((item) => (
          <SuggestedRelationItem
            key={item.targetEntryId}
            targetId={item.targetEntryId}
            relationType={item.relationType}
            score={item.score}
            sourceId={entryId}
            onIgnore={(id) => setIgnoredIds((prev) => new Set(prev).add(id))}
          />
        ))}
      </div>
    </div>
  )
}
