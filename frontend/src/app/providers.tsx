import { useState, useEffect, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getSystemLocale, updateSystemLocale } from '@/features/settings/api/system-settings'
import { getInitializationStatus } from '@/features/initialization/api/systemInitialization'
import { useAppStore } from '@/stores/app-store'
import { Toaster } from 'sonner'

function SystemLocaleBootstrap() {
  const { i18n } = useTranslation()
  const setLocale = useAppStore((s) => s.setLocale)

  useEffect(() => {
    let cancelled = false

    const sync = async () => {
      try {
        const currentLocale = useAppStore.getState().locale === 'zh' ? 'zh' : 'en'
        const initialization = await getInitializationStatus()
        if (cancelled) return

        if (!initialization.initialized) {
          if (initialization.locale !== useAppStore.getState().locale) {
            setLocale(initialization.locale, { manual: false })
          }
          if (i18n.language !== initialization.locale) {
            await i18n.changeLanguage(initialization.locale)
          }
          return
        }

        const response = await getSystemLocale()
        if (cancelled) return

        if (response.persisted) {
          if (response.locale !== useAppStore.getState().locale) {
            setLocale(response.locale, { manual: true })
          }
          if (i18n.language !== response.locale) {
            await i18n.changeLanguage(response.locale)
          }
          return
        }

        await updateSystemLocale(currentLocale)
        if (cancelled) return
        setLocale(currentLocale, { manual: true })
        if (i18n.language !== currentLocale) {
          await i18n.changeLanguage(currentLocale)
        }
      } catch (error) {
        console.error('Failed to synchronize system locale', error)
      }
    }

    void sync()
    return () => {
      cancelled = true
    }
  }, [i18n, setLocale])

  return null
}

function LanguageSync() {
  const { i18n } = useTranslation()
  const locale = useAppStore((s) => s.locale)

  useEffect(() => {
    if (locale !== 'en' && locale !== 'zh') return
    if (i18n.language !== locale) {
      i18n.changeLanguage(locale)
    }
  }, [locale, i18n])

  return null
}

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60 * 1000,
        refetchOnWindowFocus: false,
      },
    },
  }))

  return (
    <QueryClientProvider client={queryClient}>
      <SystemLocaleBootstrap />
      <LanguageSync />
      <Toaster />
      {children}
    </QueryClientProvider>
  )
}
