import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { InitializationDefaultEntryType, InitializationStatusResponse } from './api/systemInitialization'
import type {
  CapabilityModuleSummary,
  RuntimeAutomationConfigResponse,
  RuntimeConfigGroupKey,
  RuntimeConfigResponse,
  RuntimeDocumentParsingConfigResponse,
  RuntimeKnowledgeGraphConfigResponse,
  RuntimeStorageConfigResponse,
} from '@/features/system-setup'
import { getRandomColor } from '@/lib/colors'
import { getCurrentLocale } from '@/lib/api/locale'
import type { Locale } from '@/stores/app-store'

export interface InitializationDraftEntryType extends InitializationDefaultEntryType {
  draftId: string
  isDirty: boolean
}

export interface RuntimeStorageDraft extends RuntimeStorageConfigResponse {
  accessKey: string
  secretKey: string
  isDirty: boolean
}

export interface RuntimeKnowledgeGraphDraft extends RuntimeKnowledgeGraphConfigResponse {
  embeddingApiKey: string
  neo4jPassword: string
  rerankApiKey: string
  isDirty: boolean
}

export interface RuntimeDocumentParsingDraft extends RuntimeDocumentParsingConfigResponse {
  pictureDescriptionApiKey: string
  isDirty: boolean
}

export interface RuntimeAutomationDraft extends RuntimeAutomationConfigResponse {
  isDirty: boolean
}

export interface InitializationRuntimeConfigDraft {
  storage: RuntimeStorageDraft
  knowledgeGraph: RuntimeKnowledgeGraphDraft
  documentParsing: RuntimeDocumentParsingDraft
  automation: RuntimeAutomationDraft
}

interface InitializationWizardState {
  step: number
  locale: Locale
  aiCredential: {
    name: string
    baseUrl: string
    apiKey: string
  }
  llmModelName: string
  entryTypes: InitializationDraftEntryType[]
  removedDefaultCodes: string[]
  capabilityModules: CapabilityModuleSummary[]
  activeCapabilityGroup: RuntimeConfigGroupKey | null
  skippedCapabilityGroups: RuntimeConfigGroupKey[]
  runtimeConfigDraft: InitializationRuntimeConfigDraft
  setStep: (step: number) => void
  setLocale: (locale: Locale) => void
  setAiCredential: (patch: Partial<InitializationWizardState['aiCredential']>) => void
  setLlmModelName: (name: string) => void
  mergeDefaultEntryTypes: (defaults: InitializationDefaultEntryType[], locale: Locale) => void
  addCustomEntryType: () => void
  updateEntryType: (draftId: string, patch: Partial<InitializationDraftEntryType>) => void
  removeEntryType: (draftId: string) => void
  hydrateCapabilityDefaults: (modules: CapabilityModuleSummary[], runtimeConfig: RuntimeConfigResponse) => void
  setActiveCapabilityGroup: (groupKey: RuntimeConfigGroupKey | null) => void
  setCapabilitySkipped: (groupKey: RuntimeConfigGroupKey, skipped: boolean) => void
  updateRuntimeConfigGroup: <GroupKey extends RuntimeConfigGroupKey>(
    groupKey: GroupKey,
    patch: Partial<InitializationRuntimeConfigDraft[RuntimeConfigGroupToDraftKey[GroupKey]]>
  ) => void
  resetDraft: (locale?: Locale) => void
}

interface InitializationStatusSnapshotState {
  initialized: boolean | null
  locale: Locale | null
  checkedAt: number | null
  setSnapshot: (status: Pick<InitializationStatusResponse, 'initialized' | 'locale'>) => void
  clearSnapshot: () => void
}

type RuntimeConfigGroupToDraftKey = {
  storage: 'storage'
  knowledge_graph: 'knowledgeGraph'
  document_parsing: 'documentParsing'
  automation: 'automation'
}

function createDraftId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function createCustomEntryType(): InitializationDraftEntryType {
  return {
    draftId: createDraftId('custom'),
    code: '',
    name: '',
    description: '',
    color: getRandomColor(),
    icon: 'file-text',
    graphEnabled: true,
    aiEnabled: true,
    enabled: true,
    origin: 'custom',
    isDirty: true,
  }
}

