import { Outlet, useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { ThemeProvider } from './ThemeProvider'
import { useAppStore } from '@/stores/app-store'
import { FloatingWidget } from '@/features/assistant'
import { cn } from '@/lib/utils'

export function AppLayout() {
  const location = useLocation()
  const sidebarOpen = useAppStore((s) => s.sidebarOpen)
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)

  return (
    <ThemeProvider>
      <div className="flex h-screen overflow-hidden bg-background">
        {/* Mobile Overlay */}
        {sidebarOpen && (
          <button
            type="button"
            className="fixed inset-0 z-40 bg-black/50 md:hidden"
            onClick={toggleSidebar}
            aria-label="Close sidebar"
          />
        )}

        <Sidebar />

        <div className="flex flex-1 flex-col overflow-hidden">
          <Header />
          <main className="flex-1 overflow-y-auto p-4 md:p-6">
            <Outlet />
          </main>
        </div>
        {!location.pathname.startsWith('/assistant') && <FloatingWidget />}
      </div>
    </ThemeProvider>
  )
}
