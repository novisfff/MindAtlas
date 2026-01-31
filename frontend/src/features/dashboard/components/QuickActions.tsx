import { Link } from 'react-router-dom'
import { Plus, Network, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTranslation } from 'react-i18next'

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
            'group flex items-center gap-2 px-3 py-2 rounded-lg border',
            'bg-card hover:bg-muted/50 transition-colors'
          )}
        >
          <action.icon className="w-4 h-4" style={{ color: action.color }} />
          <span className="text-sm font-medium">{action.label}</span>
        </Link>
      ))}
    </div>
  )
}
