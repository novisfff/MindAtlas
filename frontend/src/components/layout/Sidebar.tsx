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
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'

const navItems = [
  { icon: LayoutDashboard, label: 'Dashboard', href: '/dashboard' },
  { icon: FileText, label: 'Entries', href: '/entries' },
  { icon: Network, label: 'Graph', href: '/graph' },
  { icon: Clock, label: 'Timeline', href: '/timeline' },
  { icon: Settings, label: 'Settings', href: '/settings' },
]

export function Sidebar() {
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
            aria-label={item.label}
            className={({ isActive }) => cn(
              "flex items-center rounded-lg px-3 py-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
              "text-muted-foreground hover:bg-muted hover:text-foreground",
              isActive && "bg-primary/10 text-primary",
              !sidebarOpen && "md:justify-center md:px-2"
            )}
            title={!sidebarOpen ? item.label : undefined}
          >
            <item.icon className="h-5 w-5 shrink-0" />
            <span className={cn(
              "ml-3 whitespace-nowrap",
              !sidebarOpen && "md:hidden"
            )}>
              {item.label}
            </span>
          </NavLink>
        ))}
      </nav>

      {/* Collapse Button */}
      <div className="border-t p-2">
        <button
          onClick={toggleSidebar}
          aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
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
            Collapse
          </span>
        </button>
      </div>
    </aside>
  )
}
