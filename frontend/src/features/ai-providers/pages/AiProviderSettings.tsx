import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { ProviderManager } from '../components/ProviderManager'
import { useTranslation } from 'react-i18next'

export function AiProviderSettings() {
  const navigate = useNavigate()
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/settings')}
          className="inline-flex items-center justify-center w-8 h-8 rounded-full hover:bg-muted transition-colors"
        >
          <ArrowLeft className="w-5 h-5 text-muted-foreground" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">{t('pages.aiProviders.title')}</h1>
          <p className="text-muted-foreground">
            {t('pages.aiProviders.description')}
          </p>
        </div>
      </div>

      <div className="rounded-xl border bg-card p-6">
        <ProviderManager />
      </div>
    </div>
  )
}
