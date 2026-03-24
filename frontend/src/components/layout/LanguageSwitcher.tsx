import { useState } from 'react'
import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { updateSystemLocale } from '@/features/settings/api/system-settings'
import { useAppStore } from '@/stores/app-store'
import { cn } from '@/lib/utils'

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const locale = useAppStore((s) => s.locale)
  const setLocale = useAppStore((s) => s.setLocale)
  const [pendingLocale, setPendingLocale] = useState<'zh' | 'en' | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const currentLocale = locale === 'zh' ? 'zh' : 'en'
  const pendingLocaleLabelZh = pendingLocale
    ? t(`layout.languageSwitcher.targetLanguages.${pendingLocale}.zh`)
    : ''
  const pendingLocaleLabelEn = pendingLocale
    ? t(`layout.languageSwitcher.targetLanguages.${pendingLocale}.en`)
    : ''

  const openConfirmDialog = () => {
    setPendingLocale(currentLocale === 'en' ? 'zh' : 'en')
    setConfirmOpen(true)
  }

  const closeConfirmDialog = () => {
    if (isSubmitting) return
    setConfirmOpen(false)
    setPendingLocale(null)
  }

  const confirmLanguageSwitch = async () => {
    if (!pendingLocale) return

    setIsSubmitting(true)
    try {
      await updateSystemLocale(pendingLocale)
      setLocale(pendingLocale, { manual: true })
      await i18n.changeLanguage(pendingLocale)
      setConfirmOpen(false)
      setPendingLocale(null)
    } catch (error) {
      console.error('Failed to persist system locale', error)
      toast.error(t('layout.languageSwitcher.persistError'))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <>
      <button
        onClick={openConfirmDialog}
        disabled={isSubmitting}
        className={cn(
          'flex items-center gap-1.5 rounded-md px-2 py-1.5',
          'text-muted-foreground hover:bg-muted hover:text-foreground',
          'transition-colors disabled:cursor-not-allowed disabled:opacity-60'
        )}
        aria-label={t('layout.languageSwitcher.ariaLabel')}
      >
        <Languages className="h-4 w-4" />
        <span className="text-sm font-medium">
          {currentLocale === 'en' ? 'EN' : '中文'}
        </span>
      </button>

      <ConfirmDialog
        isOpen={confirmOpen}
        title={t('layout.languageSwitcher.confirmTitle')}
        description={t('layout.languageSwitcher.confirmDescription', {
          languageZh: pendingLocaleLabelZh,
          languageEn: pendingLocaleLabelEn,
        })}
        confirmText={t('layout.languageSwitcher.confirmAction', {
          languageZh: pendingLocaleLabelZh,
          languageEn: pendingLocaleLabelEn,
        })}
        cancelText={t('layout.languageSwitcher.cancelAction')}
        onConfirm={() => {
          void confirmLanguageSwitch()
        }}
        onCancel={closeConfirmDialog}
        isLoading={isSubmitting}
      />
    </>
  )
}
