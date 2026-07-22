import { useNavigate } from 'react-router-dom'
import { FileType, Tags, ChevronRight, Bot, Wrench, BrainCircuit, Network, Sparkles, Settings2, Clock3, PlugZap, Package } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { uiChrome, uiRadius } from '@/components/ui/styles'
import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
  SettingsSectionHeader,
} from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'
import { useSkillAdminSurfaceQuery } from '@/features/assistant-config/queries'

export function SettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const surface = useSkillAdminSurfaceQuery()
  const universalAvailable = Boolean(surface.data?.available)

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
      id: 'universal-skills',
      titleKey: 'pages.settings.universalSkills',
      descKey: 'pages.settings.universalSkillsDesc',
      icon: Package,
      path: '/settings/universal-skills',
      color: 'text-indigo-500',
      bgColor: 'bg-indigo-500/10'
    },
    {
      id: 'main-agent-profile',
      titleKey: 'pages.settings.mainAgentProfile',
      descKey: 'pages.settings.mainAgentProfileDesc',
      icon: Bot,
      path: '/settings/main-agent-profile',
      color: 'text-violet-500',
      bgColor: 'bg-violet-500/10'
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
      id: 'openclaw-integration',
      titleKey: 'pages.settings.openClawIntegration',
      descKey: 'pages.settings.openClawIntegrationDesc',
      icon: PlugZap,
      path: '/settings/openclaw-integration',
      color: 'text-orange-600',
      bgColor: 'bg-orange-500/10'
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

  const visibleAiCategories = aiCategories.filter((category) => {
    if (category.id === 'universal-skills' || category.id === 'main-agent-profile') {
      return universalAvailable
    }
    return true
  })

  const runtimeCategories = [
    {
      id: 'lightrag',
      titleKey: 'pages.settings.lightRag',
      descKey: 'pages.settings.lightRagDesc',
      icon: Network,
      path: '/settings/lightrag',
      color: 'text-cyan-600',
      bgColor: 'bg-cyan-500/10'
    },
    {
      id: 'docling',
      titleKey: 'pages.settings.docling',
      descKey: 'pages.settings.doclingDesc',
      icon: Wrench,
      path: '/settings/docling',
      color: 'text-emerald-600',
      bgColor: 'bg-emerald-500/10'
    },
    {
      id: 'automation',
      titleKey: 'pages.settings.automation',
      descKey: 'pages.settings.automationDesc',
      icon: Clock3,
      path: '/settings/automation',
      color: 'text-amber-600',
      bgColor: 'bg-amber-500/10'
    },
    {
      id: 'system-setup',
      titleKey: 'pages.settings.systemSetup',
      descKey: 'pages.settings.systemSetupDesc',
      icon: Settings2,
      path: '/settings/system-setup',
      color: 'text-slate-700',
      bgColor: 'bg-slate-500/10'
    }
  ]

  const sections = [
    {
      id: 'content',
      titleKey: 'pages.settings.contentSection',
      descKey: 'pages.settings.contentSectionDesc',
      gridClassName: 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3',
      categories: contentCategories
    },
    {
      id: 'ai',
      titleKey: 'pages.settings.aiSection',
      descKey: 'pages.settings.aiSectionDesc',
      gridClassName: 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3',
      categories: visibleAiCategories
    },
    {
      id: 'runtime',
      titleKey: 'pages.settings.runtimeSection',
      descKey: 'pages.settings.runtimeSectionDesc',
      gridClassName: 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3',
      categories: runtimeCategories
    }
  ]

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.title')}
        description={t('pages.settings.subtitle')}
      />

      <div className="space-y-5">
        {sections.map((section) => (
          <SettingsSection key={section.id} className="space-y-5 p-5 sm:p-6">
            <SettingsSectionHeader
              title={t(section.titleKey)}
              description={t(section.descKey)}
            />

            <div className={`grid gap-4 ${section.gridClassName}`}>
              {section.categories.map((category) => (
                <button
                  key={category.id}
                  onClick={() => navigate(category.path)}
                  className={cn(
                    uiChrome.control,
                    'group flex min-h-[108px] items-center gap-4 p-4 text-left transition-all duration-200',
                    'hover:border-primary/20 hover:bg-background',
                  )}
                >
                  <div className={cn(uiRadius.control, 'flex h-12 w-12 items-center justify-center p-3', category.bgColor, category.color)}>
                    <category.icon className="h-6 w-6" />
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className="text-lg font-semibold leading-6 text-foreground">
                        {t(category.titleKey)}
                      </h3>
                      <div className={cn(uiChrome.control, 'flex h-8 w-8 items-center justify-center p-0 text-muted-foreground shadow-none transition-colors group-hover:border-primary/20 group-hover:text-primary')}>
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
          </SettingsSection>
        ))}
      </div>
    </SettingsPageShell>
  )
}
