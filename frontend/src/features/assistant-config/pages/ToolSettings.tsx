import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { ToolManager } from '../components/ToolManager'

export function ToolSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/settings')}
          className="p-2 rounded-lg hover:bg-muted"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">{t('pages.settings.assistantTools')}</h1>
          <p className="text-muted-foreground">{t('pages.settings.assistantToolsDesc')}</p>
        </div>
      </div>

      <div className="bg-card rounded-xl border p-6">
        <ToolManager />
      </div>
    </div>
  )
}
