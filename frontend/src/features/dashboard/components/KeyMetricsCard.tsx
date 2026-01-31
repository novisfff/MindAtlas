import { useTranslation } from 'react-i18next'
import { Loader2, FileText, Calendar, Database, GitBranch } from 'lucide-react'
import { useWeeklyMetricsQuery } from '../queries'

export function KeyMetricsCard() {
  const { t } = useTranslation()
  const { data, isLoading } = useWeeklyMetricsQuery()

  if (isLoading) {
    return (
      <div className="rounded-xl border bg-card p-4 shadow-sm">
        <div className="flex items-center justify-center h-24">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  const metrics = [
    {
      label: t('dashboard.metrics.weekEntries'),
      value: data?.weekEntryCount ?? 0,
      icon: FileText,
      color: 'text-blue-500',
    },
    {
      label: t('dashboard.metrics.activeDays'),
      value: data?.activeDays ?? 0,
      icon: Calendar,
      color: 'text-green-500',
    },
    {
      label: t('dashboard.metrics.totalEntries'),
      value: data?.totalEntries ?? 0,
      icon: Database,
      color: 'text-purple-500',
    },
    {
      label: t('dashboard.metrics.totalRelations'),
      value: data?.totalRelations ?? 0,
      icon: GitBranch,
      color: 'text-orange-500',
    },
  ]

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm h-full">
      <div className="grid grid-cols-2 gap-4">
        {metrics.map((m) => (
          <div key={m.label} className="flex flex-col items-center justify-center p-2 rounded-lg hover:bg-muted/50 transition-colors">
            <div className={`p-2 rounded-full bg-background mb-2 ring-1 ring-border ${m.color}`}>
              <m.icon className="w-5 h-5" />
            </div>
            <p className="text-2xl font-bold tracking-tight">{m.value}</p>
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">{m.label}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
