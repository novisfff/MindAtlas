import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import {
  ChevronDown,
  ChevronUp,
  RefreshCw,
  History,
  Loader2,
  AlertCircle,
  Sparkles,
} from 'lucide-react'
import { useLatestWeeklyReportQuery, useGenerateWeeklyReportMutation } from '../queries'
import type { WeeklyReportContent } from '../api/reports'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ReportHistoryDialog } from './ReportHistoryDialog'

export function WeeklyReportCard() {
  const { t, i18n } = useTranslation()
  const [expanded, setExpanded] = useState(true)
  const [historyOpen, setHistoryOpen] = useState(false)

  const { data: report, isLoading } = useLatestWeeklyReportQuery()
  const generateMutation = useGenerateWeeklyReportMutation()

  const locale = i18n.language === 'zh' ? zhCN : enUS

  const formatDateRange = (start: string, end: string) => {
    const startDate = new Date(start)
    const endDate = new Date(end)
    return `${format(startDate, 'MM/dd', { locale })} - ${format(endDate, 'MM/dd', { locale })}`
  }

  const handleGenerate = () => {
    generateMutation.mutate()
  }

  if (isLoading) {
    return (
      <div className="rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-center justify-center h-32">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  const hasContent = report?.content && report.status === 'completed'
  const isGenerating = report?.status === 'generating' || generateMutation.isPending
  const hasFailed = report?.status === 'failed'

  return (
    <>
      <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
        <div
          className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-amber-500" />
            <h3 className="font-semibold">{t('dashboard.weeklyReport.title')}</h3>
            {report && (
              <span className="text-sm text-muted-foreground">
                ({formatDateRange(report.weekStart, report.weekEnd)})
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={(e) => {
                e.stopPropagation()
                setHistoryOpen(true)
              }}
            >
              <History className="h-4 w-4" />
            </Button>
            {expanded ? (
              <ChevronUp className="h-5 w-5 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-5 w-5 text-muted-foreground" />
            )}
          </div>
        </div>

        {expanded && (
          <div className="px-4 pb-4 space-y-4">
            {!report && (
              <EmptyState onGenerate={handleGenerate} isGenerating={isGenerating} />
            )}

            {isGenerating && (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-sm">{t('dashboard.weeklyReport.generating')}</span>
              </div>
            )}

            {hasFailed && !isGenerating && (
              <FailedState
                error={report?.lastError}
                onRetry={handleGenerate}
                isRetrying={generateMutation.isPending}
              />
            )}

            {hasContent && <ReportContent content={report.content!} />}

            {hasContent && (
              <div className="flex justify-end pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleGenerate}
                  disabled={isGenerating}
                >
                  <RefreshCw className={cn('h-4 w-4 mr-2', isGenerating && 'animate-spin')} />
                  {t('dashboard.weeklyReport.regenerate')}
                </Button>
              </div>
            )}
          </div>
        )}
      </div>

      <ReportHistoryDialog open={historyOpen} onOpenChange={setHistoryOpen} />
    </>
  )
}

function EmptyState({
  onGenerate,
  isGenerating,
}: {
  onGenerate: () => void
  isGenerating: boolean
}) {
  const { t } = useTranslation()

  return (
    <div className="text-center py-6">
      <p className="text-muted-foreground mb-4">
        {t('dashboard.weeklyReport.noReport')}
      </p>
      <Button onClick={onGenerate} disabled={isGenerating}>
        {isGenerating ? (
          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
        ) : (
          <Sparkles className="h-4 w-4 mr-2" />
        )}
        {t('dashboard.weeklyReport.generate')}
      </Button>
    </div>
  )
}

function FailedState({
  error,
  onRetry,
  isRetrying,
}: {
  error?: string | null
  onRetry: () => void
  isRetrying: boolean
}) {
  const { t } = useTranslation()

  return (
    <div className="flex items-start gap-3 p-3 rounded-lg bg-destructive/10 text-destructive">
      <AlertCircle className="h-5 w-5 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">{t('dashboard.weeklyReport.failed')}</p>
        {error && <p className="text-xs mt-1 opacity-80 truncate">{error}</p>}
        <Button
          variant="outline"
          size="sm"
          className="mt-2"
          onClick={onRetry}
          disabled={isRetrying}
        >
          {isRetrying && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {t('dashboard.weeklyReport.retry')}
        </Button>
      </div>
    </div>
  )
}

function ReportContent({ content }: { content: WeeklyReportContent }) {
  const { t } = useTranslation()

  return (
    <div className="space-y-4">
      {content.summary && (
        <div>
          <h4 className="text-sm font-medium text-muted-foreground mb-1">
            {t('dashboard.weeklyReport.summary')}
          </h4>
          <p className="text-sm">{content.summary}</p>
        </div>
      )}

      {content.suggestions && content.suggestions.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-muted-foreground mb-1">
            {t('dashboard.weeklyReport.suggestions')}
          </h4>
          <ul className="text-sm space-y-1">
            {content.suggestions.map((suggestion, index) => (
              <li key={index} className="flex items-start gap-2">
                <span className="text-primary">•</span>
                <span>{suggestion}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {content.trends && (
        <div>
          <h4 className="text-sm font-medium text-muted-foreground mb-1">
            {t('dashboard.weeklyReport.trends')}
          </h4>
          <p className="text-sm">{content.trends}</p>
        </div>
      )}
    </div>
  )
}
