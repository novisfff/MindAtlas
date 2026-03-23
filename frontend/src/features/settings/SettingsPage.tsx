import { useNavigate } from 'react-router-dom'
import { FileType, Tags, ChevronRight, Bot, Wrench, BrainCircuit, Network, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function SettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const contentCategories = [
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
    }
  ]

  const aiCategories = [
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
    },
    {
      id: 'assistant-targets',
      titleKey: 'pages.settings.assistantTargets',
      descKey: 'pages.settings.assistantTargetsDesc',
      icon: Network,
      path: '/settings/assistant-targets',
      color: 'text-cyan-500',
      bgColor: 'bg-cyan-500/10'
    },
    {
      id: 'system-ai-behaviors',
      titleKey: 'pages.settings.systemAiBehaviors',
      descKey: 'pages.settings.systemAiBehaviorsDesc',
      icon: Sparkles,
      path: '/settings/system-ai-behaviors',
      color: 'text-amber-500',
      bgColor: 'bg-amber-500/10'
    }
  ]

  const sections = [
    {
      id: 'content',
      titleKey: 'pages.settings.contentSection',
      descKey: 'pages.settings.contentSectionDesc',
      gridClassName: 'grid-cols-1 md:grid-cols-2',
      categories: contentCategories
    },
    {
      id: 'ai',
      titleKey: 'pages.settings.aiSection',
      descKey: 'pages.settings.aiSectionDesc',
      gridClassName: 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3',
      categories: aiCategories
    }
  ]

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight text-foreground">
          {t('pages.settings.title')}
        </h1>
        <p className="max-w-2xl text-base text-muted-foreground">
          {t('pages.settings.subtitle')}
        </p>
      </div>

      <div className="space-y-5">
        {sections.map((section) => (
          <section
            key={section.id}
            className="rounded-2xl border bg-card/50 p-5 shadow-sm ring-1 ring-border/40"
          >
            <div className="mb-4 flex flex-col gap-1.5">
              <h2 className="text-lg font-semibold text-foreground">
                {t(section.titleKey)}
              </h2>
              <p className="text-sm text-muted-foreground">
                {t(section.descKey)}
              </p>
            </div>

            <div className={`grid gap-4 ${section.gridClassName}`}>
              {section.categories.map((category) => (
                <button
                  key={category.id}
                  onClick={() => navigate(category.path)}
                  className="group flex min-h-[108px] items-center gap-4 rounded-2xl border bg-background/90 p-4 text-left shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-md"
                >
                  <div className={`rounded-xl p-3 ${category.bgColor} ${category.color}`}>
                    <category.icon className="h-6 w-6" />
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-lg font-semibold leading-6 text-foreground">
                        {t(category.titleKey)}
                      </h3>
                      <div className="rounded-full bg-muted px-2.5 py-1 text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
                        <ChevronRight className="h-4 w-4" />
                      </div>
                    </div>
                    <p className="mt-1.5 line-clamp-2 text-sm leading-6 text-muted-foreground">
                      {t(category.descKey)}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
