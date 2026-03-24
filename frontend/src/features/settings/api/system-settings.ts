import { apiClient } from '@/lib/api/client'
import type { Locale } from '@/stores/app-store'

export interface SystemLocaleResponse {
  locale: Locale
  persisted: boolean
}

export function getSystemLocale() {
  return apiClient.get<SystemLocaleResponse>('/api/system-settings/locale')
}

export function updateSystemLocale(locale: Locale) {
  return apiClient.put<SystemLocaleResponse>('/api/system-settings/locale', {
    body: { locale },
  })
}