function createDefaultDraft(item: InitializationDefaultEntryType, existingDraftId?: string): InitializationDraftEntryType {
  return {
    ...item,
    draftId: existingDraftId ?? createDraftId(item.code.toLowerCase()),
    isDirty: false,
  }
}

function createEmptyRuntimeStorageDraft(): RuntimeStorageDraft {
  return {
    groupKey: 'storage',
    configured: false,
    source: 'default',
    restartRequired: false,
    hasSecrets: false,
    effectiveSummary: '',
    endpoint: '',
    bucket: '',
    secure: false,
    maxFileSizeMb: 100,
    maxPdfPages: 500,
    accessKeyState: { configured: false },
    secretKeyState: { configured: false },
    accessKey: '',
    secretKey: '',
    isDirty: false,
  }
}

function createEmptyKnowledgeGraphDraft(): RuntimeKnowledgeGraphDraft {
  return {
    groupKey: 'knowledge_graph',
    configured: false,
    source: 'default',
    restartRequired: false,
    hasSecrets: false,
    effectiveSummary: '',
    enabled: false,
    neo4jUri: '',
    neo4jUser: '',
    neo4jDatabase: '',
    workspace: '',
    graphStorage: 'Neo4JStorage',
    summaryLanguage: '',
    llmModelId: null,
    llmModelName: '',
    embeddingModelId: null,
    embeddingModelName: '',
    embeddingHost: '',
    embeddingDim: 1536,
    rerankModel: '',
    rerankHost: '',
    rerankRequestFormat: 'standard',
    neo4jPasswordState: { configured: false },
    embeddingApiKeyState: { configured: false },
    rerankApiKeyState: { configured: false },
    neo4jPassword: '',
    embeddingApiKey: '',
    rerankApiKey: '',
    isDirty: false,
  }
}

function createEmptyDocumentParsingDraft(): RuntimeDocumentParsingDraft {
  return {
    groupKey: 'document_parsing',
    configured: false,
    source: 'default',
    restartRequired: true,
    hasSecrets: false,
    effectiveSummary: '',
    workerEnabled: false,
    ocrEnabled: true,
    ocrLangs: 'auto',
    pictureDescriptionEnabled: false,
    pictureDescriptionUrl: '',
    pictureDescriptionModel: '',
    pictureDescriptionPrompt: '',
    pictureDescriptionTimeoutSec: 60,
    pictureDescriptionParamsJson: '',
    maxFileSizeMb: 100,
    maxPdfPages: 500,
    pictureDescriptionApiKeyState: { configured: false },
    pictureDescriptionApiKey: '',
    isDirty: false,
  }
}

function createEmptyAutomationDraft(): RuntimeAutomationDraft {
  return {
    groupKey: 'automation',
    configured: false,
    source: 'default',
    restartRequired: false,
    hasSecrets: false,
    effectiveSummary: '',
    schedulerEnabled: false,
    isDirty: false,
  }
}

function createInitialRuntimeConfigDraft(): InitializationRuntimeConfigDraft {
  return {
    storage: createEmptyRuntimeStorageDraft(),
    knowledgeGraph: createEmptyKnowledgeGraphDraft(),
    documentParsing: createEmptyDocumentParsingDraft(),
    automation: createEmptyAutomationDraft(),
  }
}

function hydrateStorageDraft(
  current: RuntimeStorageDraft,
  incoming: RuntimeStorageConfigResponse
): RuntimeStorageDraft {
  if (current.isDirty) {
    return current
  }
  return {
    ...incoming,
    accessKey: '',
    secretKey: '',
    isDirty: false,
  }
}

function hydrateKnowledgeGraphDraft(
  current: RuntimeKnowledgeGraphDraft,
  incoming: RuntimeKnowledgeGraphConfigResponse
): RuntimeKnowledgeGraphDraft {
  if (current.isDirty) {
    return current
  }
  return {
    ...incoming,
    llmModelName: incoming.llmModelName ?? '',
    embeddingModelName: incoming.embeddingModelName ?? '',
    embeddingDim: incoming.embeddingDim ?? 1536,
    neo4jPassword: '',
    embeddingApiKey: '',
    rerankApiKey: '',
    isDirty: false,
  }
}

