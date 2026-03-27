import { useState, useEffect, ReactNode } from 'react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { getSystemLocale, updateSystemLocale } from '@/features/settings/api/system-settings'
import { fetchInitializationStatus, initializationKeys } from '@/features/initialization/queries'
import { useAppStore } from '@/stores/app-store'
import { Toaster } from 'sonner'

function SystemLocaleBootstrap() {
  const { i18n } = useTranslation()
  const setLocale = useAppStore((s) => s.setLocale)
  const queryClient = useQueryClient()

  useEffect(() => {
    let cancelled = false

    const sync = async () => {
      try {
        const currentLocale = useAppStore.getState().locale === 'zh' ? 'zh' : 'en'
        const initialization = await queryClient.fetchQuery({
          queryKey: initializationKeys.status,
          queryFn: fetchInitializationStatus,
          staleTime: 0,
        })
        if (cancelled) return

        if (!initialization.initialized) {
          // During initialization, the wizard owns the temporary locale choice.
          // Do not snap the UI back to the backend fallback locale on refresh.
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
  }, [i18n, queryClient, setLocale])

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
