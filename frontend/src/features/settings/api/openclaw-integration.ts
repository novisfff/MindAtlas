import { apiClient } from '@/lib/api/client'

export type OpenClawCapabilitySourceType = 'tool' | 'workflow' | 'agent'
export type OpenClawCatalogSourceType = 'tool' | 'workflow' | 'agent'
export type OpenClawToolResponseMode = 'json_schema' | 'text_field'
export type OpenClawSchemaMode = 'readonly' | 'editable'

export interface OpenClawCatalogItem {
  id: string
  capabilityKey: string
  toolName: string
  title: string
  description: string
  sourceType: OpenClawCapabilitySourceType
  implementationType: string
  systemDefaultKey?: string | null
  sourceToolName?: string | null
  toolId?: string | null
  workflowId?: string | null
  agentProfileId?: string | null
  sourceName?: string | null
  sourceDescription?: string | null
  sourceIsSystem: boolean
  sourceEnabled?: boolean | null
  publishedVersionId?: string | null
  enabled: boolean
  isSystemItem: boolean
  available: boolean
  availabilityReason?: string | null
  schemaEditable: boolean
  inputSummary: string
  outputSummary: string
  inputSchema: Record<string, unknown>
  outputSchema: Record<string, unknown>
  toolResponseMode: OpenClawToolResponseMode
  createdAt: string
  updatedAt: string
}

export interface OpenClawCatalogSource {
  sourceType: OpenClawCatalogSourceType
  sourceKey: string
  title: string
  description: string
  isSystem: boolean
  enabled: boolean
  bindable: boolean
  unavailableReason?: string | null
  schemaMode: OpenClawSchemaMode
  sourceToolName?: string | null
  toolId?: string | null
  workflowId?: string | null
  agentProfileId?: string | null
  publishedVersionId?: string | null
  defaultInputSchema?: Record<string, unknown> | null
  defaultOutputSchema?: Record<string, unknown> | null
  defaultInputSummary: string
  defaultOutputSummary: string
  defaultToolResponseMode?: OpenClawToolResponseMode | null
}

export interface OpenClawIntegrationSettingsResponse {
  enabled: boolean
  secretConfigured: boolean
  secretHint?: string | null
  secretLastRotatedAt?: string | null
  catalogItems: OpenClawCatalogItem[]
}

export interface OpenClawRotateSecretResponse {
  secret: string
  settings: OpenClawIntegrationSettingsResponse
}

export interface OpenClawCatalogSourceListResponse {
  items: OpenClawCatalogSource[]
}

export interface OpenClawIntegrationUpdateRequest {
  enabled: boolean
}

export interface OpenClawCatalogItemUpsertRequest {
  sourceType: OpenClawCatalogSourceType
  toolName: string
  title: string
  description: string
  enabled: boolean
  inputSummary?: string
  outputSummary?: string
  inputSchema?: Record<string, unknown>
  outputSchema?: Record<string, unknown>
  toolResponseMode?: OpenClawToolResponseMode
  sourceToolName?: string | null
  toolId?: string | null
  workflowId?: string | null
  agentProfileId?: string | null
}

export interface OpenClawCatalogItemUpdateRequest extends Partial<OpenClawCatalogItemUpsertRequest> {}

export function getOpenClawIntegrationSettings() {
  return apiClient.get<OpenClawIntegrationSettingsResponse>('/api/system-settings/openclaw-integration')
}

export function updateOpenClawIntegrationSettings(payload: OpenClawIntegrationUpdateRequest) {
  return apiClient.put<OpenClawIntegrationSettingsResponse>('/api/system-settings/openclaw-integration', {
    body: payload,
  })
}

export function rotateOpenClawIntegrationSecret() {
  return apiClient.post<OpenClawRotateSecretResponse>('/api/system-settings/openclaw-integration/rotate-secret')
}

export function getOpenClawCatalogSources(sourceType: OpenClawCatalogSourceType) {
  return apiClient.get<OpenClawCatalogSourceListResponse>('/api/system-settings/openclaw-integration/catalog-sources', {
    query: { sourceType },
  })
}

export function createOpenClawCatalogItem(payload: OpenClawCatalogItemUpsertRequest) {
  return apiClient.post<OpenClawCatalogItem>('/api/system-settings/openclaw-integration/catalog-items', {
    body: payload,
  })
}

export function updateOpenClawCatalogItem(itemId: string, payload: OpenClawCatalogItemUpdateRequest) {
  return apiClient.put<OpenClawCatalogItem>(`/api/system-settings/openclaw-integration/catalog-items/${itemId}`, {
    body: payload,
  })
}

export function deleteOpenClawCatalogItem(itemId: string) {
  return apiClient.delete<{ deleted: boolean }>(`/api/system-settings/openclaw-integration/catalog-items/${itemId}`)
}

export function resetOpenClawSystemItems() {
  return apiClient.post<OpenClawIntegrationSettingsResponse>('/api/system-settings/openclaw-integration/reset-system-items')
}
