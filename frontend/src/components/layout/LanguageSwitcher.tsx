import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '@/stores/app-store'
import { cn } from '@/lib/utils'

export function LanguageSwitcher() {
  const { i18n } = useTranslation()
  const locale = useAppStore((s) => s.locale)
  const setLocale = useAppStore((s) => s.setLocale)

  const toggleLanguage = () => {
    const newLocale = locale === 'en' ? 'zh' : 'en'
    setLocale(newLocale)
    i18n.changeLanguage(newLocale)
  }

  return (
    <button
      onClick={toggleLanguage}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2 py-1.5',
        'text-muted-foreground hover:bg-muted hover:text-foreground',
        'transition-colors'
      )}
      aria-label="Switch Language"
    >
      <Languages className="h-4 w-4" />
      <span className="text-sm font-medium">
        {locale === 'en' ? 'EN' : '中文'}
      </span>
    </button>
  )
}
