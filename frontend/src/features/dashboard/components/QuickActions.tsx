import { Link } from 'react-router-dom'
import { Plus, Network, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

const actions = [
  {
    label: 'New Entry',
    href: '/entries/new',
    icon: Plus,
    color: '#3B82F6',
  },
  {
    label: 'View Graph',
    href: '/graph',
    icon: Network,
    color: '#10B981',
  },
  {
    label: 'Search',
    href: '/entries',
    icon: Search,
    color: '#8B5CF6',
  },
]

export function QuickActions() {
  return (
    <div className="rounded-xl border bg-card p-6 shadow-sm">
      <h3 className="font-semibold mb-4">Quick Actions</h3>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {actions.map((action) => (
          <Link
            key={action.href}
            to={action.href}
            className={cn(
              'group flex flex-col items-center justify-center gap-3 p-4 rounded-xl border border-transparent',
              'bg-muted/30 hover:bg-card hover:border-border hover:shadow-sm transition-all duration-300'
            )}
          >
            <div
              className="flex h-12 w-12 items-center justify-center rounded-xl transition-transform duration-300 group-hover:scale-110"
              style={{ backgroundColor: `${action.color}15` }}
            >
              <action.icon className="w-6 h-6" style={{ color: action.color }} />
            </div>
            <span className="text-sm font-medium">{action.label}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}
