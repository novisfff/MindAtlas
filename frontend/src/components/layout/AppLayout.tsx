import { lazy, Suspense } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { ThemeProvider } from './ThemeProvider'
import { useAppStore } from '@/stores/app-store'
import { cn } from '@/lib/utils'
import { uiSurface } from '@/components/ui/styles'

const FloatingWidget = lazy(async () => {
  const mod = await import('@/features/assistant/components/FloatingWidget')
  return { default: mod.FloatingWidget }
})

export function AppLayout() {
  const location = useLocation()
  const sidebarOpen = useAppStore((s) => s.sidebarOpen)
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)
  const isAssistantRoute = location.pathname.startsWith('/assistant')
  const isCalendarRoute = location.pathname.startsWith('/calendar')
  const isFullBleedRoute = isAssistantRoute || isCalendarRoute

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
          <main
            className={cn(
              'flex-1 min-h-0',
              isFullBleedRoute
                ? 'overflow-hidden'
                : cn(uiSurface.pageBackdrop, 'overflow-y-auto px-4 py-4 md:px-6 md:py-6'),
            )}
          >
            <Outlet />
          </main>
        </div>
        {!isAssistantRoute && (
          <Suspense fallback={null}>
            <FloatingWidget />
          </Suspense>
        )}
      </div>
    </ThemeProvider>
  )
}
