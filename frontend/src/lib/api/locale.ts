import { useAppStore, type Locale } from '@/stores/app-store'

export function getCurrentLocale(): Locale {
  const locale = useAppStore.getState().locale
  return locale === 'zh' ? 'zh' : 'en'
}

export function withMindAtlasLocale(headers?: HeadersInit): Headers {
  const result = new Headers(headers)
  if (!result.has('X-MindAtlas-Locale')) {
    result.set('X-MindAtlas-Locale', getCurrentLocale())
  }
  return result
}
