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
  Calendar,
} from 'lucide-react'
import {
  useLatestWeeklyReportQuery,
  useGenerateWeeklyReportMutation,
  useLatestMonthlyReportQuery,
  useGenerateMonthlyReportMutation,
} from '../queries'
import type { WeeklyReportContent, MonthlyReportContent } from '../api/reports'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ReportHistoryDialog } from './ReportHistoryDialog'

type ReportType = 'weekly' | 'monthly'

export function AIReportsContainer() {
  const { t, i18n } = useTranslation()
  const [activeTab, setActiveTab] = useState<ReportType>('weekly')
  const [expanded, setExpanded] = useState(true)
  const [historyOpen, setHistoryOpen] = useState(false)

  const locale = i18n.language === 'zh' ? zhCN : enUS

  // Weekly report hooks
  const { data: weeklyReport, isLoading: weeklyLoading } = useLatestWeeklyReportQuery()
  const weeklyMutation = useGenerateWeeklyReportMutation()

  // Monthly report hooks
  const { data: monthlyReport, isLoading: monthlyLoading } = useLatestMonthlyReportQuery()
  const monthlyMutation = useGenerateMonthlyReportMutation()

  const isLoading = activeTab === 'weekly' ? weeklyLoading : monthlyLoading
  const report = activeTab === 'weekly' ? weeklyReport : monthlyReport
  const mutation = activeTab === 'weekly' ? weeklyMutation : monthlyMutation

  const formatDateRange = (start: string, end: string) => {
    const startDate = new Date(start)
    const endDate = new Date(end)
    return `${format(startDate, 'MM/dd', { locale })} - ${format(endDate, 'MM/dd', { locale })}`
  }

  const getDateRange = () => {
    if (!report) return null
    if (activeTab === 'weekly' && weeklyReport) {
      return formatDateRange(weeklyReport.weekStart, weeklyReport.weekEnd)
    }
    if (activeTab === 'monthly' && monthlyReport) {
      return formatDateRange(monthlyReport.monthStart, monthlyReport.monthEnd)
    }
    return null
  }

  const handleGenerate = () => {
    mutation.mutate()
  }

  const hasContent = report?.content && report.status === 'completed'
  const isGenerating = report?.status === 'generating' || mutation.isPending
  const hasFailed = report?.status === 'failed'

  return (
    <>
      <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between p-4">
          <div className="flex items-center gap-3">
            <Sparkles className="w-5 h-5 text-amber-500" />
            <h3 className="font-semibold">{t('dashboard.reports.title')}</h3>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-lg bg-muted p-1">
              <button
                onClick={() => setActiveTab('weekly')}
                className={cn(
                  'px-3 py-1 text-xs font-medium rounded-md transition-colors',
                  activeTab === 'weekly'
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {t('dashboard.reports.tabs.weekly')}
              </button>
              <button
                onClick={() => setActiveTab('monthly')}
                className={cn(
                  'px-3 py-1 text-xs font-medium rounded-md transition-colors',
                  activeTab === 'monthly'
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {t('dashboard.reports.tabs.monthly')}
              </button>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setHistoryOpen(true)}
              aria-label={t('dashboard.weeklyReport.history')}
            >
              <History className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setExpanded(!expanded)}
              aria-label={expanded ? t('actions.collapse') : t('actions.expand')}
            >
              {expanded ? (
                <ChevronUp className="h-5 w-5 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-5 w-5 text-muted-foreground" />
              )}
            </Button>
          </div>
        </div>

        {/* Date range subtitle */}
        {getDateRange() && (
          <div className="px-4 pb-2 -mt-2">
            <span className="text-sm text-muted-foreground">
              {getDateRange()}
            </span>
          </div>
        )}

        {/* Content */}
        {expanded && (
          <div className="px-4 pb-4 space-y-4">
            {isLoading && (
              <div className="flex items-center justify-center h-32">
                <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
              </div>
            )}

            {!isLoading && !report && (
              <EmptyState
                type={activeTab}
                onGenerate={handleGenerate}
                isGenerating={isGenerating}
              />
            )}

            {!isLoading && isGenerating && (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span className="text-sm">
                  {activeTab === 'weekly'
                    ? t('dashboard.weeklyReport.generating')
                    : t('dashboard.monthlyReport.generating')}
                </span>
              </div>
            )}

            {!isLoading && hasFailed && !isGenerating && (
              <FailedState
                type={activeTab}
                error={report?.lastError}
                onRetry={handleGenerate}
                isRetrying={mutation.isPending}
              />
            )}

            {!isLoading && hasContent && (
              <ReportContent type={activeTab} content={report.content!} />
            )}

            {!isLoading && hasContent && (
              <div className="flex justify-end pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleGenerate}
                  disabled={isGenerating}
                >
                  <RefreshCw className={cn('h-4 w-4 mr-2', isGenerating && 'animate-spin')} />
                  {activeTab === 'weekly'
                    ? t('dashboard.weeklyReport.regenerate')
                    : t('dashboard.monthlyReport.regenerate')}
                </Button>
              </div>
            )}
          </div>
        )}
      </div>

      <ReportHistoryDialog
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        type={activeTab}
      />
    </>
  )
}

function EmptyState({
  type,
  onGenerate,
  isGenerating,
}: {
  type: ReportType
  onGenerate: () => void
  isGenerating: boolean
}) {
  const { t } = useTranslation()
  const isWeekly = type === 'weekly'

  return (
    <div className="text-center py-6">
      <p className="text-muted-foreground mb-4">
        {isWeekly
          ? t('dashboard.weeklyReport.noReport')
          : t('dashboard.monthlyReport.noReport')}
      </p>
      <Button onClick={onGenerate} disabled={isGenerating}>
        {isGenerating ? (
          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
        ) : isWeekly ? (
          <Sparkles className="h-4 w-4 mr-2" />
        ) : (
          <Calendar className="h-4 w-4 mr-2" />
        )}
        {isWeekly
          ? t('dashboard.weeklyReport.generate')
          : t('dashboard.monthlyReport.generate')}
      </Button>
    </div>
  )
}

function FailedState({
  type,
  error,
  onRetry,
  isRetrying,
}: {
  type: ReportType
  error?: string | null
  onRetry: () => void
  isRetrying: boolean
}) {
  const { t } = useTranslation()
  const isWeekly = type === 'weekly'

  return (
    <div className="flex items-start gap-3 p-3 rounded-lg bg-destructive/10 text-destructive">
      <AlertCircle className="h-5 w-5 mt-0.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">
          {isWeekly
            ? t('dashboard.weeklyReport.failed')
            : t('dashboard.monthlyReport.failed')}
        </p>
        {error && <p className="text-xs mt-1 opacity-80 truncate">{error}</p>}
        <Button
          variant="outline"
          size="sm"
          className="mt-2"
          onClick={onRetry}
          disabled={isRetrying}
        >
          {isRetrying && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          {isWeekly
            ? t('dashboard.weeklyReport.retry')
            : t('dashboard.monthlyReport.retry')}
        </Button>
      </div>
    </div>
  )
}

function ReportContent({
  type,
  content,
}: {
  type: ReportType
  content: WeeklyReportContent | MonthlyReportContent
}) {
  const { t } = useTranslation()
  const isWeekly = type === 'weekly'
  const prefix = isWeekly ? 'dashboard.weeklyReport' : 'dashboard.monthlyReport'

  return (
    <div className="space-y-4">
      {content.summary && (
        <div>
          <h4 className="text-sm font-medium text-muted-foreground mb-1">
            {t(`${prefix}.summary`)}
          </h4>
          <p className="text-sm">{content.summary}</p>
        </div>
      )}

      {content.suggestions && content.suggestions.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-muted-foreground mb-1">
            {t(`${prefix}.suggestions`)}
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
            {t(`${prefix}.trends`)}
          </h4>
          <p className="text-sm">{content.trends}</p>
        </div>
      )}
    </div>
  )
}
