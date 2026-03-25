import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Languages,
  Loader2,
  Plus,
  Rocket,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wand2,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { initializationKeys, useInitializationDefaultsQuery, useInitializationStatusQuery, useInitializeSystemMutation } from '../queries'
import {
  setPersistedInitializationStatus,
  useInitializationWizardStore,
  type InitializationDraftEntryType,
} from '../store'
import { useAppStore, type Locale } from '@/stores/app-store'
import { discoverModelsByKey } from '@/features/ai-providers/api/credentials'
import {
  AutomationCapabilityFields,
  DocumentParsingCapabilityFields,
  getRuntimeCapabilityStatus,
  KnowledgeGraphCapabilityFields,
  RuntimeCapabilityMeta,
  StorageCapabilityFields,
  type RuntimeConfigGroupKey,
} from '@/features/system-setup'

const STEP_KEYS = ['language', 'ai', 'entryTypes', 'capabilities', 'review'] as const

const PROVIDER_PRESETS = [
  {
    key: 'openai',
    label: 'OpenAI',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    className: 'border-emerald-200/80 from-emerald-500/15 to-teal-500/5',
  },
  {
    key: 'openrouter',
    label: 'OpenRouter',
    name: 'OpenRouter',
    baseUrl: 'https://openrouter.ai/api/v1',
    className: 'border-sky-200/80 from-sky-500/15 to-blue-500/5',
  },
  {
    key: 'siliconflow',
    label: 'SiliconFlow',
    name: 'SiliconFlow',
    baseUrl: 'https://api.siliconflow.cn/v1',
    className: 'border-violet-200/80 from-violet-500/15 to-fuchsia-500/5',
  },
  {
    key: 'deepseek',
    label: 'DeepSeek',
    name: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    className: 'border-amber-200/80 from-amber-500/15 to-orange-500/5',
  },
] as const

const FIELD_CLASSNAME =
  'h-11 w-full rounded-2xl border border-slate-200 bg-white px-4 text-sm text-slate-900 shadow-sm outline-none transition focus:border-slate-900/50 focus:ring-4 focus:ring-slate-900/5'
const TEXTAREA_CLASSNAME =
  'w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-slate-900/50 focus:ring-4 focus:ring-slate-900/5'

function Label({ children }: { children: ReactNode }) {
  return <label className="text-sm font-medium text-slate-800">{children}</label>
}