function hydrateDocumentParsingDraft(
  current: RuntimeDocumentParsingDraft,
  incoming: RuntimeDocumentParsingConfigResponse
): RuntimeDocumentParsingDraft {
  if (current.isDirty) {
    return current
  }
  return {
    ...incoming,
    pictureDescriptionApiKey: '',
    isDirty: false,
  }
}

function hydrateAutomationDraft(
  current: RuntimeAutomationDraft,
  incoming: RuntimeAutomationConfigResponse
): RuntimeAutomationDraft {
  if (current.isDirty) {
    return current
  }
  return {
    ...incoming,
    isDirty: false,
  }
}

function initialState(locale: Locale) {
  return {
    step: 0,
    locale,
    aiCredential: {
      name: '',
      baseUrl: '',
      apiKey: '',
    },
    llmModelName: '',
    entryTypes: [] as InitializationDraftEntryType[],
    removedDefaultCodes: [] as string[],
    capabilityModules: [] as CapabilityModuleSummary[],
    activeCapabilityGroup: null as RuntimeConfigGroupKey | null,
    skippedCapabilityGroups: [] as RuntimeConfigGroupKey[],
    runtimeConfigDraft: createInitialRuntimeConfigDraft(),
  }
}

function initialStatusSnapshot() {
  return {
    initialized: null as boolean | null,
    locale: null as Locale | null,
    checkedAt: null as number | null,
  }
}

function sanitizeDraftLlmModelName(value: unknown) {
  return typeof value === 'string' ? value : ''
}

