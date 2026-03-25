import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, FileType2, Languages, Loader2, Network, Settings2, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
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

type RuntimeStatusCard =
  | RuntimeStorageConfigResponse
  | RuntimeKnowledgeGraphConfigResponse
  | RuntimeDocumentParsingConfigResponse
  | RuntimeAutomationConfigResponse

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
    <div className="mx-auto max-w-5xl space-y-8">
      <div className="space-y-3">
        <div className="inline-flex rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">
          {t('systemSetup.eyebrow')}
        </div>
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
            {t('systemSetup.title')}
          </h1>
          <p className="max-w-3xl text-sm leading-7 text-slate-600">
            {t('systemSetup.description')}
          </p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <button
          type="button"
          onClick={() => navigate('/settings/ai-providers')}
          className="rounded-[24px] border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
        >
          <div className="flex items-start gap-4">
            <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
              <Languages className="h-5 w-5" />
            </div>
            <div className="space-y-2">
              <p className="text-sm text-slate-500">{t('systemSetup.core.language')}</p>
              <p className="text-base font-semibold text-slate-900">{locale === 'zh' ? '中文' : 'English'}</p>
              <p className="text-sm leading-6 text-slate-600">{t('systemSetup.core.languageHint')}</p>
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={() => navigate('/settings/ai-providers')}
          className="rounded-[24px] border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
        >
          <div className="flex items-start gap-4">
            <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
              <Bot className="h-5 w-5" />
            </div>
            <div className="space-y-2">
              <p className="text-sm text-slate-500">{t('systemSetup.core.modelBindings')}</p>
              <p className="text-base font-semibold text-slate-900">
                {bindingSummary.length ? bindingSummary.join(' / ') : t('initialization.review.emptyValue')}
              </p>
              <p className="text-sm leading-6 text-slate-600">{t('systemSetup.core.modelBindingsHint')}</p>
            </div>
          </div>
        </button>

        <button
          type="button"
          onClick={() => navigate('/settings/entry-types')}
          className="rounded-[24px] border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
        >
          <div className="flex items-start gap-4">
            <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
              <FileType2 className="h-5 w-5" />
            </div>
            <div className="space-y-2">
              <p className="text-sm text-slate-500">{t('systemSetup.core.entryTypes')}</p>
              <p className="text-base font-semibold text-slate-900">{t('systemSetup.core.entryTypesValue')}</p>
              <p className="text-sm leading-6 text-slate-600">{t('systemSetup.core.entryTypesHint')}</p>
            </div>
          </div>
        </button>
      </div>

      <div className="rounded-[28px] border border-slate-200 bg-slate-50/70 p-5">
        <div className="flex items-start gap-3">
          <div className="rounded-2xl bg-white p-2 text-slate-700 shadow-sm">
            <Settings2 className="h-5 w-5" />
          </div>
          <div className="space-y-1">
            <p className="text-sm font-semibold text-slate-900">{t('systemSetup.runtimeTitle')}</p>
            <p className="text-sm leading-6 text-slate-600">{t('systemSetup.runtimeDescription')}</p>
          </div>
        </div>
      </div>

      {moduleMap ? (
        <div className="grid gap-4 md:grid-cols-2">
          {runtimeModules.map((module) => {
            const current = moduleMap[module.groupKey]
            return (
              <section key={module.groupKey} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
                <div className="space-y-4">
                  <RuntimeCapabilityMeta module={current} skipped={false} t={t} />
                  <div className="space-y-2">
                    <h2 className="text-lg font-semibold text-slate-900">{module.title}</h2>
                    <p className="text-sm leading-6 text-slate-600">{module.description}</p>
                    <p className="text-sm leading-6 text-slate-500">{current.effectiveSummary}</p>
                  </div>
                  <div className="rounded-[22px] border border-slate-200 bg-slate-50/80 px-4 py-4 text-sm leading-6 text-slate-600">
                    {t('systemSetup.managedHint')}
                  </div>
                </div>
              </section>
            )
          })}
        </div>
      ) : (
        <div className="rounded-[28px] border border-red-100 bg-white p-6 shadow-sm">
          <p className="text-sm text-red-600">{t('pages.graph.failedToLoad')}</p>
        </div>
      )}

      <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
            <Network className="h-5 w-5" />
          </div>
          <div className="space-y-3">
            <div className="space-y-2">
              <p className="text-sm font-semibold text-slate-900">{t('systemSetup.footerTitle')}</p>
              <p className="text-sm leading-6 text-slate-600">{t('systemSetup.footerDescription')}</p>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" variant="outline" onClick={() => navigate('/settings/ai-providers')} className="rounded-2xl">
                <Bot className="h-4 w-4" />
                {t('systemSetup.actions.openAiProviders')}
              </Button>
              <Button type="button" variant="outline" onClick={() => navigate('/settings/system-ai-behaviors')} className="rounded-2xl">
                <Sparkles className="h-4 w-4" />
                {t('systemSetup.footerAction')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
