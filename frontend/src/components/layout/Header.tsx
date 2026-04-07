import { Menu, Moon, Sun, Monitor } from 'lucide-react'
import { useAppStore } from '@/stores/app-store'
import { cn } from '@/lib/utils'
import { LanguageSwitcher } from './LanguageSwitcher'
import { uiChrome, uiRadius, uiSurface } from '@/components/ui/styles'

export function Header() {
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)
  const theme = useAppStore((s) => s.theme)
  const setTheme = useAppStore((s) => s.setTheme)

  return (
    <header
      className={cn(
        'sticky top-0 z-40 flex h-16 items-center justify-between px-4 md:px-6',
        uiSurface.headerGlass,
      )}
    >
      <button
        onClick={toggleSidebar}
        className={cn(
          'p-2.5 text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground md:hidden',
          uiRadius.control,
        )}
        aria-label="Toggle Menu"
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="flex-1" />

      <div className="flex items-center gap-2">
        <LanguageSwitcher />
        <div className={cn('inline-flex items-center gap-1 p-1', uiChrome.control)}>
          {(['light', 'dark', 'system'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTheme(t)}
              className={cn(
                'p-2 transition-colors',
                uiRadius.control,
                theme === t
                  ? 'bg-background text-foreground shadow-sm ring-1 ring-border/60'
                  : 'text-muted-foreground hover:bg-muted/55 hover:text-foreground',
              )}
              aria-label={`${t} theme`}
            >
              {t === 'light' && <Sun className="h-4 w-4" />}
              {t === 'dark' && <Moon className="h-4 w-4" />}
              {t === 'system' && <Monitor className="h-4 w-4" />}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}
