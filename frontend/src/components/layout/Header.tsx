import { Menu, Moon, Sun, Monitor } from 'lucide-react'
import { useAppStore } from '@/stores/app-store'
import { cn } from '@/lib/utils'

export function Header() {
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)
  const theme = useAppStore((s) => s.theme)
  const setTheme = useAppStore((s) => s.setTheme)

  return (
    <header className="sticky top-0 z-40 flex h-14 items-center justify-between border-b bg-background/95 px-4 backdrop-blur">
      <button
        onClick={toggleSidebar}
        className="rounded-md p-2 text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
        aria-label="Toggle Menu"
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="flex-1" />

      <div className="flex items-center gap-1">
        {(['light', 'dark', 'system'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTheme(t)}
            className={cn(
              "rounded-md p-2 hover:bg-muted",
              theme === t && "bg-muted text-primary"
            )}
            aria-label={`${t} theme`}
          >
            {t === 'light' && <Sun className="h-4 w-4" />}
            {t === 'dark' && <Moon className="h-4 w-4" />}
            {t === 'system' && <Monitor className="h-4 w-4" />}
          </button>
        ))}
      </div>
    </header>
  )
}
