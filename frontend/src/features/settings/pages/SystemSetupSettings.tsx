import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, BrainCircuit, FileType2, Languages, Loader2, Network, Settings2, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { uiChrome } from '@/components/ui/styles'
import { useAppStore } from '@/stores/app-store'
import { useModelBindingsQuery } from '@/features/ai-providers/queries'
import {
  RuntimeCapabilityMeta,
  useRuntimeConfigQuery,
  type RuntimeAutomationConfigResponse,
  type RuntimeConfigGroupKey,
  type RuntimeDocumentParsingConfigResponse,
  type RuntimeKnowledgeGraphConfigResponse,
  type RuntimeStorageConfigResponse,
} from '@/features/system-setup'
import { SettingsPageHeader, SettingsPageShell } from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'

type RuntimeStatusCard =
  | RuntimeStorageConfigResponse
  | RuntimeKnowledgeGraphConfigResponse
  | RuntimeDocumentParsingConfigResponse
  | RuntimeAutomationConfigResponse

interface CoreSummaryCard {
  key: string
  title: string
  value: string
  description: string
  icon: typeof Languages
  path?: string
}

function getModuleSummary(
  module: RuntimeStatusCard,
  groupKey: RuntimeConfigGroupKey,
  t: (key: string) => string
) {
  if (groupKey === 'knowledge_graph') {
    const knowledgeGraph = module as RuntimeKnowledgeGraphConfigResponse
    if (!knowledgeGraph.enabled) {
      return t('systemSetup.detailPages.lightrag.notStartedSummary')
    }
  }

  if (groupKey === 'document_parsing') {
    const documentParsing = module as RuntimeDocumentParsingConfigResponse
    if (!documentParsing.workerEnabled) {
      return t('systemSetup.detailPages.docling.notStartedSummary')
    }
  }

  return module.effectiveSummary
}

