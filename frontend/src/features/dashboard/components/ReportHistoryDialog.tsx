import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { Loader2, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useWeeklyReportListQuery, useMonthlyReportListQuery } from '../queries'
import type { WeeklyReport, MonthlyReport } from '../api/reports'
import { cn } from '@/lib/utils'

type ReportType = 'weekly' | 'monthly'
type Report = WeeklyReport | MonthlyReport

interface ReportHistoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  type?: ReportType
}

export function ReportHistoryDialog({ open, onOpenChange, type = 'weekly' }: ReportHistoryDialogProps) {
  const { t, i18n } = useTranslation()
  const [page, setPage] = useState(0)
  const [selectedReport, setSelectedReport] = useState<Report | null>(null)

  const weeklyQuery = useWeeklyReportListQuery(page, 10)
  const monthlyQuery = useMonthlyReportListQuery(page, 10)

  // Only use the query for the active type to avoid unnecessary fetching
  const { data, isLoading } = type === 'weekly'
    ? { data: weeklyQuery.data, isLoading: weeklyQuery.isLoading }
    : { data: monthlyQuery.data, isLoading: monthlyQuery.isLoading }
  const locale = i18n.language === 'zh' ? zhCN : enUS

  const formatDateRange = (report: Report) => {
    if ('weekStart' in report) {
      const startDate = new Date(report.weekStart)
      const endDate = new Date(report.weekEnd)
      return `${format(startDate, 'yyyy/MM/dd', { locale })} - ${format(endDate, 'MM/dd', { locale })}`
    } else {
      const startDate = new Date(report.monthStart)
      const endDate = new Date(report.monthEnd)
      return `${format(startDate, 'yyyy/MM/dd', { locale })} - ${format(endDate, 'MM/dd', { locale })}`
    }
  }

  const totalPages = data ? Math.ceil(data.total / data.size) : 0
  const titleKey = type === 'weekly' ? 'dashboard.weeklyReport.history' : 'dashboard.monthlyReport.history'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh]">
        <DialogHeader>
          <DialogTitle>{t(titleKey)}</DialogTitle>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center h-48">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : selectedReport ? (
          <ReportDetail
            report={selectedReport}
            type={type}
            onBack={() => setSelectedReport(null)}
            locale={locale}
          />
        ) : (
          <ReportList
            reports={data?.items ?? []}
            type={type}
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
            onSelect={setSelectedReport}
            formatDateRange={formatDateRange}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function ReportList({
  reports,
  type,
  page,
  totalPages,
  onPageChange,
  onSelect,
  formatDateRange,
}: {
  reports: Report[]
  type: ReportType
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  onSelect: (report: Report) => void
  formatDateRange: (report: Report) => string
}) {
  const { t } = useTranslation()
  const noHistoryKey = type === 'weekly' ? 'dashboard.weeklyReport.noHistory' : 'dashboard.monthlyReport.noHistory'
  const entryCountKey = type === 'weekly' ? 'dashboard.weeklyReport.entryCount' : 'dashboard.monthlyReport.entryCount'

  if (reports.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        {t(noHistoryKey)}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <ScrollArea className="h-[400px]">
        <div className="space-y-2 pr-4">
          {reports.map((report) => (
            <button
              key={report.id}
              className={cn(
                'w-full text-left p-3 rounded-lg border hover:bg-muted/50 transition-colors',
                report.status === 'completed' && 'border-green-500/30',
                report.status === 'failed' && 'border-destructive/30'
              )}
              onClick={() => onSelect(report)}
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">
                  {formatDateRange(report)}
                </span>
                <StatusBadge status={report.status} type={type} />
              </div>
              <p className="text-sm text-muted-foreground mt-1">
                {t(entryCountKey, { count: report.entryCount })}
              </p>
            </button>
          ))}
        </div>
      </ScrollArea>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            disabled={page === 0}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm text-muted-foreground">
            {page + 1} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8"
            disabled={page >= totalPages - 1}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status, type }: { status: string; type: ReportType }) {
  const { t } = useTranslation()
  const prefix = type === 'weekly' ? 'dashboard.weeklyReport' : 'dashboard.monthlyReport'

  const statusConfig: Record<string, { label: string; className: string }> = {
    completed: {
      label: t(`${prefix}.status.completed`),
      className: 'bg-green-500/10 text-green-600',
    },
    generating: {
      label: t(`${prefix}.status.generating`),
      className: 'bg-blue-500/10 text-blue-600',
    },
    failed: {
      label: t(`${prefix}.status.failed`),
      className: 'bg-destructive/10 text-destructive',
    },
    pending: {
      label: t(`${prefix}.status.pending`),
      className: 'bg-muted text-muted-foreground',
    },
  }

  const config = statusConfig[status] ?? statusConfig.pending

  return (
    <span className={cn('text-xs px-2 py-0.5 rounded-full', config.className)}>
      {config.label}
    </span>
  )
}

function ReportDetail({
  report,
  type,
  onBack,
  locale,
}: {
  report: Report
  type: ReportType
  onBack: () => void
  locale: typeof zhCN | typeof enUS
}) {
  const { t } = useTranslation()
  const prefix = type === 'weekly' ? 'dashboard.weeklyReport' : 'dashboard.monthlyReport'

  const formatDate = (dateStr: string) => {
    return format(new Date(dateStr), 'yyyy/MM/dd', { locale })
  }

  const getDateRange = () => {
    if ('weekStart' in report) {
      return `${formatDate(report.weekStart)} - ${formatDate(report.weekEnd)}`
    } else {
      return `${formatDate(report.monthStart)} - ${formatDate(report.monthEnd)}`
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ChevronLeft className="h-4 w-4 mr-1" />
          {t('common.back')}
        </Button>
      </div>

      <div className="flex items-center justify-between">
        <span className="font-medium">{getDateRange()}</span>
        <StatusBadge status={report.status} type={type} />
      </div>

      <ScrollArea className="h-[350px]">
        <div className="space-y-4 pr-4">
          {report.content?.summary && (
            <Section
              title={t(`${prefix}.summary`)}
              content={report.content.summary}
            />
          )}

          {report.content?.suggestions && report.content.suggestions.length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-muted-foreground mb-2">
                {t(`${prefix}.suggestions`)}
              </h4>
              <ul className="text-sm space-y-1">
                {report.content.suggestions.map((s, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-primary">•</span>
                    <span>{s}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.content?.trends && (
            <Section
              title={t(`${prefix}.trends`)}
              content={report.content.trends}
            />
          )}

          {report.lastError && (
            <Section
              title={t(`${prefix}.error`)}
              content={report.lastError}
              className="text-destructive"
            />
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

function Section({
  title,
  content,
  className,
}: {
  title: string
  content: string
  className?: string
}) {
  return (
    <div>
      <h4 className="text-sm font-medium text-muted-foreground mb-1">{title}</h4>
      <p className={cn('text-sm', className)}>{content}</p>
    </div>
  )
}