function StepIndicator({ currentStep }: { currentStep: number }) {
  const { t } = useTranslation()

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="rounded-2xl bg-slate-900 p-3 text-white shadow-lg shadow-slate-900/10">
          <Logo className="h-7 w-7" />
        </div>
        <div>
          <p className="text-sm font-medium uppercase tracking-[0.22em] text-slate-500">
            {t('initialization.eyebrow')}
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
            {t('initialization.title')}
          </h1>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{t('initialization.progressLabel')}</span>
          <span>{currentStep + 1} / {STEP_KEYS.length}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-slate-900 transition-all duration-300"
            style={{ width: `${((currentStep + 1) / STEP_KEYS.length) * 100}%` }}
          />
        </div>
        <div className="grid gap-3 md:grid-cols-5">
          {STEP_KEYS.map((key, index) => {
            const active = index === currentStep
            const done = index < currentStep
            return (
              <div
                key={key}
                className={cn(
                  'rounded-2xl border px-4 py-3 transition-all',
                  done && 'border-emerald-200 bg-emerald-50 text-emerald-700',
                  active && 'border-slate-900 bg-slate-900 text-white shadow-lg shadow-slate-900/15',
                  !active && !done && 'border-slate-200 bg-white text-slate-500'
                )}
              >
                <div className="flex items-center gap-2">
                  <div
                    className={cn(
                      'flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold',
                      done && 'bg-emerald-100 text-emerald-700',
                      active && 'bg-white/15 text-white',
                      !active && !done && 'bg-slate-100 text-slate-600'
                    )}
                  >
                    {done ? <CheckCircle2 className="h-4 w-4" /> : index + 1}
                  </div>
                  <span className="text-sm font-medium">{t(`initialization.steps.${key}.label`)}</span>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function EntryTypeCard({
  item,
  onUpdate,
  onRemove,
}: {
  item: InitializationDraftEntryType
  onUpdate: (patch: Partial<InitializationDraftEntryType>) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()

  return (
    <div className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'inline-flex items-center rounded-full px-3 py-1 text-xs font-medium',
                  item.origin === 'default' ? 'bg-slate-100 text-slate-700' : 'bg-amber-100 text-amber-700'
                )}
              >
                {t(`initialization.entryTypes.origin.${item.origin}`)}
              </span>
              {item.code ? (
                <span className="rounded-full bg-slate-50 px-2.5 py-1 font-mono text-[11px] text-slate-500">
                  {item.code}
                </span>
              ) : null}
            </div>
            <p className="text-sm text-slate-500">
              {item.origin === 'default'
                ? t('initialization.entryTypes.defaultHint')
                : t('initialization.entryTypes.customHint')}
            </p>
          </div>

          <button
            type="button"
            onClick={onRemove}
            className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 text-slate-500 transition hover:border-red-200 hover:bg-red-50 hover:text-red-600"
            aria-label={t('initialization.entryTypes.remove')}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>

        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
          <div className="space-y-2">
            <Label>{t('initialization.entryTypes.fields.name')}</Label>
            <input
              value={item.name}
              onChange={(event) => onUpdate({ name: event.target.value })}
              className={FIELD_CLASSNAME}
              placeholder={t('initialization.entryTypes.placeholders.name')}
            />
          </div>

          <div className="space-y-2">
            <Label>{t('initialization.entryTypes.fields.icon')}</Label>
            <input
              value={item.icon || ''}
              onChange={(event) => onUpdate({ icon: event.target.value })}
              className={FIELD_CLASSNAME}
              placeholder={t('initialization.entryTypes.placeholders.icon')}
            />
          </div>

          <div className="space-y-2">
            <Label>{t('initialization.entryTypes.fields.color')}</Label>
            <div className="flex h-11 items-center justify-center rounded-2xl border border-slate-200 bg-white px-3 shadow-sm">
              <input
                type="color"
                value={item.color || '#3B82F6'}
                onChange={(event) => onUpdate({ color: event.target.value })}
                className="h-8 w-10 cursor-pointer rounded border-0 bg-transparent"
              />
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <Label>{t('initialization.entryTypes.fields.description')}</Label>
          <textarea
            value={item.description || ''}
            onChange={(event) => onUpdate({ description: event.target.value })}
            className={TEXTAREA_CLASSNAME}
            rows={3}
            placeholder={t('initialization.entryTypes.placeholders.description')}
          />
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          {[
            { key: 'enabled', label: t('initialization.entryTypes.toggles.enabled') },
            { key: 'graphEnabled', label: t('initialization.entryTypes.toggles.graphEnabled') },
            { key: 'aiEnabled', label: t('initialization.entryTypes.toggles.aiEnabled') },
          ].map((toggle) => (
            <div key={toggle.key} className="flex items-center justify-between rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3">
              <span className="text-sm font-medium text-slate-700">{toggle.label}</span>
              <Switch
                checked={Boolean(item[toggle.key as keyof InitializationDraftEntryType])}
                onCheckedChange={(checked) => onUpdate({ [toggle.key]: checked } as Partial<InitializationDraftEntryType>)}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function LanguageStep({
  locale,
  onSelect,
}: {
  locale: Locale
  onSelect: (locale: Locale) => Promise<void>
}) {
  const { t } = useTranslation()

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-2xl font-semibold text-slate-900">
          {t('initialization.steps.language.title')}
        </h2>
        <p className="max-w-2xl text-sm leading-6 text-slate-600">
          {t('initialization.steps.language.description')}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {[
          {
            value: 'zh' as const,
            title: '中文',
            description: t('initialization.steps.language.options.zh'),
          },
          {
            value: 'en' as const,
            title: 'English',
            description: t('initialization.steps.language.options.en'),
          },
        ].map((item) => (
          <button
            key={item.value}
            type="button"
            onClick={() => {
              void onSelect(item.value)
            }}
            className={cn(
              'rounded-[28px] border p-6 text-left transition-all duration-200',
              'bg-gradient-to-br from-white to-slate-50 shadow-sm hover:-translate-y-0.5 hover:shadow-lg',
              locale === item.value
                ? 'border-slate-900 ring-4 ring-slate-900/5'
                : 'border-slate-200 hover:border-slate-300'
            )}
          >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-3">
                <div className="inline-flex rounded-2xl bg-slate-900/5 p-3 text-slate-900">
                  <Languages className="h-6 w-6" />
                </div>
                <div>
                  <h3 className="text-xl font-semibold text-slate-900">{item.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{item.description}</p>
                </div>
              </div>
              {locale === item.value ? <CheckCircle2 className="h-6 w-6 text-emerald-500" /> : null}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function CapabilityOverviewCard({
  title,
  description,
  summary,
  meta,
  onConfigure,
  onSkip,
  onUndoSkip,
  skipped,
}: {
  title: string
  description: string
  summary: string
  meta: ReactNode
  onConfigure: () => void
  onSkip: () => void
  onUndoSkip: () => void
  skipped: boolean
}) {
  const { t } = useTranslation()

  return (
    <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <div className="space-y-4">
        <div className="space-y-3">
          {meta}
          <div className="space-y-2">
            <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
            <p className="text-sm leading-6 text-slate-600">{description}</p>
          </div>
        </div>

        <div className="rounded-[22px] border border-slate-200 bg-slate-50/70 px-4 py-4">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">
            {t('initialization.capabilities.currentState')}
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-700">{summary}</p>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row">
          <Button type="button" onClick={onConfigure} className="rounded-2xl">
            {t('initialization.capabilities.configure')}
          </Button>
          {skipped ? (
            <Button type="button" variant="outline" onClick={onUndoSkip} className="rounded-2xl">
              {t('initialization.capabilities.undoSkip')}
            </Button>
          ) : (
            <Button type="button" variant="outline" onClick={onSkip} className="rounded-2xl">
              {t('initialization.capabilities.skip')}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function buildRuntimeConfigPayload(state: ReturnType<typeof useInitializationWizardStore.getState>) {
  const payload: Record<string, unknown> = {}
  const skipped = new Set(state.skippedCapabilityGroups)

  if (!skipped.has('storage') && state.runtimeConfigDraft.storage.isDirty) {
    payload.storage = {
      endpoint: state.runtimeConfigDraft.storage.endpoint,
      accessKey: state.runtimeConfigDraft.storage.accessKey,
      secretKey: state.runtimeConfigDraft.storage.secretKey,
      bucket: state.runtimeConfigDraft.storage.bucket,
      secure: state.runtimeConfigDraft.storage.secure,
      maxFileSizeMb: state.runtimeConfigDraft.storage.maxFileSizeMb,
      maxPdfPages: state.runtimeConfigDraft.storage.maxPdfPages,
    }
  }

  if (!skipped.has('knowledge_graph') && state.runtimeConfigDraft.knowledgeGraph.isDirty) {
    payload.knowledgeGraph = {
      enabled: state.runtimeConfigDraft.knowledgeGraph.enabled,
      neo4jUri: state.runtimeConfigDraft.knowledgeGraph.neo4jUri,
      neo4jUser: state.runtimeConfigDraft.knowledgeGraph.neo4jUser,
      neo4jPassword: state.runtimeConfigDraft.knowledgeGraph.neo4jPassword,
      neo4jDatabase: state.runtimeConfigDraft.knowledgeGraph.neo4jDatabase,
      workspace: state.runtimeConfigDraft.knowledgeGraph.workspace,
      graphStorage: state.runtimeConfigDraft.knowledgeGraph.graphStorage,
      summaryLanguage: state.runtimeConfigDraft.knowledgeGraph.summaryLanguage,
      llmModelName: state.runtimeConfigDraft.knowledgeGraph.llmModelName,
      embeddingModelName: state.runtimeConfigDraft.knowledgeGraph.embeddingModelName,
      rerankModel: state.runtimeConfigDraft.knowledgeGraph.rerankModel,
      rerankHost: state.runtimeConfigDraft.knowledgeGraph.rerankHost,
      rerankApiKey: state.runtimeConfigDraft.knowledgeGraph.rerankApiKey,
      rerankRequestFormat: state.runtimeConfigDraft.knowledgeGraph.rerankRequestFormat,
    }
  }

  if (!skipped.has('document_parsing') && state.runtimeConfigDraft.documentParsing.isDirty) {
    payload.documentParsing = {
      workerEnabled: state.runtimeConfigDraft.documentParsing.workerEnabled,
      ocrEnabled: state.runtimeConfigDraft.documentParsing.ocrEnabled,
      ocrLangs: state.runtimeConfigDraft.documentParsing.ocrLangs,
      pictureDescriptionEnabled: state.runtimeConfigDraft.documentParsing.pictureDescriptionEnabled,
      pictureDescriptionUrl: state.runtimeConfigDraft.documentParsing.pictureDescriptionUrl,
      pictureDescriptionApiKey: state.runtimeConfigDraft.documentParsing.pictureDescriptionApiKey,
      pictureDescriptionModel: state.runtimeConfigDraft.documentParsing.pictureDescriptionModel,
      pictureDescriptionPrompt: state.runtimeConfigDraft.documentParsing.pictureDescriptionPrompt,
      pictureDescriptionTimeoutSec: state.runtimeConfigDraft.documentParsing.pictureDescriptionTimeoutSec,
      pictureDescriptionParamsJson: state.runtimeConfigDraft.documentParsing.pictureDescriptionParamsJson,
      maxFileSizeMb: state.runtimeConfigDraft.documentParsing.maxFileSizeMb,
      maxPdfPages: state.runtimeConfigDraft.documentParsing.maxPdfPages,
    }
  }

  if (!skipped.has('automation') && state.runtimeConfigDraft.automation.isDirty) {
    payload.automation = {
      schedulerEnabled: state.runtimeConfigDraft.automation.schedulerEnabled,
    }
  }

  return Object.keys(payload).length ? payload : undefined
}

export function SystemInitializationPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { t, i18n } = useTranslation()
  const statusQuery = useInitializationStatusQuery()
  const initializeMutation = useInitializeSystemMutation()
  const setAppLocale = useAppStore((state) => state.setLocale)

  const step = useInitializationWizardStore((state) => state.step)
  const locale = useInitializationWizardStore((state) => state.locale)
  const aiCredential = useInitializationWizardStore((state) => state.aiCredential)
  const llmModelName = useInitializationWizardStore((state) => state.llmModelName)
  const entryTypes = useInitializationWizardStore((state) => state.entryTypes)
  const capabilityModules = useInitializationWizardStore((state) => state.capabilityModules)
  const activeCapabilityGroup = useInitializationWizardStore((state) => state.activeCapabilityGroup)
  const skippedCapabilityGroups = useInitializationWizardStore((state) => state.skippedCapabilityGroups)
  const runtimeConfigDraft = useInitializationWizardStore((state) => state.runtimeConfigDraft)
  const setStep = useInitializationWizardStore((state) => state.setStep)
  const setDraftLocale = useInitializationWizardStore((state) => state.setLocale)
  const setAiCredential = useInitializationWizardStore((state) => state.setAiCredential)
  const setLlmModelName = useInitializationWizardStore((state) => state.setLlmModelName)
  const mergeDefaultEntryTypes = useInitializationWizardStore((state) => state.mergeDefaultEntryTypes)
  const addCustomEntryType = useInitializationWizardStore((state) => state.addCustomEntryType)
  const updateEntryType = useInitializationWizardStore((state) => state.updateEntryType)
  const removeEntryType = useInitializationWizardStore((state) => state.removeEntryType)
  const hydrateCapabilityDefaults = useInitializationWizardStore((state) => state.hydrateCapabilityDefaults)
  const setActiveCapabilityGroup = useInitializationWizardStore((state) => state.setActiveCapabilityGroup)
  const setCapabilitySkipped = useInitializationWizardStore((state) => state.setCapabilitySkipped)
  const updateRuntimeConfigGroup = useInitializationWizardStore((state) => state.updateRuntimeConfigGroup)
  const resetDraft = useInitializationWizardStore((state) => state.resetDraft)
  const defaultsQuery = useInitializationDefaultsQuery(locale)

  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [discoverError, setDiscoverError] = useState<string | null>(null)

  useEffect(() => {
    if (statusQuery.data?.initialized) {
      navigate('/dashboard', { replace: true })
    }
  }, [navigate, statusQuery.data])

  useEffect(() => {
    if (!defaultsQuery.data) return
    mergeDefaultEntryTypes(defaultsQuery.data.entryTypes, locale)
    hydrateCapabilityDefaults(defaultsQuery.data.capabilityModules, defaultsQuery.data.runtimeConfig)
  }, [defaultsQuery.data, hydrateCapabilityDefaults, locale, mergeDefaultEntryTypes])

  const handleLocaleSelect = async (nextLocale: Locale) => {
    setDraftLocale(nextLocale)
    setAppLocale(nextLocale, { manual: false })
    await i18n.changeLanguage(nextLocale)
  }

  const handleDiscoverModels = async () => {
    if (!aiCredential.baseUrl.trim() || !aiCredential.apiKey.trim()) {
      toast.error(t('initialization.validation.discover'))
      return
    }

    setIsDiscovering(true)
    setDiscoverError(null)
    try {
      const result = await discoverModelsByKey(aiCredential.baseUrl.trim(), aiCredential.apiKey.trim())
      if (!result.ok) {
        setDiscoveredModels([])
        setDiscoverError(result.message || t('initialization.ai.discoverFailed'))
        return
      }
      const llmCandidates = result.models
        .filter((item) => item.suggestedType === 'llm')
        .map((item) => item.name)
      setDiscoveredModels(llmCandidates)
      if (!llmCandidates.length) {
        setDiscoverError(t('initialization.ai.discoverEmpty'))
      } else if (!llmModelName.trim()) {
        setLlmModelName(llmCandidates[0])
      }
    } catch (error) {
      setDiscoveredModels([])
      setDiscoverError(error instanceof Error ? error.message : t('initialization.ai.discoverFailed'))
    } finally {
      setIsDiscovering(false)
    }
  }

  const canContinueFromCurrentStep = () => {
    if (step === 0) return true
    if (step === 1) {
      return Boolean(
        aiCredential.name.trim() &&
        aiCredential.baseUrl.trim() &&
        aiCredential.apiKey.trim() &&
        llmModelName.trim()
      )
    }
    if (step === 2) {
      return entryTypes.length > 0 && entryTypes.every((item) => item.name.trim())
    }
    if (step === 3 && activeCapabilityGroup) {
      return false
    }
    return true
  }

  const handleNext = () => {
    if (!canContinueFromCurrentStep()) {
      toast.error(t(`initialization.validation.step${step + 1}`))
      return
    }
    setStep(step + 1)
  }

  const handleFinish = async () => {
    if (step < STEP_KEYS.length - 1) {
      return
    }

    try {
      const storeState = useInitializationWizardStore.getState()
      const result = await initializeMutation.mutateAsync({
        locale,
        aiCredential: {
          name: aiCredential.name.trim(),
          baseUrl: aiCredential.baseUrl.trim(),
          apiKey: aiCredential.apiKey.trim(),
        },
        llmModel: {
          name: llmModelName.trim(),
        },
        entryTypes: entryTypes.map((item) => ({
          code: item.origin === 'default' ? item.code : undefined,
          name: item.name.trim(),
          description: item.description?.trim() || undefined,
          color: item.color?.trim() || undefined,
          icon: item.icon?.trim() || undefined,
          graphEnabled: item.graphEnabled,
          aiEnabled: item.aiEnabled,
          enabled: item.enabled,
          origin: item.origin,
        })),
        runtimeConfig: buildRuntimeConfigPayload(storeState),
      })

      resetDraft(result.locale)
      setAppLocale(result.locale, { manual: true })
      await i18n.changeLanguage(result.locale)
      setPersistedInitializationStatus({
        initialized: true,
        locale: result.locale,
      })
      queryClient.setQueryData(initializationKeys.status, {
        initialized: true,
        legacyAutoCompleted: false,
        locale: result.locale,
      })
      await queryClient.invalidateQueries({ queryKey: initializationKeys.status })
      toast.success(t('initialization.success'))
      navigate('/dashboard', { replace: true })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('initialization.submitError'))
    }
  }

  const summaryItems = [
    {
      key: 'locale',
      title: t('initialization.review.cards.language'),
      value: locale === 'zh' ? '中文' : 'English',
      icon: Languages,
    },
    {
      key: 'model',
      title: t('initialization.review.cards.model'),
      value: llmModelName || t('initialization.review.emptyValue'),
      icon: Sparkles,
    },
    {
      key: 'binding',
      title: t('initialization.review.cards.binding'),
      value: t('initialization.review.bindingValue'),
      icon: ShieldCheck,
    },
    {
      key: 'entryTypes',
      title: t('initialization.review.cards.entryTypes'),
      value: String(entryTypes.length),
      icon: Wand2,
    },
  ]

  const capabilitySummaries = useMemo(
    () =>
      capabilityModules.map((module) => ({
        ...module,
        skipped: skippedCapabilityGroups.includes(module.groupKey),
      })),
    [capabilityModules, skippedCapabilityGroups]
  )

  let content: ReactNode
  if (step === 0) {
    content = <LanguageStep locale={locale} onSelect={handleLocaleSelect} />
  } else if (step === 1) {
    content = (
      <div className="space-y-6">
        <div className="space-y-2">
          <h2 className="text-2xl font-semibold text-slate-900">
            {t('initialization.steps.ai.title')}
          </h2>
          <p className="max-w-2xl text-sm leading-6 text-slate-600">
            {t('initialization.steps.ai.description')}
          </p>
        </div>

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {PROVIDER_PRESETS.map((preset) => (
            <button
              key={preset.key}
              type="button"
              onClick={() => setAiCredential({ name: preset.name, baseUrl: preset.baseUrl })}
              className={cn(
                'rounded-[24px] border bg-gradient-to-br p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md',
                preset.className
              )}
            >
              <p className="text-sm font-semibold text-slate-900">{preset.label}</p>
              <p className="mt-2 text-xs leading-5 text-slate-600">{preset.baseUrl}</p>
            </button>
          ))}
        </div>

        <div className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <div className="grid gap-5">
            <div className="grid gap-5 md:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('initialization.ai.fields.providerName')}</Label>
                <input
                  value={aiCredential.name}
                  onChange={(event) => setAiCredential({ name: event.target.value })}
                  className={FIELD_CLASSNAME}
                  placeholder={t('initialization.ai.placeholders.providerName')}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('initialization.ai.fields.baseUrl')}</Label>
                <input
                  value={aiCredential.baseUrl}
                  onChange={(event) => setAiCredential({ baseUrl: event.target.value })}
                  className={FIELD_CLASSNAME}
                  placeholder="https://api.openai.com/v1"
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t('initialization.ai.fields.apiKey')}</Label>
              <input
                type="password"
                value={aiCredential.apiKey}
                onChange={(event) => setAiCredential({ apiKey: event.target.value })}
                className={FIELD_CLASSNAME}
                placeholder="sk-..."
              />
            </div>

            <div className="rounded-[24px] border border-slate-200 bg-slate-50/80 p-4">
              <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                <div>
                  <p className="text-sm font-semibold text-slate-900">
                    {t('initialization.ai.discoverTitle')}
                  </p>
                  <p className="mt-1 text-sm leading-6 text-slate-600">
                    {t('initialization.ai.discoverDescription')}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    void handleDiscoverModels()
                  }}
                  disabled={isDiscovering}
                  className="rounded-2xl"
                >
                  {isDiscovering ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {t('initialization.ai.discoverAction')}
                </Button>
              </div>

              {discoverError ? (
                <p className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-700">
                  {discoverError}
                </p>
              ) : null}

              {discoveredModels.length ? (
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  {discoveredModels.map((model) => (
                    <button
                      key={model}
                      type="button"
                      onClick={() => setLlmModelName(model)}
                      className={cn(
                        'rounded-2xl border px-4 py-3 text-left text-sm transition-all',
                        llmModelName === model
                          ? 'border-slate-900 bg-slate-900 text-white shadow-md shadow-slate-900/10'
                          : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
                      )}
                    >
                      {model}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="space-y-2">
              <Label>{t('initialization.ai.fields.modelName')}</Label>
              <input
                value={llmModelName}
                onChange={(event) => setLlmModelName(event.target.value)}
                className={FIELD_CLASSNAME}
                placeholder={t('initialization.ai.placeholders.modelName')}
              />
            </div>
          </div>
        </div>
      </div>
    )
  } else if (step === 2) {
    content = (
      <div className="space-y-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <h2 className="text-2xl font-semibold text-slate-900">
              {t('initialization.steps.entryTypes.title')}
            </h2>
            <p className="max-w-2xl text-sm leading-6 text-slate-600">
              {t('initialization.steps.entryTypes.description')}
            </p>
          </div>
          <Button type="button" onClick={addCustomEntryType} className="rounded-2xl">
            <Plus className="h-4 w-4" />
            {t('initialization.entryTypes.addCustom')}
          </Button>
        </div>

        {defaultsQuery.isLoading && !entryTypes.length ? (
          <div className="rounded-[24px] border border-slate-200 bg-white px-6 py-8 text-center text-sm text-slate-500">
            <Loader2 className="mx-auto h-5 w-5 animate-spin text-slate-400" />
            <p className="mt-3">{t('initialization.entryTypes.loading')}</p>
          </div>
        ) : null}

        <div className="space-y-4">
          {entryTypes.map((item) => (
            <EntryTypeCard
              key={item.draftId}
              item={item}
              onUpdate={(patch) => updateEntryType(item.draftId, patch)}
              onRemove={() => removeEntryType(item.draftId)}
            />
          ))}
        </div>
      </div>
    )
  } else if (step === 3) {
    if (activeCapabilityGroup) {
      const module = capabilityModules.find((item) => item.groupKey === activeCapabilityGroup)
      const currentSkipped = skippedCapabilityGroups.includes(activeCapabilityGroup)

      let moduleTitle = ''
      let moduleDescription = ''
      let editor: ReactNode = null

      if (activeCapabilityGroup === 'storage') {
        moduleTitle = module?.title || t('systemSetup.moduleTitles.storage')
        moduleDescription = module?.description || t('systemSetup.moduleDescriptions.storage')
        editor = (
          <StorageCapabilityFields
            value={runtimeConfigDraft.storage}
            onChange={(patch) => updateRuntimeConfigGroup('storage', patch)}
            t={t}
          />
        )
      } else if (activeCapabilityGroup === 'knowledge_graph') {
        moduleTitle = module?.title || t('systemSetup.moduleTitles.knowledgeGraph')
        moduleDescription = module?.description || t('systemSetup.moduleDescriptions.knowledgeGraph')
        editor = (
          <KnowledgeGraphCapabilityFields
            value={runtimeConfigDraft.knowledgeGraph}
            onChange={(patch) => updateRuntimeConfigGroup('knowledge_graph', patch)}
            t={t}
          />
        )
      } else if (activeCapabilityGroup === 'document_parsing') {
        moduleTitle = module?.title || t('systemSetup.moduleTitles.documentParsing')
        moduleDescription = module?.description || t('systemSetup.moduleDescriptions.documentParsing')
        editor = (
          <DocumentParsingCapabilityFields
            value={runtimeConfigDraft.documentParsing}
            onChange={(patch) => updateRuntimeConfigGroup('document_parsing', patch)}
            t={t}
          />
        )
      } else if (activeCapabilityGroup === 'automation') {
        moduleTitle = module?.title || t('systemSetup.moduleTitles.automation')
        moduleDescription = module?.description || t('systemSetup.moduleDescriptions.automation')
        editor = (
          <AutomationCapabilityFields
            value={runtimeConfigDraft.automation}
            onChange={(patch) => updateRuntimeConfigGroup('automation', patch)}
            t={t}
          />
        )
      }

      content = (
        <div className="space-y-6">
          <button
            type="button"
            onClick={() => setActiveCapabilityGroup(null)}
            className="inline-flex items-center gap-2 text-sm font-medium text-slate-500 transition hover:text-slate-900"
          >
            <ArrowLeft className="h-4 w-4" />
            {t('initialization.capabilities.backToCenter')}
          </button>

          <div className="space-y-2">
            <h2 className="text-2xl font-semibold text-slate-900">{moduleTitle}</h2>
            <p className="max-w-2xl text-sm leading-6 text-slate-600">{moduleDescription}</p>
          </div>

          {module ? (
            <RuntimeCapabilityMeta
              module={module}
              skipped={currentSkipped}
              t={t}
            />
          ) : null}

          <div className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            {editor}
          </div>
        </div>
      )
    } else {
      content = (
        <div className="space-y-6">
          <div className="space-y-2">
            <h2 className="text-2xl font-semibold text-slate-900">
              {t('initialization.steps.capabilities.title')}
            </h2>
            <p className="max-w-2xl text-sm leading-6 text-slate-600">
              {t('initialization.steps.capabilities.description')}
            </p>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-slate-50/70 p-5">
            <div className="flex items-start gap-3">
              <div className="rounded-2xl bg-white p-2 text-slate-700 shadow-sm">
                <ShieldCheck className="h-5 w-5" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-semibold text-slate-900">
                  {t('initialization.capabilities.introTitle')}
                </p>
                <p className="text-sm leading-6 text-slate-600">
                  {t('initialization.capabilities.introDescription')}
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-4">
            {capabilityModules.map((module) => {
              const skipped = skippedCapabilityGroups.includes(module.groupKey)
              const currentDraft =
                module.groupKey === 'storage'
                  ? runtimeConfigDraft.storage
                  : module.groupKey === 'knowledge_graph'
                    ? runtimeConfigDraft.knowledgeGraph
                    : module.groupKey === 'document_parsing'
                      ? runtimeConfigDraft.documentParsing
                      : runtimeConfigDraft.automation
              const status = getRuntimeCapabilityStatus(module, skipped, t)

              return (
                <CapabilityOverviewCard
                  key={module.groupKey}
                  title={module.title}
                  description={module.description}
                  summary={currentDraft.effectiveSummary || status.label}
                  meta={<RuntimeCapabilityMeta module={module} skipped={skipped} t={t} />}
                  onConfigure={() => setActiveCapabilityGroup(module.groupKey)}
                  onSkip={() => setCapabilitySkipped(module.groupKey, true)}
                  onUndoSkip={() => setCapabilitySkipped(module.groupKey, false)}
                  skipped={skipped}
                />
              )
            })}
          </div>
        </div>
      )
    }
  } else {
    content = (
      <div className="space-y-6">
        <div className="space-y-2">
          <h2 className="text-2xl font-semibold text-slate-900">
            {t('initialization.steps.review.title')}
          </h2>
          <p className="max-w-2xl text-sm leading-6 text-slate-600">
            {t('initialization.steps.review.description')}
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {summaryItems.map((item) => (
            <div key={item.key} className="rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex items-start gap-4">
                <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
                  <item.icon className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm text-slate-500">{item.title}</p>
                  <p className="mt-2 break-words text-base font-semibold text-slate-900">{item.value}</p>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="space-y-4">
            <div className="space-y-1">
              <h3 className="text-lg font-semibold text-slate-900">
                {t('initialization.review.capabilitiesTitle')}
              </h3>
              <p className="text-sm leading-6 text-slate-600">
                {t('initialization.review.capabilitiesDescription')}
              </p>
            </div>

            <div className="space-y-3">
              {capabilitySummaries.map((module) => (
                <div key={module.groupKey} className="flex flex-col gap-3 rounded-[22px] border border-slate-200 bg-slate-50/70 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">{module.title}</p>
                    <p className="mt-1 text-sm leading-6 text-slate-600">{module.effectiveSummary || module.description}</p>
                  </div>
                  <RuntimeCapabilityMeta module={module} skipped={module.skipped} t={t} />
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="rounded-[24px] border border-sky-200 bg-sky-50/80 p-5">
          <div className="flex items-start gap-3">
            <div className="rounded-2xl bg-white p-2 text-sky-600 shadow-sm">
              <Rocket className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-sky-900">
                {t('initialization.review.noteTitle')}
              </p>
              <p className="mt-1 text-sm leading-6 text-sky-800">
                {t('initialization.review.noteDescription')}
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const stepLabel = step === 3 && activeCapabilityGroup
    ? t('initialization.capabilities.editing')
    : t(`initialization.steps.${STEP_KEYS[step]}.label`)

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(59,130,246,0.18),_transparent_32%),radial-gradient(circle_at_top_right,_rgba(15,23,42,0.08),_transparent_28%),linear-gradient(180deg,_#f8fbff,_#f5f7fb_45%,_#eef2f9)] px-4 py-6 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-6xl">
        <div className="overflow-hidden rounded-[32px] border border-white/70 bg-white/88 shadow-[0_40px_120px_rgba(15,23,42,0.14)] backdrop-blur-xl">
          <div className="border-b border-slate-200/80 px-6 py-6 sm:px-8">
            <StepIndicator currentStep={step} />
          </div>

          <div className="px-6 py-8 sm:px-8">
            <div className="mx-auto max-w-3xl">
              {content}
            </div>
          </div>

          <div className="sticky bottom-0 border-t border-slate-200/80 bg-white/95 px-6 py-4 backdrop-blur sm:px-8">
            <div className="mx-auto flex max-w-3xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="text-sm text-slate-500">{stepLabel}</div>

              <div className="flex flex-wrap items-center gap-3">
                {step === 3 && activeCapabilityGroup ? (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setActiveCapabilityGroup(null)}
                      className="rounded-2xl"
                    >
                      <ChevronLeft className="h-4 w-4" />
                      {t('initialization.capabilities.saveAndBack')}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setCapabilitySkipped(activeCapabilityGroup, true)
                        setActiveCapabilityGroup(null)
                      }}
                      className="rounded-2xl"
                    >
                      {t('initialization.capabilities.skipCurrent')}
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setStep(step - 1)}
                      disabled={step === 0 || initializeMutation.isPending}
                      className="rounded-2xl"
                    >
                      <ChevronLeft className="h-4 w-4" />
                      {t('initialization.actions.back')}
                    </Button>

                    {step < STEP_KEYS.length - 1 ? (
                      <Button type="button" onClick={handleNext} className="rounded-2xl">
                        {t('initialization.actions.next')}
                        <ChevronRight className="h-4 w-4" />
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        onClick={() => {
                          void handleFinish()
                        }}
                        disabled={initializeMutation.isPending}
                        className="rounded-2xl"
                      >
                        {initializeMutation.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4" />
                        )}
                        {t('initialization.actions.finish')}
                      </Button>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
