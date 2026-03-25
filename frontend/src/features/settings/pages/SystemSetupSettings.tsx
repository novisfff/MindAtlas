import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, FileType2, Languages, Loader2, Network, RefreshCcw, Settings2, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/stores/app-store'
import { useModelBindingsQuery } from '@/features/ai-providers/queries'
import {
  AutomationCapabilityFields,
  DocumentParsingCapabilityFields,
  KnowledgeGraphCapabilityFields,
  RuntimeCapabilityMeta,
  StorageCapabilityFields,
  useRuntimeConfigQuery,
  useUpdateRuntimeConfigMutation,
  useValidateRuntimeConfigMutation,
  type RuntimeAutomationDraft,
  type RuntimeConfigGroupKey,
  type RuntimeConfigValidationResponse,
  type RuntimeDocumentParsingDraft,
  type RuntimeKnowledgeGraphDraft,
  type RuntimeStorageDraft,
} from '@/features/system-setup'

type RuntimeDraftState = {
  storage: RuntimeStorageDraft
  knowledgeGraph: RuntimeKnowledgeGraphDraft
  documentParsing: RuntimeDocumentParsingDraft
  automation: RuntimeAutomationDraft
}

function toRuntimeDraftState(data: ReturnType<typeof useRuntimeConfigQuery>['data']): RuntimeDraftState | null {
  if (!data) return null
  return {
    storage: {
      ...data.storage,
      accessKey: '',
      secretKey: '',
    },
    knowledgeGraph: {
      ...data.knowledgeGraph,
      llmModelName: data.knowledgeGraph.llmModelName ?? '',
      embeddingModelName: data.knowledgeGraph.embeddingModelName ?? '',
      neo4jPassword: '',
      rerankApiKey: '',
    },
    documentParsing: {
      ...data.documentParsing,
      pictureDescriptionApiKey: '',
    },
    automation: {
      ...data.automation,
    },
  }
}

