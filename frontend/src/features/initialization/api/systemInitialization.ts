import { apiClient } from '@/lib/api/client'
import type { Locale } from '@/stores/app-store'
import type {
  CapabilityModuleSummary,
  RuntimeConfigPayloadRequest,
  RuntimeConfigResponse,
} from '@/features/system-setup'

export interface InitializationStatusResponse {
  initialized: boolean
  legacyAutoCompleted: boolean
  locale: Locale
}

export interface InitializationDefaultEntryType {
  code: string
  name: string
  description?: string
  color?: string
  icon?: string
  graphEnabled: boolean
  aiEnabled: boolean
  enabled: boolean
  origin: 'default' | 'custom'
}

export interface InitializationDefaultsResponse {
  locale: Locale
  entryTypes: InitializationDefaultEntryType[]
  capabilityModules: CapabilityModuleSummary[]
  runtimeConfig: RuntimeConfigResponse
}

export interface InitializeSystemRequest {
  locale: Locale
  aiCredential: {
    name: string
    baseUrl: string
    apiKey: string
  }
  llmModel: {
    name: string
  }
  entryTypes: Array<{
    code?: string
    name: string
    description?: string
    color?: string
    icon?: string
    graphEnabled: boolean
    aiEnabled: boolean
    enabled: boolean
    origin: 'default' | 'custom'
  }>
  runtimeConfig?: RuntimeConfigPayloadRequest
}

export interface InitializationCompletionResponse {
  initialized: boolean
  locale: Locale
}

export function getInitializationStatus() {
  return apiClient.get<InitializationStatusResponse>('/api/system-settings/initialization-status')
}

export function getInitializationDefaults(locale: Locale) {
  return apiClient.get<InitializationDefaultsResponse>('/api/system-settings/initialization-defaults', {
    query: { locale },
  })
}

export function initializeSystem(payload: InitializeSystemRequest) {
  return apiClient.post<InitializationCompletionResponse>('/api/system-settings/initialize', {
    body: payload,
  })
}
