import { Loader2, FileText, Tags, GitFork } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useDashboardStatsQuery } from './queries'
import { useEntriesQuery } from '@/features/entries/queries'
import { StatsCard } from './components/StatsCard'
import { RecentEntries } from './components/RecentEntries'
import { TypeDistribution } from './components/TypeDistribution'
import { QuickActions } from './components/QuickActions'

export function DashboardPage() {
  const { t } = useTranslation()
  const { data: stats, isLoading: statsLoading } = useDashboardStatsQuery()
  const { data: entriesPage, isLoading: entriesLoading } = useEntriesQuery({ size: 5 })

  const isLoading = statsLoading || entriesLoading

  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return t('greetings.morning')
    if (hour < 18) return t('greetings.afternoon')
    return t('greetings.evening')
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-4rem)]">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    )
  }

  const recentEntries = entriesPage?.content ?? []

  return (
    <div className="max-w-7xl mx-auto space-y-8 p-1">
      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight bg-gradient-to-r from-foreground to-foreground/60 bg-clip-text text-transparent">
          {t('greetings.welcome', { greeting: getGreeting() })}
        </h1>
        <p className="text-muted-foreground">
          {t('pages.dashboard.subtitle')}
        </p>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <StatsCard
          title={t('pages.dashboard.totalEntries')}
          value={stats?.totalEntries ?? 0}
          icon={FileText}
          color="#3B82F6"
          description={t('pages.dashboard.allTimeEntries')}
        />
        <StatsCard
          title={t('pages.dashboard.tagsUsed')}
          value={stats?.totalTags ?? 0}
          icon={Tags}
          color="#10B981"
          description={t('pages.dashboard.categoriesDefined')}
        />
        <StatsCard
          title={t('pages.dashboard.activeRelations')}
          value={stats?.totalRelations ?? 0}
          icon={GitFork}
          color="#8B5CF6"
          description={t('pages.dashboard.connectionsMade')}
        />
      </div>

      {/* Main Content */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 min-h-[400px]">
          <RecentEntries entries={recentEntries} />
        </div>
        <div className="space-y-8">
          <QuickActions />
          <div className="min-h-[300px]">
            <TypeDistribution data={stats?.entriesByType ?? []} />
          </div>
        </div>
      </div>
    </div>
  )
}