export function SystemSetupSettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useAppStore((state) => state.locale)
  const runtimeConfigQuery = useRuntimeConfigQuery()
  const bindingsQuery = useModelBindingsQuery()

  const storageMutation = useUpdateRuntimeConfigMutation('storage')
  const knowledgeGraphMutation = useUpdateRuntimeConfigMutation('knowledge_graph')
  const documentParsingMutation = useUpdateRuntimeConfigMutation('document_parsing')
  const automationMutation = useUpdateRuntimeConfigMutation('automation')

  const validateStorageMutation = useValidateRuntimeConfigMutation('storage')
  const validateKnowledgeGraphMutation = useValidateRuntimeConfigMutation('knowledge_graph')

  const [expandedGroup, setExpandedGroup] = useState<RuntimeConfigGroupKey | null>('storage')
  const [drafts, setDrafts] = useState<RuntimeDraftState | null>(null)
  const [validation, setValidation] = useState<Partial<Record<RuntimeConfigGroupKey, RuntimeConfigValidationResponse | null>>>({})

  useEffect(() => {
    setDrafts(toRuntimeDraftState(runtimeConfigQuery.data))
  }, [runtimeConfigQuery.data])

  const moduleMeta = useMemo(
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

  const bindingSummary = [
    bindingsQuery.data?.assistant?.llmModel?.name,
    bindingsQuery.data?.workflowCopilot?.llmModel?.name,
    bindingsQuery.data?.lightrag?.llmModel?.name,
  ].filter(Boolean)

  const handleSave = async (groupKey: RuntimeConfigGroupKey) => {
    if (!drafts) return

    try {
      if (groupKey === 'storage') {
        await storageMutation.mutateAsync({
          endpoint: drafts.storage.endpoint,
          accessKey: drafts.storage.accessKey,
          secretKey: drafts.storage.secretKey,
          bucket: drafts.storage.bucket,
          secure: drafts.storage.secure,
          maxFileSizeMb: drafts.storage.maxFileSizeMb,
          maxPdfPages: drafts.storage.maxPdfPages,
        })
      } else if (groupKey === 'knowledge_graph') {
        await knowledgeGraphMutation.mutateAsync({
          enabled: drafts.knowledgeGraph.enabled,
          neo4jUri: drafts.knowledgeGraph.neo4jUri,
          neo4jUser: drafts.knowledgeGraph.neo4jUser,
          neo4jPassword: drafts.knowledgeGraph.neo4jPassword,
          neo4jDatabase: drafts.knowledgeGraph.neo4jDatabase,
          workspace: drafts.knowledgeGraph.workspace,
          graphStorage: drafts.knowledgeGraph.graphStorage,
          summaryLanguage: drafts.knowledgeGraph.summaryLanguage,
          llmModelName: drafts.knowledgeGraph.llmModelName || undefined,
          embeddingModelName: drafts.knowledgeGraph.embeddingModelName || undefined,
          rerankModel: drafts.knowledgeGraph.rerankModel,
          rerankHost: drafts.knowledgeGraph.rerankHost,
          rerankApiKey: drafts.knowledgeGraph.rerankApiKey,
          rerankRequestFormat: drafts.knowledgeGraph.rerankRequestFormat,
        })
      } else if (groupKey === 'document_parsing') {
        await documentParsingMutation.mutateAsync({
          workerEnabled: drafts.documentParsing.workerEnabled,
          ocrEnabled: drafts.documentParsing.ocrEnabled,
          ocrLangs: drafts.documentParsing.ocrLangs,
          pictureDescriptionEnabled: drafts.documentParsing.pictureDescriptionEnabled,
          pictureDescriptionUrl: drafts.documentParsing.pictureDescriptionUrl,
          pictureDescriptionApiKey: drafts.documentParsing.pictureDescriptionApiKey,
          pictureDescriptionModel: drafts.documentParsing.pictureDescriptionModel,
          pictureDescriptionPrompt: drafts.documentParsing.pictureDescriptionPrompt,
          pictureDescriptionTimeoutSec: drafts.documentParsing.pictureDescriptionTimeoutSec,
          pictureDescriptionParamsJson: drafts.documentParsing.pictureDescriptionParamsJson,
          maxFileSizeMb: drafts.documentParsing.maxFileSizeMb,
          maxPdfPages: drafts.documentParsing.maxPdfPages,
        })
      } else {
        await automationMutation.mutateAsync({
          schedulerEnabled: drafts.automation.schedulerEnabled,
        })
      }

      setValidation((state) => ({ ...state, [groupKey]: null }))
      toast.success(t('systemSetup.messages.saved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('messages.error'))
    }
  }

  const handleValidate = async (groupKey: Extract<RuntimeConfigGroupKey, 'storage' | 'knowledge_graph'>) => {
    if (!drafts) return

    try {
      const result = groupKey === 'storage'
        ? await validateStorageMutation.mutateAsync({
            endpoint: drafts.storage.endpoint,
            accessKey: drafts.storage.accessKey,
            secretKey: drafts.storage.secretKey,
            bucket: drafts.storage.bucket,
            secure: drafts.storage.secure,
            maxFileSizeMb: drafts.storage.maxFileSizeMb,
            maxPdfPages: drafts.storage.maxPdfPages,
          })
        : await validateKnowledgeGraphMutation.mutateAsync({
            enabled: drafts.knowledgeGraph.enabled,
            neo4jUri: drafts.knowledgeGraph.neo4jUri,
            neo4jUser: drafts.knowledgeGraph.neo4jUser,
            neo4jPassword: drafts.knowledgeGraph.neo4jPassword,
            neo4jDatabase: drafts.knowledgeGraph.neo4jDatabase,
            workspace: drafts.knowledgeGraph.workspace,
            graphStorage: drafts.knowledgeGraph.graphStorage,
            summaryLanguage: drafts.knowledgeGraph.summaryLanguage,
            llmModelName: drafts.knowledgeGraph.llmModelName || undefined,
            embeddingModelName: drafts.knowledgeGraph.embeddingModelName || undefined,
            rerankModel: drafts.knowledgeGraph.rerankModel,
            rerankHost: drafts.knowledgeGraph.rerankHost,
            rerankApiKey: drafts.knowledgeGraph.rerankApiKey,
            rerankRequestFormat: drafts.knowledgeGraph.rerankRequestFormat,
          })

      setValidation((state) => ({ ...state, [groupKey]: result }))
      if (result.ok) {
        toast.success(result.message || t('systemSetup.messages.validationPassed'))
      } else {
        toast.error(result.message || t('systemSetup.messages.validationFailed'))
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('messages.error'))
    }
  }

  if (runtimeConfigQuery.isLoading && !drafts) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    )
  }

  const runtimeConfig = runtimeConfigQuery.data
  if (!runtimeConfig || !drafts) {
    return (
      <div className="rounded-[28px] border border-red-100 bg-white p-6 shadow-sm">
        <p className="text-sm text-red-600">{t('pages.graph.failedToLoad')}</p>
      </div>
    )
  }

  const moduleMap = {
    storage: runtimeConfig.storage,
    knowledge_graph: runtimeConfig.knowledgeGraph,
    document_parsing: runtimeConfig.documentParsing,
    automation: runtimeConfig.automation,
  }

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

      <div className="space-y-4">
        {moduleMeta.map((module) => {
          const current = moduleMap[module.groupKey]
          const isExpanded = expandedGroup === module.groupKey
          const validationState = validation[module.groupKey]
          const isSaving =
            (module.groupKey === 'storage' && storageMutation.isPending) ||
            (module.groupKey === 'knowledge_graph' && knowledgeGraphMutation.isPending) ||
            (module.groupKey === 'document_parsing' && documentParsingMutation.isPending) ||
            (module.groupKey === 'automation' && automationMutation.isPending)
          const isValidating =
            (module.groupKey === 'storage' && validateStorageMutation.isPending) ||
            (module.groupKey === 'knowledge_graph' && validateKnowledgeGraphMutation.isPending)

          return (
            <section key={module.groupKey} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="space-y-3">
                  <RuntimeCapabilityMeta module={current} skipped={false} t={t} />
                  <div className="space-y-2">
                    <h2 className="text-xl font-semibold text-slate-900">{module.title}</h2>
                    <p className="max-w-2xl text-sm leading-6 text-slate-600">{module.description}</p>
                    <p className="text-sm leading-6 text-slate-500">{current.effectiveSummary}</p>
                  </div>
                </div>
                <Button
                  type="button"
                  variant={isExpanded ? 'outline' : 'default'}
                  onClick={() => setExpandedGroup(isExpanded ? null : module.groupKey)}
                  className="rounded-2xl"
                >
                  {isExpanded ? t('systemSetup.actions.collapse') : t('systemSetup.actions.configure')}
                </Button>
              </div>

              {isExpanded ? (
                <div className="mt-6 space-y-5 border-t border-slate-200 pt-5">
                  {module.groupKey === 'storage' ? (
                    <StorageCapabilityFields
                      value={drafts.storage}
                      onChange={(patch) => setDrafts((state) => state ? { ...state, storage: { ...state.storage, ...patch } } : state)}
                      t={t}
                    />
                  ) : null}

                  {module.groupKey === 'knowledge_graph' ? (
                    <KnowledgeGraphCapabilityFields
                      value={drafts.knowledgeGraph}
                      onChange={(patch) => setDrafts((state) => state ? { ...state, knowledgeGraph: { ...state.knowledgeGraph, ...patch } } : state)}
                      t={t}
                    />
                  ) : null}

                  {module.groupKey === 'document_parsing' ? (
                    <DocumentParsingCapabilityFields
                      value={drafts.documentParsing}
                      onChange={(patch) => setDrafts((state) => state ? { ...state, documentParsing: { ...state.documentParsing, ...patch } } : state)}
                      t={t}
                    />
                  ) : null}

                  {module.groupKey === 'automation' ? (
                    <AutomationCapabilityFields
                      value={drafts.automation}
                      onChange={(patch) => setDrafts((state) => state ? { ...state, automation: { ...state.automation, ...patch } } : state)}
                      t={t}
                    />
                  ) : null}

                  {validationState ? (
                    <div className={cn(
                      'rounded-[22px] border px-4 py-4 text-sm leading-6',
                      validationState.ok
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                        : 'border-amber-200 bg-amber-50 text-amber-700'
                    )}>
                      <p className="font-semibold">{validationState.message}</p>
                      {!validationState.ok && Object.keys(validationState.fieldErrors).length ? (
                        <div className="mt-2 space-y-1">
                          {Object.entries(validationState.fieldErrors).map(([field, message]) => (
                            <p key={field}>{field}: {message}</p>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-3">
                    {module.groupKey === 'storage' || module.groupKey === 'knowledge_graph' ? (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => void handleValidate(module.groupKey)}
                        disabled={isValidating || isSaving}
                        className="rounded-2xl"
                      >
                        {isValidating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                        {t('systemSetup.actions.validate')}
                      </Button>
                    ) : null}

                    <Button
                      type="button"
                      onClick={() => void handleSave(module.groupKey)}
                      disabled={isSaving}
                      className="rounded-2xl"
                    >
                      {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                      {t('systemSetup.actions.save')}
                    </Button>
                  </div>
                </div>
              ) : null}
            </section>
          )
        })}
      </div>

      <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
            <Network className="h-5 w-5" />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-semibold text-slate-900">{t('systemSetup.footerTitle')}</p>
            <p className="text-sm leading-6 text-slate-600">{t('systemSetup.footerDescription')}</p>
            <Button type="button" variant="outline" onClick={() => navigate('/settings/system-ai-behaviors')} className="rounded-2xl">
              <Sparkles className="h-4 w-4" />
              {t('systemSetup.footerAction')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
