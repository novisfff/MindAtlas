import { Sparkles, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

interface AiAssistButtonProps {
  onClick: () => void
  isLoading?: boolean
  disabled?: boolean
}

export function AiAssistButton({ onClick, isLoading, disabled }: AiAssistButtonProps) {
  const { t } = useTranslation()
  const aiAssistLabel = t('entry.form.aiAssist')

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || isLoading}
      aria-label={aiAssistLabel}
      className={cn(
        'inline-flex items-center gap-1.5 px-3 py-1.5 text-sm',
        'rounded-lg border border-purple-300 bg-purple-50',
        'text-purple-700 hover:bg-purple-100',
        'dark:border-purple-700 dark:bg-purple-950 dark:text-purple-300',
        'transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
      )}
    >
      {isLoading ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <Sparkles className="w-4 h-4" />
      )}
      {isLoading ? t('entry.form.generating') : aiAssistLabel}
    </button>
  )
}
