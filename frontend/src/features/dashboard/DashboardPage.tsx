import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useEntriesQuery } from '@/features/entries/queries'
import { RecentEntries } from './components/RecentEntries'
import { QuickActions } from './components/QuickActions'
import { AIReportsContainer } from './components/AIReportsContainer'
import { KeyMetricsCard } from './components/KeyMetricsCard'
import { MiniCalendar } from './components/MiniCalendar'
import { TypeTagHotness } from './components/TypeTagHotness'
import { uiLayout } from '@/components/ui/styles'

export function DashboardPage() {
  const { t } = useTranslation()
  const { data: entriesPage, isLoading: entriesLoading } = useEntriesQuery({ size: 5 })

  const getGreeting = () => {
    const hour = new Date().getHours()
    if (hour < 12) return t('greetings.morning')
    if (hour < 18) return t('greetings.afternoon')
    return t('greetings.evening')
  }

  if (entriesLoading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-4rem)]">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    )
  }

  const recentEntries = entriesPage?.content ?? []

  return (
    <div className={uiLayout.page7}>
      {/* Header with Greeting and Quick Actions */}
      <div className={uiLayout.headerRow}>
        <div className={uiLayout.headerBlock}>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">
            {t('greetings.welcome', { greeting: getGreeting() })}
          </h1>
          <p className="max-w-2xl text-sm leading-7 text-muted-foreground md:text-base">
            {t('pages.dashboard.subtitle')}
          </p>
        </div>
        <QuickActions />
      </div>

      {/* Row 1: Key Metrics | Recent Entries | Mini Calendar */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_272px] lg:auto-rows-[280px] gap-4">
        <KeyMetricsCard />
        <RecentEntries entries={recentEntries} />
        <MiniCalendar />
      </div>

      {/* Row 2: AI Reports | Type/Tag Hotness */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3">
          <AIReportsContainer />
        </div>
        <div className="lg:col-span-2">
          <TypeTagHotness />
        </div>
      </div>
    </div>
  )
}
