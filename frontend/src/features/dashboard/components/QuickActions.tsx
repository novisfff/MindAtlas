import { Link } from 'react-router-dom'
import { Plus, Network, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'
import { uiChrome } from '@/components/ui/styles'

export function QuickActions() {
  const { t } = useTranslation()

  const actions = [
    {
      label: t('dashboard.quickActions.newEntry'),
      href: '/entries/new',
      icon: Plus,
      color: '#3B82F6',
    },
    {
      label: t('dashboard.quickActions.viewGraph'),
      href: '/graph',
      icon: Network,
      color: '#10B981',
    },
    {
      label: t('dashboard.quickActions.search'),
      href: '/entries',
      icon: Search,
      color: '#8B5CF6',
    },
  ]

  return (
    <div className="flex items-center gap-2">
      {actions.map((action) => (
        <Link
          key={action.href}
          to={action.href}
          className={cn(
            uiChrome.control,
            'group flex items-center gap-2 px-3 py-2 transition-colors hover:bg-muted/55'
          )}
        >
          <action.icon className="w-4 h-4" style={{ color: action.color }} />
          <span className="text-sm font-medium">{action.label}</span>
        </Link>
      ))}
    </div>
  )
}