export function SystemSetupSettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useAppStore((state) => state.locale)
  const runtimeConfigQuery = useRuntimeConfigQuery()
  const bindingsQuery = useModelBindingsQuery()

  const bindingSummary = [
    bindingsQuery.data?.assistant?.llmModel?.name,
    bindingsQuery.data?.workflowCopilot?.llmModel?.name,
    bindingsQuery.data?.lightrag?.llmModel?.name,
  ].filter(Boolean)
  const coreCards: CoreSummaryCard[] = [
    {
      key: 'language',
      title: t('systemSetup.core.language'),
      value: locale === 'zh' ? '中文' : 'English',
      description: t('systemSetup.core.languageHint'),
      icon: Languages,
    },
    {
      key: 'bindings',
      title: t('systemSetup.core.modelBindings'),
      value: bindingSummary.length ? bindingSummary.join(' / ') : t('initialization.review.emptyValue'),
      description: t('systemSetup.core.modelBindingsHint'),
      icon: Bot,
      path: '/settings/ai-providers',
    },
    {
      key: 'entryTypes',
      title: t('systemSetup.core.entryTypes'),
      value: t('systemSetup.core.entryTypesValue'),
      description: t('systemSetup.core.entryTypesHint'),
      icon: FileType2,
      path: '/settings/entry-types',
    },
  ]

  const runtimeModules = useMemo(
    () => [
      {
        groupKey: 'storage' as const,
        title: t('systemSetup.moduleTitles.storage'),
        description: t('systemSetup.moduleDescriptions.storage'),
      },
      {
        groupKey: 'knowledge_graph' as const,
        title: t('systemSetup.moduleTitles.knowledgeGraph'),
        description: t('systemSetup.moduleDescriptions.knowledgeGraph'),
      },
      {
        groupKey: 'document_parsing' as const,
        title: t('systemSetup.moduleTitles.documentParsing'),
        description: t('systemSetup.moduleDescriptions.documentParsing'),
      },
      {
        groupKey: 'automation' as const,
        title: t('systemSetup.moduleTitles.automation'),
        description: t('systemSetup.moduleDescriptions.automation'),
      },
    ],
    [t]
  )
  const moduleDetailRoutes: Partial<Record<RuntimeConfigGroupKey, string>> = {
    automation: '/settings/automation',
    knowledge_graph: '/settings/lightrag',
    document_parsing: '/settings/docling',
  }

  if (runtimeConfigQuery.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    )
  }

  const runtimeConfig = runtimeConfigQuery.data
  const moduleMap: Record<RuntimeConfigGroupKey, RuntimeStatusCard> | null =
    runtimeConfig
      ? {
          storage: runtimeConfig.storage,
          knowledge_graph: runtimeConfig.knowledgeGraph,
          document_parsing: runtimeConfig.documentParsing,
          automation: runtimeConfig.automation,
        }
      : null

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('systemSetup.title')}
        description={t('systemSetup.description')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />

      <div className="grid gap-4 md:grid-cols-3">
        {coreCards.map((card) => {
          const content = (
            <div className="flex items-start gap-4">
              <div className={cn(uiChrome.inset, 'p-3 text-foreground')}>
                <card.icon className="h-5 w-5" />
              </div>
              <div className="space-y-2">
                <p className="text-sm text-muted-foreground">{card.title}</p>
                <p className="text-base font-semibold text-foreground">{card.value}</p>
                <p className="text-sm leading-6 text-muted-foreground">{card.description}</p>
              </div>
            </div>
          )

          if (card.path) {
            return (
              <button
                key={card.key}
                type="button"
                onClick={() => navigate(card.path!)}
                className={cn(
                  uiChrome.card,
                  'p-5 text-left transition duration-200 hover:border-primary/20',
                )}
              >
                {content}
              </button>
            )
          }

          return (
            <div
              key={card.key}
              className={cn(uiChrome.card, 'p-5 text-left')}
            >
              {content}
            </div>
          )
        })}
      </div>

      <div className={cn(uiChrome.inset, 'p-5')}>
        <div className="flex items-start gap-3">
          <div className={cn(uiChrome.control, 'p-2 text-foreground shadow-none')}>
            <Settings2 className="h-5 w-5" />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-semibold text-foreground">{t('systemSetup.runtimeTitle')}</p>
            <p className="text-sm leading-6 text-muted-foreground">{t('systemSetup.runtimeDescription')}</p>
          </div>
        </div>
      </div>

      {moduleMap ? (
        <div className="grid gap-4 md:grid-cols-2">
          {runtimeModules.map((module) => {
            const current = moduleMap[module.groupKey]
            const detailRoute = moduleDetailRoutes[module.groupKey]
            const summary = getModuleSummary(current, module.groupKey, t)
            return (
              <section key={module.groupKey} className={cn(uiChrome.card, 'p-5')}>
                <div className="space-y-4">
                  <RuntimeCapabilityMeta module={current} skipped={false} t={t} />
                  <div className="space-y-2">
                    <h2 className="text-lg font-semibold text-foreground">{module.title}</h2>
                    <p className="text-sm leading-6 text-muted-foreground">{module.description}</p>
                    <p className="text-sm leading-6 text-muted-foreground">{summary}</p>
                  </div>
                  <div className={cn(uiChrome.inset, 'px-4 py-4 text-sm leading-6 text-muted-foreground')}>
                    {t('systemSetup.managedHint')}
                  </div>
                  {detailRoute ? (
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => navigate(detailRoute)}
                    >
                      {t('systemSetup.actions.openDetails')}
                    </Button>
                  ) : null}
                </div>
              </section>
            )
          })}
        </div>
      ) : (
        <div className="rounded-[20px] border border-destructive/20 bg-destructive/5 p-6">
          <p className="text-sm text-destructive">{t('pages.graph.failedToLoad')}</p>
        </div>
      )}

      <div className={cn(uiChrome.card, 'p-5')}>
        <div className="flex items-start gap-4">
          <div className={cn(uiChrome.inset, 'p-3 text-foreground')}>
            <Network className="h-5 w-5" />
          </div>
          <div className="space-y-3">
            <div className="space-y-2">
              <p className="text-sm font-semibold text-foreground">{t('systemSetup.footerTitle')}</p>
              <p className="text-sm leading-6 text-muted-foreground">{t('systemSetup.footerDescription')}</p>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" variant="outline" onClick={() => navigate('/settings/assistant-skills')}>
                <BrainCircuit className="h-4 w-4" />
                {t('systemSetup.actions.openAiSkills')}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate('/settings/system-ai-behaviors')}>
                <Sparkles className="h-4 w-4" />
                {t('systemSetup.footerAction')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </SettingsPageShell>
  )
}
