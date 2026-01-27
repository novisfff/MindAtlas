import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type Locale = 'en' | 'zh'

interface AppState {
  theme: 'light' | 'dark' | 'system'
  sidebarOpen: boolean
  locale: Locale
  localeManuallySet: boolean
  setTheme: (theme: 'light' | 'dark' | 'system') => void
  toggleSidebar: () => void
  setLocale: (locale: Locale) => void
}

function getBrowserLocale(): Locale {
  if (typeof window === 'undefined') return 'en'
  const navLang = window.navigator?.language?.toLowerCase() ?? ''
  return navLang.startsWith('zh') ? 'zh' : 'en'
}

function getInitialLocale(): Locale {
  if (typeof window === 'undefined') return 'en'
  return getBrowserLocale()
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      theme: 'system',
      sidebarOpen: true,
      locale: getInitialLocale(),
      localeManuallySet: false,
      setTheme: (theme) => set({ theme }),
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setLocale: (locale) => set({ locale, localeManuallySet: true }),
    }),
    {
      name: 'mindatlas-store',
      version: 2,
      migrate: (persistedState, persistedVersion) => {
        const raw = (persistedState ?? {}) as Partial<AppState> & { state?: Partial<AppState> }

        // v1 bug: our migrate returned a wrapper, which got persisted as `state`.
        const prevState = raw.state ? raw.state : raw

        const prevLocale = prevState.locale
        const prevLocaleManuallySet = prevState.localeManuallySet

        const isManual =
          prevLocaleManuallySet === true ||
          // v0 had no localeManuallySet; treat zh as manually selected
          (persistedVersion === 0 && prevLocale === 'zh')

        const migratedLocale: Locale =
          isManual && (prevLocale === 'en' || prevLocale === 'zh')
            ? prevLocale
            : getBrowserLocale()

        return {
          theme: prevState.theme ?? 'system',
          sidebarOpen: prevState.sidebarOpen ?? true,
          locale: migratedLocale,
          localeManuallySet: isManual,
          // functions are provided by the store initializer; persist will merge them back in
        } as unknown as AppState
      },
    }
  )
)
