import { useNavigate } from 'react-router-dom'
import { FileType, Tags, ChevronRight, Bot, Wrench, BrainCircuit } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function SettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const categories = [
    {
      id: 'entry-types',
      titleKey: 'pages.settings.entryTypes',
      descKey: 'pages.settings.entryTypesDesc',
      icon: FileType,
      path: '/settings/entry-types',
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10'
    },
    {
      id: 'tags',
      titleKey: 'pages.settings.tags',
      descKey: 'pages.settings.tagsDesc',
      icon: Tags,
      path: '/settings/tags',
      color: 'text-green-500',
      bgColor: 'bg-green-500/10'
    },
    {
      id: 'ai-providers',
      titleKey: 'pages.settings.aiProviders',
      descKey: 'pages.settings.aiProvidersDesc',
      icon: Bot,
      path: '/settings/ai-providers',
      color: 'text-violet-600',
      bgColor: 'bg-violet-600/10'
    },
    {
      id: 'assistant-tools',
      titleKey: 'pages.settings.assistantTools',
      descKey: 'pages.settings.assistantToolsDesc',
      icon: Wrench,
      path: '/settings/assistant-tools',
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10'
    },
    {
      id: 'assistant-skills',
      titleKey: 'pages.settings.assistantSkills',
      descKey: 'pages.settings.assistantSkillsDesc',
      icon: BrainCircuit,
      path: '/settings/assistant-skills',
      color: 'text-purple-500',
      bgColor: 'bg-purple-500/10'
    }
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">{t('pages.settings.title')}</h1>
        <p className="text-muted-foreground">{t('pages.settings.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {categories.map((category) => (
          <button
            key={category.id}
            onClick={() => navigate(category.path)}
            className="group flex items-start gap-4 p-4 rounded-xl border bg-card hover:bg-accent/50 transition-all text-left"
          >
            <div className={`p-3 rounded-lg ${category.bgColor} ${category.color}`}>
              <category.icon className="w-6 h-6" />
            </div>

            <div className="flex-1">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-lg">{t(category.titleKey)}</h3>
                <ChevronRight className="w-5 h-5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
              <p className="text-sm text-muted-foreground mt-1">
                {t(category.descKey)}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
