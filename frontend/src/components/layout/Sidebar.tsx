import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  FileText,
  Network,
  Clock,
  Settings,
  ChevronLeft,
  Brain
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

const navItems = [
  { icon: LayoutDashboard, labelKey: 'navigation.dashboard', href: '/dashboard' },
  { icon: FileText, labelKey: 'navigation.entries', href: '/entries' },
  { icon: Network, labelKey: 'navigation.graph', href: '/graph' },
  { icon: Clock, labelKey: 'navigation.timeline', href: '/timeline' },
  { icon: Settings, labelKey: 'navigation.settings', href: '/settings' },
]

export function Sidebar() {
  const { t } = useTranslation()
  const sidebarOpen = useAppStore((s) => s.sidebarOpen)
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex flex-col border-r bg-background transition-[width,transform] duration-300 md:relative",
        sidebarOpen ? "w-64 translate-x-0" : "-translate-x-full md:w-16 md:translate-x-0"
      )}
    >
      {/* Logo */}
      <div className={cn(
        "flex h-16 items-center border-b px-4",
        !sidebarOpen && "md:justify-center md:px-2"
      )}>
        <Brain className="h-6 w-6 text-primary shrink-0" />
        <span className={cn(
          "ml-2 font-bold text-lg whitespace-nowrap overflow-hidden transition-all",
          !sidebarOpen && "md:hidden md:w-0"
        )}>
          MindAtlas
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2">
        {navItems.map((item) => (
          <NavLink
            key={item.href}
            to={item.href}
            aria-label={t(item.labelKey)}
            className={({ isActive }) => cn(
              "flex items-center rounded-lg px-3 py-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
              isActive && "bg-primary/10 text-primary",
              !sidebarOpen && "md:justify-center md:px-2"
            )}
            title={!sidebarOpen ? t(item.labelKey) : undefined}
          >
            <item.icon className="h-5 w-5 shrink-0" />
            <span className={cn(
              "ml-3 whitespace-nowrap",
              !sidebarOpen && "md:hidden"
            )}>
              {t(item.labelKey)}
            </span>
          </NavLink>
        ))}
      </nav>

      {/* Collapse Button */}
      <div className="border-t p-2">
        <button
          onClick={toggleSidebar}
          aria-label={sidebarOpen ? t('actions.collapse') : t('actions.expand')}
          className={cn(
            "flex w-full items-center rounded-lg px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            "text-muted-foreground hover:bg-muted hover:text-foreground",
            !sidebarOpen && "md:justify-center md:px-2"
          )}
        >
          <ChevronLeft className={cn(
            "h-5 w-5 transition-transform",
            !sidebarOpen && "md:rotate-180"
          )} />
          <span className={cn("ml-3", !sidebarOpen && "md:hidden")}>
            {t('actions.collapse')}
          </span>
        </button>
      </div>
    </aside>
  )
}
