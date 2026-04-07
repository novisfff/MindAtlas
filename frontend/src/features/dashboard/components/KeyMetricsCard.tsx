import { useTranslation } from 'react-i18next'
import { Loader2, FileText, Calendar, Database, GitBranch } from 'lucide-react'
import { useWeeklyMetricsQuery } from '../queries'
import { cn } from '@/lib/utils'
import { uiChrome, uiRadius } from '@/components/ui/styles'

export function KeyMetricsCard() {
  const { t } = useTranslation()
  const { data, isLoading } = useWeeklyMetricsQuery()

  if (isLoading) {
    return (
      <div className={cn(uiChrome.card, 'p-4')}>
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
    <div className={cn(uiChrome.card, 'h-full overflow-hidden p-4')}>
      <div className="grid h-full min-h-0 grid-cols-2 grid-rows-2 gap-3.5">
        {metrics.map((m) => (
          <div
            key={m.label}
            className={cn(
              uiRadius.control,
              'flex min-h-0 flex-col items-center justify-center overflow-hidden p-3 text-center transition-colors hover:bg-background/90 sm:p-4',
            )}
          >
            <div
              className={cn(
                'mb-3 p-2.5 ring-1 ring-border/60',
                uiRadius.pill,
                m.color,
              )}
              style={{ backgroundColor: 'hsl(var(--background) / 0.86)' }}
            >
              <m.icon className="w-5 h-5" />
            </div>
            <p className="text-2xl font-bold tracking-tight">{m.value}</p>
            <p className="text-xs font-medium leading-5 text-muted-foreground">{m.label}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
