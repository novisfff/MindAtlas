import { Loader2 } from 'lucide-react'
import { KnowledgeGraph, useGraphDataQuery } from '@/features/graph'
import { useTranslation } from 'react-i18next'

export function GraphPage() {
  const { data, isLoading, error } = useGraphDataQuery()
  const { t } = useTranslation()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[600px]">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="text-center py-16">
        <p className="text-destructive">{t('pages.graph.failedToLoad')}</p>
      </div>
    )
  }

  if (data.nodes.length === 0) {
    return (
      <div className="text-center py-16">
        <h1 className="text-2xl font-bold mb-4">{t('pages.graph.title')}</h1>
        <p className="text-muted-foreground">{t('pages.graph.noData')}</p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">{t('pages.graph.title')}</h1>
      <div className="border rounded-lg bg-card overflow-hidden">
        <KnowledgeGraph data={data} width={1000} height={600} />
      </div>
    </div>
  )
}
