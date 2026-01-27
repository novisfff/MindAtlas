import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import enCommon from '../locales/en/common.json'
import zhCommon from '../locales/zh/common.json'

const resources = {
  en: { common: enCommon },
  zh: { common: zhCommon },
}

function getBrowserLanguage(): 'en' | 'zh' {
  if (typeof window === 'undefined') return 'en'
  const navLang = window.navigator?.language?.toLowerCase() ?? ''
  return navLang.startsWith('zh') ? 'zh' : 'en'
}

function getInitialLanguage(): 'en' | 'zh' {
  if (typeof window === 'undefined') return 'en'

  try {
    const raw = window.localStorage.getItem('mindatlas-store')
    if (raw) {
      const parsed = JSON.parse(raw) as {
        state?: { locale?: unknown; localeManuallySet?: unknown }
      }
      const locale = parsed?.state?.locale
      const localeManuallySet = parsed?.state?.localeManuallySet

      // v0 didn't have localeManuallySet; if locale is 'zh', it must have been user-selected.
      const isManual = localeManuallySet === true || locale === 'zh'
      if (isManual && (locale === 'en' || locale === 'zh')) return locale
    }
  } catch {
    // ignore
  }

  return getBrowserLanguage()
}

i18n.use(initReactI18next).init({
  resources,
  lng: getInitialLanguage(),
  fallbackLng: 'en',
  ns: ['common'],
  defaultNS: 'common',
  interpolation: {
    escapeValue: false,
  },
})

export default i18n