export const useInitializationWizardStore = create<InitializationWizardState>()(
  persist(
    (set) => ({
      ...initialState(getCurrentLocale()),
      setStep: (step) => set({ step: Math.max(0, Math.min(step, 5)) }),
      setLocale: (locale) => set({ locale }),
      setAiCredential: (patch) =>
        set((state) => ({
          aiCredential: {
            ...state.aiCredential,
            ...patch,
          },
        })),
      setLlmModelName: (name) => set({ llmModelName: name }),
      mergeDefaultEntryTypes: (defaults, locale) =>
        set((state) => {
          const removedCodes = new Set(state.removedDefaultCodes)
          const currentDefaults = new Map(
            state.entryTypes
              .filter((item) => item.origin === 'default')
              .map((item) => [item.code, item] as const)
          )

          const mergedDefaults = defaults
            .filter((item) => !removedCodes.has(item.code))
            .map((item) => {
              const existing = currentDefaults.get(item.code)
              if (!existing) return createDefaultDraft(item)
              if (existing.isDirty) return existing
              return createDefaultDraft(item, existing.draftId)
            })

          return {
            locale,
            entryTypes: [
              ...mergedDefaults,
              ...state.entryTypes.filter((item) => item.origin === 'custom'),
            ],
          }
        }),
      addCustomEntryType: () =>
        set((state) => ({
          entryTypes: [...state.entryTypes, createCustomEntryType()],
        })),
      updateEntryType: (draftId, patch) =>
        set((state) => ({
          entryTypes: state.entryTypes.map((item) =>
            item.draftId === draftId
              ? {
                  ...item,
                  ...patch,
                  isDirty: true,
                }
              : item
          ),
        })),
      removeEntryType: (draftId) =>
        set((state) => {
          const target = state.entryTypes.find((item) => item.draftId === draftId)
          const removedDefaultCodes = [...state.removedDefaultCodes]
          if (target?.origin === 'default' && target.code && !removedDefaultCodes.includes(target.code)) {
            removedDefaultCodes.push(target.code)
          }
          return {
            removedDefaultCodes,
            entryTypes: state.entryTypes.filter((item) => item.draftId !== draftId),
          }
        }),
      hydrateCapabilityDefaults: (modules, runtimeConfig) =>
        set((state) => ({
          capabilityModules: modules,
          runtimeConfigDraft: {
            storage: hydrateStorageDraft(state.runtimeConfigDraft.storage, runtimeConfig.storage),
            knowledgeGraph: hydrateKnowledgeGraphDraft(state.runtimeConfigDraft.knowledgeGraph, runtimeConfig.knowledgeGraph),
            documentParsing: hydrateDocumentParsingDraft(state.runtimeConfigDraft.documentParsing, runtimeConfig.documentParsing),
            automation: hydrateAutomationDraft(state.runtimeConfigDraft.automation, runtimeConfig.automation),
          },
        })),
      setActiveCapabilityGroup: (groupKey) => set({ activeCapabilityGroup: groupKey }),
      setCapabilitySkipped: (groupKey, skipped) =>
        set((state) => {
          const next = new Set(state.skippedCapabilityGroups)
          if (skipped) {
            next.add(groupKey)
          } else {
            next.delete(groupKey)
          }
          return {
            skippedCapabilityGroups: Array.from(next),
          }
        }),
      updateRuntimeConfigGroup: (groupKey, patch) =>
        set((state) => {
          const runtimeConfigDraft = { ...state.runtimeConfigDraft }
          if (groupKey === 'storage') {
            runtimeConfigDraft.storage = {
              ...state.runtimeConfigDraft.storage,
              ...patch,
              isDirty: true,
            }
          } else if (groupKey === 'knowledge_graph') {
            runtimeConfigDraft.knowledgeGraph = {
              ...state.runtimeConfigDraft.knowledgeGraph,
              ...patch,
              isDirty: true,
            }
          } else if (groupKey === 'document_parsing') {
            runtimeConfigDraft.documentParsing = {
              ...state.runtimeConfigDraft.documentParsing,
              ...patch,
              isDirty: true,
            }
          } else if (groupKey === 'automation') {
            runtimeConfigDraft.automation = {
              ...state.runtimeConfigDraft.automation,
              ...patch,
              isDirty: true,
            }
          }

          const skippedCapabilityGroups = state.skippedCapabilityGroups.filter((item) => item !== groupKey)
          return {
            runtimeConfigDraft,
            skippedCapabilityGroups,
          }
        }),
      resetDraft: (locale = getCurrentLocale()) => set(initialState(locale)),
    }),
    {
      name: 'mindatlas-initialization-draft',
      version: 6,
      migrate: (persistedState, version) => {
        const state = (persistedState ?? {}) as Record<string, unknown>
        const rawStep = typeof state.step === 'number' ? state.step : 0
        const migratedStep =
          version < 6 && rawStep >= 1
            ? Math.min(rawStep + 1, 5)
            : Math.max(0, Math.min(rawStep, 5))
        return {
          ...state,
          step: migratedStep,
          llmModelName: sanitizeDraftLlmModelName(state.llmModelName),
        }
      },
    }
  )
)

export const useInitializationStatusSnapshotStore = create<InitializationStatusSnapshotState>()(
  persist(
    (set) => ({
      ...initialStatusSnapshot(),
      setSnapshot: (status) =>
        set({
          initialized: status.initialized,
          locale: status.locale,
          checkedAt: Date.now(),
        }),
      clearSnapshot: () => set(initialStatusSnapshot()),
    }),
    {
      name: 'mindatlas-initialization-status',
      version: 1,
      partialize: (state) => ({
        initialized: state.initialized,
        locale: state.locale,
        checkedAt: state.checkedAt,
      }),
    }
  )
)

export function setPersistedInitializationStatus(
  status: Pick<InitializationStatusResponse, 'initialized' | 'locale'>
) {
  useInitializationStatusSnapshotStore.getState().setSnapshot(status)
}

export function getPersistedInitializationStatus(): InitializationStatusResponse | undefined {
  const snapshot = useInitializationStatusSnapshotStore.getState()
  if (snapshot.initialized === null) {
    return undefined
  }

  return {
    initialized: snapshot.initialized,
    locale: snapshot.locale ?? getCurrentLocale(),
  }
}

export function getPersistedInitializationCheckedAt() {
  return useInitializationStatusSnapshotStore.getState().checkedAt ?? undefined
}
