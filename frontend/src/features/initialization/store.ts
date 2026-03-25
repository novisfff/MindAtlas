import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { InitializationDefaultEntryType } from './api/systemInitialization'
import { getRandomColor } from '@/lib/colors'
import { getCurrentLocale } from '@/lib/api/locale'
import type { Locale } from '@/stores/app-store'

export interface InitializationDraftEntryType extends InitializationDefaultEntryType {
  draftId: string
  isDirty: boolean
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
  setStep: (step: number) => void
  setLocale: (locale: Locale) => void
  setAiCredential: (patch: Partial<InitializationWizardState['aiCredential']>) => void
  setLlmModelName: (name: string) => void
  mergeDefaultEntryTypes: (defaults: InitializationDefaultEntryType[], locale: Locale) => void
  addCustomEntryType: () => void
  updateEntryType: (draftId: string, patch: Partial<InitializationDraftEntryType>) => void
  removeEntryType: (draftId: string) => void
  resetDraft: (locale?: Locale) => void
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
  }
}

export const useInitializationWizardStore = create<InitializationWizardState>()(
  persist(
    (set) => ({
      ...initialState(getCurrentLocale()),
      setStep: (step) => set({ step: Math.max(0, Math.min(step, 3)) }),
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
      resetDraft: (locale = getCurrentLocale()) => set(initialState(locale)),
    }),
    {
      name: 'mindatlas-initialization-draft',
      version: 1,
    }
  )
)
