import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { KnowledgeGraph, useGraphDataQuery, useLightRagGraphQuery } from '@/features/graph'
import { useTranslation } from 'react-i18next'
import { GraphMode, ModeSwitch } from './components/ModeSwitch'

export function GraphPage() {
  const { t } = useTranslation()
  const [mode, setMode] = useState<GraphMode>('system')
  const [dateRange, setDateRange] = useState<{ start: string; end: string }>({ start: '', end: '' })

  const systemQuery = useGraphDataQuery({
    timeFrom: dateRange.start || undefined,
    timeTo: dateRange.end || undefined,
  })

  const lightRagQuery = useLightRagGraphQuery({}, mode === 'lightrag')

  const currentQuery = mode === 'system' ? systemQuery : lightRagQuery
  const { data, isLoading, error } = currentQuery

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
      <div className="border rounded-lg bg-card overflow-hidden h-[calc(100vh-200px)]">
        <div className="flex items-center justify-center h-full flex-col">
          <h1 className="text-2xl font-bold mb-4">{t('pages.graph.title')}</h1>
          <ModeSwitch mode={mode} onModeChange={setMode} />
          <p className="text-muted-foreground mt-4">
            {mode === 'system' ? t('pages.graph.noData') : t('pages.graph.noLightRagData')}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="h-[calc(100vh-60px)]">
        <KnowledgeGraph
          data={data}
          // width/height defaults to full container if not specified, 
          // or we can just let it fill the parent div which has fixed height
          width={window.innerWidth - 300} // Approximate width minus sidebar
          height={window.innerHeight - 100} // Approximate height
          filterDateRange={mode === 'system' ? dateRange : { start: '', end: '' }}
          onFilterDateRangeChange={mode === 'system' ? setDateRange : undefined}
          mode={mode}
          onModeChange={setMode}
        />
      </div>
    </div>
  )
}
