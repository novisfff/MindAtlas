import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { SkillManager } from '../components/SkillManager'

export function SkillSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <div className="max-w-5xl mx-auto py-8 px-6 space-y-8">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate('/settings')}
              className="p-1.5 -ml-2 rounded-lg text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
            <h1 className="text-3xl font-bold tracking-tight text-foreground">{t('pages.settings.assistantSkills')}</h1>
          </div>
          <p className="text-muted-foreground max-w-2xl text-base">{t('pages.settings.assistantSkillsDesc')}</p>
        </div>
      </div>

      <div className="bg-card/50 rounded-xl border p-6 shadow-sm">
        <SkillManager />
      </div>
    </div>
  )
}
