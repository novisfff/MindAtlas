import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Languages,
  Loader2,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wand2,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Logo } from '@/components/Logo'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { initializationKeys, useInitializationDefaultsQuery, useInitializationStatusQuery, useInitializeSystemMutation } from '../queries'
import {
  setPersistedInitializationStatus,
  useInitializationWizardStore,
  type InitializationDraftEntryType,
} from '../store'
import { useAppStore, type Locale } from '@/stores/app-store'
import { discoverModelsByKey } from '@/features/ai-providers/api/credentials'

const STEP_KEYS = ['language', 'intro', 'ai', 'entryTypes', 'capabilities', 'review'] as const

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

const DEFAULT_EMBEDDING_MODELS = [
  'text-embedding-3-small',
  'text-embedding-3-large',
  'text-embedding-ada-002',
] as const

const OCR_LANGUAGE_OPTIONS = ['auto', 'zh', 'en', 'zh,en'] as const
const RERANK_REQUEST_FORMAT_OPTIONS = ['standard', 'aliyun'] as const

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
        <div className="grid gap-3 md:grid-cols-6">
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
    <div className="rounded-[22px] border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  'inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-medium',
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
            <p className="text-xs leading-5 text-slate-500">
              {item.origin === 'default'
                ? t('initialization.entryTypes.defaultHint')
                : t('initialization.entryTypes.customHint')}
            </p>
          </div>

          <button
            type="button"
            onClick={onRemove}
            className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 text-slate-500 transition hover:border-red-200 hover:bg-red-50 hover:text-red-600"
            aria-label={t('initialization.entryTypes.remove')}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>

        <div className="grid gap-3 md:grid-cols-[minmax(0,1.15fr)_minmax(0,0.9fr)_auto]">
          <div className="space-y-1.5">
            <Label>{t('initialization.entryTypes.fields.name')}</Label>
            <input
              value={item.name}
              onChange={(event) => onUpdate({ name: event.target.value })}
              className={FIELD_CLASSNAME}
              placeholder={t('initialization.entryTypes.placeholders.name')}
            />
          </div>

          <div className="space-y-1.5">
            <Label>{t('initialization.entryTypes.fields.icon')}</Label>
            <input
              value={item.icon || ''}
              onChange={(event) => onUpdate({ icon: event.target.value })}
              className={FIELD_CLASSNAME}
              placeholder={t('initialization.entryTypes.placeholders.icon')}
            />
          </div>

          <div className="space-y-1.5">
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

        <div className="space-y-1.5">
          <Label>{t('initialization.entryTypes.fields.description')}</Label>
          <textarea
            value={item.description || ''}
            onChange={(event) => onUpdate({ description: event.target.value })}
            className={TEXTAREA_CLASSNAME}
            rows={2}
            placeholder={t('initialization.entryTypes.placeholders.description')}
          />
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

