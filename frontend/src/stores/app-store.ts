import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type Locale = 'en' | 'zh'

interface AppState {
  theme: 'light' | 'dark' | 'system'
  sidebarOpen: boolean
  locale: Locale
  setTheme: (theme: 'light' | 'dark' | 'system') => void
  toggleSidebar: () => void
  setLocale: (locale: Locale) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      theme: 'system',
      sidebarOpen: true,
      locale: 'en',
      setTheme: (theme) => set({ theme }),
      toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setLocale: (locale) => set({ locale }),
    }),
    { name: 'mindatlas-store' }
  )
)