function IntroStep({
  lightRagEnabled,
  doclingEnabled,
}: {
  lightRagEnabled: boolean
  doclingEnabled: boolean
}) {
  const { t } = useTranslation()
  const nextItems = [
    {
      key: 'ai',
      title: t('initialization.intro.highlights.ai.title'),
      description: t('initialization.intro.highlights.ai.description'),
      icon: Sparkles,
    },
    {
      key: 'entryTypes',
      title: t('initialization.intro.highlights.entryTypes.title'),
      description: t('initialization.intro.highlights.entryTypes.description'),
      icon: Wand2,
    },
    {
      key: 'capabilities',
      title: t('initialization.intro.highlights.capabilities.title'),
      description: t('initialization.intro.highlights.capabilities.description'),
      icon: ShieldCheck,
    },
  ]
  const statusCards = [
    {
      key: 'lightrag',
      title: 'LightRAG',
      description: t('initialization.intro.cards.lightrag.description'),
      icon: Sparkles,
      enabled: lightRagEnabled,
    },
    {
      key: 'docling',
      title: 'Docling',
      description: t('initialization.intro.cards.docling.description'),
      icon: ShieldCheck,
      enabled: doclingEnabled,
    },
  ]

  return (
    <div className="space-y-6">
      <div className="overflow-hidden rounded-[32px] border border-slate-900/10 bg-[radial-gradient(circle_at_top_right,_rgba(56,189,248,0.18),_transparent_32%),linear-gradient(135deg,_#0f172a,_#111827_52%,_#1e293b)] p-6 text-white shadow-[0_28px_70px_rgba(15,23,42,0.18)]">
        <div className="inline-flex items-center rounded-full border border-white/15 bg-white/10 px-3 py-1 text-xs font-medium tracking-[0.16em] text-white/80">
          {t('initialization.intro.badge')}
        </div>

        <div className="mt-5 flex flex-col gap-5">
          <div className="flex items-start gap-4">
            <div className="rounded-[24px] border border-white/10 bg-white/10 p-3.5 shadow-lg shadow-black/10">
              <Logo className="h-8 w-8" />
            </div>
            <div className="min-w-0 space-y-2">
              <h2 className="text-2xl font-semibold tracking-tight text-white">
                {t('initialization.steps.intro.title')}
              </h2>
              <p className="max-w-2xl text-sm leading-6 text-slate-200">
                {t('initialization.steps.intro.description')}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {['record', 'connect', 'analyze'].map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center rounded-full border border-white/10 bg-white/8 px-3 py-1.5 text-xs font-medium text-white/85"
              >
                {t(`initialization.intro.tags.${tag}`)}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-2">
          <p className="text-sm font-semibold text-slate-900">
            {t('initialization.intro.systemTitle')}
          </p>
          <p className="text-sm leading-6 text-slate-600">
            {t('initialization.intro.systemDescription')}
          </p>
        </div>

        <div className="mt-5 rounded-[24px] border border-slate-200 bg-slate-50/80 p-4">
          <div className="space-y-1">
            <p className="text-sm font-semibold text-slate-900">
              {t('initialization.intro.setupTitle')}
            </p>
            <p className="text-sm leading-6 text-slate-600">
              {t('initialization.intro.setupDescription')}
            </p>
          </div>

          <div className="mt-4 space-y-3">
            {nextItems.map((item, index) => (
              <div
                key={item.key}
                className="flex items-start gap-4 rounded-[20px] bg-white px-4 py-4 shadow-sm ring-1 ring-slate-100"
              >
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-slate-900 text-sm font-semibold text-white">
                  {index + 1}
                </div>
                <div className="flex min-w-0 gap-3">
                  <div className="rounded-2xl bg-slate-100 p-2.5 text-slate-700">
                    <item.icon className="h-4 w-4" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                    <p className="mt-1 text-sm leading-6 text-slate-600">{item.description}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div className="space-y-1">
            <p className="text-sm font-semibold text-slate-900">
              {t('initialization.intro.statusTitle')}
            </p>
            <p className="text-sm leading-6 text-slate-600">
              {t('initialization.intro.statusDescription')}
            </p>
          </div>
          <span className="inline-flex items-center rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
            {t('initialization.intro.statusSource')}
          </span>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {statusCards.map((card) => (
            <div
              key={card.key}
              className={cn(
                'rounded-[24px] border p-5 shadow-sm transition-colors',
                card.enabled
                  ? 'border-emerald-200/90 bg-emerald-50/70'
                  : 'border-slate-200 bg-white'
              )}
            >
              <div className="flex items-start gap-4">
                <div
                  className={cn(
                    'rounded-2xl p-3',
                    card.enabled
                      ? 'bg-emerald-100 text-emerald-700'
                      : 'bg-slate-100 text-slate-500'
                  )}
                >
                  <card.icon className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-base font-semibold text-slate-900">{card.title}</p>
                    <span
                      className={cn(
                        'shrink-0 rounded-full px-3 py-1 text-xs font-medium',
                        card.enabled
                          ? 'bg-white text-emerald-700 ring-1 ring-emerald-200'
                          : 'bg-slate-100 text-slate-600'
                      )}
                    >
                      {card.enabled
                        ? t('settings.skills.enabledStateOn')
                        : t('settings.skills.enabledStateOff')}
                    </span>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{card.description}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-[24px] border border-amber-200/80 bg-amber-50/85 px-5 py-4">
        <p className="text-sm font-semibold text-amber-900">
          {t('initialization.intro.noteTitle')}
        </p>
        <p className="mt-1 text-sm leading-6 text-amber-800">
          {t('initialization.intro.note')}
        </p>
      </div>
    </div>
  )
}

function OptionButtonGroup({
  options,
  value,
  onChange,
}: {
  options: Array<{ value: string; label: string }>
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={cn(
            'inline-flex items-center rounded-full border px-3 py-1.5 text-sm transition',
            value === option.value
              ? 'border-slate-900 bg-slate-900 text-white shadow-sm'
              : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900'
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function resolveRerankMode(knowledgeGraph: ReturnType<typeof useInitializationWizardStore.getState>['runtimeConfigDraft']['knowledgeGraph']) {
  return knowledgeGraph.rerankModel.trim() ||
    knowledgeGraph.rerankHost.trim() ||
    knowledgeGraph.rerankApiKey.trim() ||
    knowledgeGraph.rerankApiKeyState.configured
    ? 'enabled'
    : 'disabled'
}

function buildRuntimeConfigPayload(
  state: ReturnType<typeof useInitializationWizardStore.getState>,
  locale: Locale
) {
  const knowledgeGraphPayload: Record<string, unknown> = {}
  const documentParsingPayload: Record<string, unknown> = {}

  const knowledgeGraphEnabled = state.runtimeConfigDraft.knowledgeGraph.enabled
  const ocrConfigEnabled =
    state.runtimeConfigDraft.documentParsing.workerEnabled &&
    state.runtimeConfigDraft.documentParsing.ocrEnabled
  const pictureDescriptionEnabled = state.runtimeConfigDraft.documentParsing.pictureDescriptionEnabled
  const summaryLanguage =
    state.runtimeConfigDraft.knowledgeGraph.summaryLanguage.trim() ||
    (locale === 'zh' ? 'Chinese' : 'English')
  const embeddingModelName = (state.runtimeConfigDraft.knowledgeGraph.embeddingModelName || '').trim()
  const embeddingHost = state.runtimeConfigDraft.knowledgeGraph.embeddingHost.trim()
  const embeddingApiKey = state.runtimeConfigDraft.knowledgeGraph.embeddingApiKey.trim()
  const rerankModel = state.runtimeConfigDraft.knowledgeGraph.rerankModel.trim()
  const rerankHost = state.runtimeConfigDraft.knowledgeGraph.rerankHost.trim()
  const rerankApiKey = state.runtimeConfigDraft.knowledgeGraph.rerankApiKey.trim()
  const rerankRequestFormat = state.runtimeConfigDraft.knowledgeGraph.rerankRequestFormat.trim()
  const ocrLangs = state.runtimeConfigDraft.documentParsing.ocrLangs.trim()
  const pictureDescriptionUrl = state.runtimeConfigDraft.documentParsing.pictureDescriptionUrl.trim()
  const pictureDescriptionApiKey = state.runtimeConfigDraft.documentParsing.pictureDescriptionApiKey.trim()
  const pictureDescriptionModel = state.runtimeConfigDraft.documentParsing.pictureDescriptionModel.trim()
  const pictureDescriptionPrompt = state.runtimeConfigDraft.documentParsing.pictureDescriptionPrompt.trim()

  if (knowledgeGraphEnabled) {
    knowledgeGraphPayload.summaryLanguage = summaryLanguage
  }
  if (knowledgeGraphEnabled && embeddingModelName) {
    knowledgeGraphPayload.embeddingModelName = embeddingModelName
  }
  if (knowledgeGraphEnabled && embeddingHost) {
    knowledgeGraphPayload.embeddingHost = embeddingHost
  }
  if (knowledgeGraphEnabled && embeddingApiKey) {
    knowledgeGraphPayload.embeddingApiKey = embeddingApiKey
  }
  if (knowledgeGraphEnabled && rerankModel) {
    knowledgeGraphPayload.rerankModel = rerankModel
  }
  if (knowledgeGraphEnabled && rerankHost) {
    knowledgeGraphPayload.rerankHost = rerankHost
  }
  if (knowledgeGraphEnabled && rerankApiKey) {
    knowledgeGraphPayload.rerankApiKey = rerankApiKey
  }
  if (knowledgeGraphEnabled && (rerankModel || rerankHost || rerankApiKey) && rerankRequestFormat) {
    knowledgeGraphPayload.rerankRequestFormat = rerankRequestFormat
  }
  if (ocrConfigEnabled && ocrLangs && ocrLangs !== 'auto') {
    documentParsingPayload.ocrLangs = ocrLangs
  }
  if (pictureDescriptionEnabled !== undefined) {
    documentParsingPayload.pictureDescriptionEnabled = pictureDescriptionEnabled
  }
  if (pictureDescriptionEnabled && pictureDescriptionUrl) {
    documentParsingPayload.pictureDescriptionUrl = pictureDescriptionUrl
  }
  if (pictureDescriptionEnabled && pictureDescriptionApiKey) {
    documentParsingPayload.pictureDescriptionApiKey = pictureDescriptionApiKey
  }
  if (pictureDescriptionEnabled && pictureDescriptionModel) {
    documentParsingPayload.pictureDescriptionModel = pictureDescriptionModel
  }
  if (pictureDescriptionEnabled && pictureDescriptionPrompt) {
    documentParsingPayload.pictureDescriptionPrompt = pictureDescriptionPrompt
  }

  const payload: Record<string, unknown> = {}
  if (Object.keys(knowledgeGraphPayload).length) {
    payload.knowledgeGraph = knowledgeGraphPayload
  }
  if (Object.keys(documentParsingPayload).length) {
    payload.documentParsing = documentParsingPayload
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
  const updateRuntimeConfigGroup = useInitializationWizardStore((state) => state.updateRuntimeConfigGroup)
  const resetDraft = useInitializationWizardStore((state) => state.resetDraft)
  const defaultsQuery = useInitializationDefaultsQuery(locale)

  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [discoveredEmbeddingModels, setDiscoveredEmbeddingModels] = useState<string[]>([])
  const [discoverError, setDiscoverError] = useState<string | null>(null)
  const [rerankMode, setRerankMode] = useState<'enabled' | 'disabled'>(() => {
    return resolveRerankMode(useInitializationWizardStore.getState().runtimeConfigDraft.knowledgeGraph)
  })

  useEffect(() => {
    if (statusQuery.data?.initialized) {
      navigate('/dashboard', { replace: true })
    }
  }, [navigate, statusQuery.data])

  useEffect(() => {
    if (!defaultsQuery.data) return
    mergeDefaultEntryTypes(defaultsQuery.data.entryTypes, locale)
    hydrateCapabilityDefaults(defaultsQuery.data.capabilityModules, defaultsQuery.data.runtimeConfig)
    const nextKnowledgeGraph = useInitializationWizardStore.getState().runtimeConfigDraft.knowledgeGraph
    setRerankMode(resolveRerankMode(nextKnowledgeGraph))
  }, [defaultsQuery.data, hydrateCapabilityDefaults, locale, mergeDefaultEntryTypes])

  const knowledgeGraphEnabled = runtimeConfigDraft.knowledgeGraph.enabled
  const doclingVisible =
    runtimeConfigDraft.documentParsing.workerEnabled ||
    runtimeConfigDraft.documentParsing.pictureDescriptionEnabled
  const ocrConfigEnabled =
    runtimeConfigDraft.documentParsing.workerEnabled &&
    runtimeConfigDraft.documentParsing.ocrEnabled
  const pictureDescriptionEnabled = runtimeConfigDraft.documentParsing.pictureDescriptionEnabled

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
        setDiscoveredEmbeddingModels([])
        setDiscoverError(result.message || t('initialization.ai.discoverFailed'))
        return
      }
      const llmCandidates = result.models
        .filter((item) => item.suggestedType === 'llm')
        .map((item) => item.name)
      const embeddingCandidates = result.models
        .filter((item) => item.suggestedType === 'embedding')
        .map((item) => item.name)
      setDiscoveredModels(llmCandidates)
      setDiscoveredEmbeddingModels(embeddingCandidates)
      if (!llmCandidates.length) {
        setDiscoverError(t('initialization.ai.discoverEmpty'))
      } else if (!llmModelName.trim()) {
        setLlmModelName(llmCandidates[0])
      }
      if (
        embeddingCandidates.length &&
        !useInitializationWizardStore.getState().runtimeConfigDraft.knowledgeGraph.embeddingModelName?.trim()
      ) {
        updateRuntimeConfigGroup('knowledge_graph', { embeddingModelName: embeddingCandidates[0] })
      }
    } catch (error) {
      setDiscoveredModels([])
      setDiscoveredEmbeddingModels([])
      setDiscoverError(error instanceof Error ? error.message : t('initialization.ai.discoverFailed'))
    } finally {
      setIsDiscovering(false)
    }
  }

  const canContinueFromCurrentStep = () => {
    if (step === 0) return true
    if (step === 1) {
      return true
    }
    if (step === 2) {
      return Boolean(
        aiCredential.name.trim() &&
        aiCredential.baseUrl.trim() &&
        aiCredential.apiKey.trim() &&
        llmModelName.trim()
      )
    }
    if (step === 3) {
      return entryTypes.length > 0 && entryTypes.every((item) => item.name.trim())
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
          graphEnabled: true,
          aiEnabled: true,
          enabled: true,
          origin: item.origin,
        })),
        runtimeConfig: buildRuntimeConfigPayload(storeState, locale),
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

  const selectedLlmModelName = llmModelName.trim()
  const doclingEnabledForSummary = runtimeConfigDraft.documentParsing.workerEnabled
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
      value: selectedLlmModelName || t('initialization.review.emptyValue'),
      icon: Sparkles,
    },
    {
      key: 'binding',
      title: t('initialization.review.cards.binding'),
      value: t('initialization.review.bindingValue'),
      icon: ShieldCheck,
    },
    {
      key: 'lightrag',
      title: t('initialization.review.cards.lightrag'),
      value: knowledgeGraphEnabled ? t('settings.skills.enabledStateOn') : t('settings.skills.enabledStateOff'),
      icon: Sparkles,
    },
    {
      key: 'docling',
      title: t('initialization.review.cards.docling'),
      value: doclingEnabledForSummary ? t('settings.skills.enabledStateOn') : t('settings.skills.enabledStateOff'),
      icon: ShieldCheck,
    },
    {
      key: 'entryTypes',
      title: t('initialization.review.cards.entryTypes'),
      value: String(entryTypes.length),
      icon: Wand2,
    },
  ]
  const defaultSummaryLanguage = locale === 'zh' ? 'Chinese' : 'English'
  const summaryLanguageValue =
    runtimeConfigDraft.knowledgeGraph.summaryLanguage.trim() || defaultSummaryLanguage
  const rerankEnabled = rerankMode === 'enabled'

  let content: ReactNode
  if (step === 0) {
    content = <LanguageStep locale={locale} onSelect={handleLocaleSelect} />
  } else if (step === 1) {
    content = (
      <IntroStep
        lightRagEnabled={runtimeConfigDraft.knowledgeGraph.enabled}
        doclingEnabled={runtimeConfigDraft.documentParsing.workerEnabled}
      />
    )
  } else if (step === 2) {
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
  } else if (step === 3) {
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
  } else if (step === 4) {
    const renderLightRagSection = () => (
      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-5">
          <div className="space-y-2">
            <h3 className="text-xl font-semibold text-slate-900">LightRAG</h3>
            <p className="text-sm leading-6 text-slate-600">
              {t('initialization.capabilities.sections.lightrag.description')}
            </p>
          </div>

          <div className="rounded-[22px] border border-amber-200 bg-amber-50/80 p-4">
            <div className="space-y-2">
              <Label>{t('initialization.ai.lightragLocaleLabel')}</Label>
              <OptionButtonGroup
                value={summaryLanguageValue}
                onChange={(value) =>
                  updateRuntimeConfigGroup('knowledge_graph', { summaryLanguage: value })
                }
                options={[
                  { value: 'Chinese', label: 'Chinese' },
                  { value: 'English', label: 'English' },
                ]}
              />
              <p className="text-xs leading-5 text-amber-700">
                {t('initialization.ai.lightragLocaleHint')}
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <Label>{t('systemSetup.forms.knowledgeGraph.embeddingModelName.label')}</Label>
            <input
              value={runtimeConfigDraft.knowledgeGraph.embeddingModelName || ''}
              onChange={(event) =>
                updateRuntimeConfigGroup('knowledge_graph', { embeddingModelName: event.target.value })
              }
              className={FIELD_CLASSNAME}
              placeholder={t('systemSetup.forms.knowledgeGraph.embeddingModelName.placeholder')}
            />
          </div>

          <div className="rounded-[22px] border border-slate-200 bg-slate-50/70 p-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('systemSetup.forms.knowledgeGraph.embeddingHost.label')}</Label>
                <input
                  value={runtimeConfigDraft.knowledgeGraph.embeddingHost}
                  onChange={(event) =>
                    updateRuntimeConfigGroup('knowledge_graph', { embeddingHost: event.target.value })
                  }
                  className={FIELD_CLASSNAME}
                  placeholder={t('systemSetup.forms.knowledgeGraph.embeddingHost.placeholder')}
                />
              </div>

              <div className="space-y-2">
                <Label>{t('systemSetup.forms.knowledgeGraph.embeddingApiKey.label')}</Label>
                <input
                  type="password"
                  value={runtimeConfigDraft.knowledgeGraph.embeddingApiKey}
                  onChange={(event) =>
                    updateRuntimeConfigGroup('knowledge_graph', { embeddingApiKey: event.target.value })
                  }
                  className={FIELD_CLASSNAME}
                  placeholder={t('systemSetup.forms.knowledgeGraph.embeddingApiKey.placeholder')}
                />
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <Label>{t('initialization.capabilities.rerank.label')}</Label>
            <OptionButtonGroup
              value={rerankMode}
              onChange={(value) => {
                setRerankMode(value as 'enabled' | 'disabled')
                if (value === 'disabled') {
                  updateRuntimeConfigGroup('knowledge_graph', {
                    rerankModel: '',
                    rerankHost: '',
                    rerankApiKey: '',
                    rerankRequestFormat: 'standard',
                  })
                  return
                }
              }}
              options={[
                { value: 'disabled', label: t('initialization.capabilities.rerank.options.disabled') },
                { value: 'enabled', label: t('initialization.capabilities.rerank.options.enabled') },
              ]}
            />
          </div>

          {rerankEnabled ? (
            <div className="rounded-[22px] border border-slate-200 bg-white p-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.knowledgeGraph.rerankModel.label')}</Label>
                  <input
                    value={runtimeConfigDraft.knowledgeGraph.rerankModel}
                    onChange={(event) =>
                      updateRuntimeConfigGroup('knowledge_graph', { rerankModel: event.target.value })
                    }
                    className={FIELD_CLASSNAME}
                    placeholder={t('systemSetup.forms.knowledgeGraph.rerankModel.placeholder')}
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.knowledgeGraph.rerankHost.label')}</Label>
                  <input
                    value={runtimeConfigDraft.knowledgeGraph.rerankHost}
                    onChange={(event) =>
                      updateRuntimeConfigGroup('knowledge_graph', { rerankHost: event.target.value })
                    }
                    className={FIELD_CLASSNAME}
                    placeholder="https://your-rerank-host/v1"
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.knowledgeGraph.rerankApiKey.label')}</Label>
                  <input
                    type="password"
                    value={runtimeConfigDraft.knowledgeGraph.rerankApiKey}
                    onChange={(event) =>
                      updateRuntimeConfigGroup('knowledge_graph', { rerankApiKey: event.target.value })
                    }
                    className={FIELD_CLASSNAME}
                    placeholder={t('systemSetup.forms.knowledgeGraph.rerankApiKey.placeholder')}
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.knowledgeGraph.rerankRequestFormat.label')}</Label>
                  <OptionButtonGroup
                    value={runtimeConfigDraft.knowledgeGraph.rerankRequestFormat}
                    onChange={(value) =>
                      updateRuntimeConfigGroup('knowledge_graph', { rerankRequestFormat: value })
                    }
                    options={RERANK_REQUEST_FORMAT_OPTIONS.map((item) => ({ value: item, label: item }))}
                  />
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    )

    const renderDoclingSection = () => (
      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="space-y-5">
          <div className="space-y-2">
            <h3 className="text-xl font-semibold text-slate-900">Docling</h3>
            <p className="text-sm leading-6 text-slate-600">
              {t('initialization.capabilities.sections.docling.description')}
            </p>
          </div>

          {ocrConfigEnabled ? (
            <div className="space-y-2">
              <Label>{t('systemSetup.forms.documentParsing.ocrLangs.label')}</Label>
              <OptionButtonGroup
                value={runtimeConfigDraft.documentParsing.ocrLangs}
                onChange={(value) => updateRuntimeConfigGroup('document_parsing', { ocrLangs: value })}
                options={OCR_LANGUAGE_OPTIONS.map((item) => ({
                  value: item,
                  label: t(`initialization.capabilities.ocr.options.${item.replace(',', '_')}`),
                }))}
              />
            </div>
          ) : null}

          <div className="space-y-2">
            <Label>{t('systemSetup.forms.documentParsing.pictureDescriptionEnabled.label')}</Label>
            <OptionButtonGroup
              value={pictureDescriptionEnabled ? 'enabled' : 'disabled'}
              onChange={(value) =>
                updateRuntimeConfigGroup('document_parsing', {
                  pictureDescriptionEnabled: value === 'enabled',
                })
              }
              options={[
                { value: 'enabled', label: t('settings.skills.enabledStateOn') },
                { value: 'disabled', label: t('settings.skills.enabledStateOff') },
              ]}
            />
            <p className="text-sm leading-6 text-slate-500">
              {t('systemSetup.forms.documentParsing.pictureDescriptionEnabled.description')}
            </p>
          </div>

          {pictureDescriptionEnabled ? (
            <div className="rounded-[22px] border border-slate-200 bg-slate-50/70 p-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.documentParsing.pictureDescriptionUrl.label')}</Label>
                  <input
                    value={runtimeConfigDraft.documentParsing.pictureDescriptionUrl}
                    onChange={(event) =>
                      updateRuntimeConfigGroup('document_parsing', { pictureDescriptionUrl: event.target.value })
                    }
                    className={FIELD_CLASSNAME}
                    placeholder="https://api.openai.com/v1"
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.documentParsing.pictureDescriptionModel.label')}</Label>
                  <input
                    value={runtimeConfigDraft.documentParsing.pictureDescriptionModel}
                    onChange={(event) =>
                      updateRuntimeConfigGroup('document_parsing', { pictureDescriptionModel: event.target.value })
                    }
                    className={FIELD_CLASSNAME}
                    placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionModel.placeholder')}
                  />
                </div>

                <div className="space-y-2">
                  <Label>{t('systemSetup.forms.documentParsing.pictureDescriptionApiKey.label')}</Label>
                  <input
                    type="password"
                    value={runtimeConfigDraft.documentParsing.pictureDescriptionApiKey}
                    onChange={(event) =>
                      updateRuntimeConfigGroup('document_parsing', { pictureDescriptionApiKey: event.target.value })
                    }
                    className={FIELD_CLASSNAME}
                    placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionApiKey.placeholder')}
                  />
                  {!runtimeConfigDraft.documentParsing.pictureDescriptionApiKey.trim() &&
                  runtimeConfigDraft.documentParsing.pictureDescriptionApiKeyState.configured ? (
                    <p className="text-xs leading-5 text-slate-500">
                      {t('systemSetup.forms.secret.keepExisting')}
                      {runtimeConfigDraft.documentParsing.pictureDescriptionApiKeyState.hint
                        ? ` (${runtimeConfigDraft.documentParsing.pictureDescriptionApiKeyState.hint})`
                        : ''}
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="mt-4 space-y-2">
                <Label>{t('systemSetup.forms.documentParsing.pictureDescriptionPrompt.label')}</Label>
                <textarea
                  value={runtimeConfigDraft.documentParsing.pictureDescriptionPrompt}
                  onChange={(event) =>
                    updateRuntimeConfigGroup('document_parsing', { pictureDescriptionPrompt: event.target.value })
                  }
                  className={TEXTAREA_CLASSNAME}
                  rows={3}
                  placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionPrompt.placeholder')}
                />
              </div>
            </div>
          ) : null}

          {!ocrConfigEnabled && !pictureDescriptionEnabled ? (
            <div className="rounded-[22px] border border-slate-200 bg-slate-50/70 px-4 py-4 text-sm text-slate-600">
              {t('initialization.capabilities.sections.docling.empty')}
            </div>
          ) : null}
        </div>
      </section>
    )

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

        {knowledgeGraphEnabled || doclingVisible ? (
          <div className="space-y-4">
            {knowledgeGraphEnabled ? renderLightRagSection() : null}
            {doclingVisible ? renderDoclingSection() : null}
          </div>
        ) : (
          <div className="rounded-[24px] border border-slate-200 bg-white p-6 shadow-sm">
            <p className="text-sm font-semibold text-slate-900">
              {t('initialization.capabilities.emptyTitle')}
            </p>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {t('initialization.capabilities.emptyDescription')}
            </p>
          </div>
        )}
      </div>
    )
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
      </div>
    )
  }
  const stepLabel = t(`initialization.steps.${STEP_KEYS[step]}.label`)

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
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
