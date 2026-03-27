import { apiClient } from '@/lib/api/client'

export type RuntimeConfigSource = 'app_config' | 'environment_default' | 'default'
export type RuntimeConfigGroupKey = 'storage' | 'knowledge_graph' | 'document_parsing' | 'automation'

export interface SecretFieldState {
  configured: boolean
  hint?: string | null
}

export interface RuntimeConfigModuleBase {
  groupKey: RuntimeConfigGroupKey
  configured: boolean
  source: RuntimeConfigSource
  restartRequired: boolean
  hasSecrets: boolean
  effectiveSummary: string
}

export interface CapabilityModuleSummary extends RuntimeConfigModuleBase {
  title: string
  description: string
  allowSkip: boolean
}

export interface RuntimeStorageConfigResponse extends RuntimeConfigModuleBase {
  endpoint: string
  bucket: string
  secure: boolean
  maxFileSizeMb: number
  maxPdfPages: number
  accessKeyState: SecretFieldState
  secretKeyState: SecretFieldState
}

export interface RuntimeKnowledgeGraphConfigResponse extends RuntimeConfigModuleBase {
  enabled: boolean
  neo4jUri: string
  neo4jUser: string
  neo4jDatabase: string
  workspace: string
  graphStorage: string
  summaryLanguage: string
  llmModelId?: string | null
  llmModelName?: string | null
  embeddingModelId?: string | null
  embeddingModelName?: string | null
  embeddingHost: string
  embeddingDim: number | null
  rerankModel: string
  rerankHost: string
  rerankRequestFormat: string
  neo4jPasswordState: SecretFieldState
  embeddingApiKeyState: SecretFieldState
  rerankApiKeyState: SecretFieldState
}

export interface RuntimeDocumentParsingConfigResponse extends RuntimeConfigModuleBase {
  workerEnabled: boolean
  ocrEnabled: boolean
  ocrLangs: string
  pictureDescriptionEnabled: boolean
  pictureDescriptionUrl: string
  pictureDescriptionModel: string
  pictureDescriptionPrompt: string
  pictureDescriptionTimeoutSec: number
  pictureDescriptionParamsJson: string
  maxFileSizeMb: number
  maxPdfPages: number
  pictureDescriptionApiKeyState: SecretFieldState
}

export interface RuntimeAutomationConfigResponse extends RuntimeConfigModuleBase {
  schedulerEnabled: boolean
}

export interface RuntimeConfigResponse {
  storage: RuntimeStorageConfigResponse
  knowledgeGraph: RuntimeKnowledgeGraphConfigResponse
  documentParsing: RuntimeDocumentParsingConfigResponse
  automation: RuntimeAutomationConfigResponse
}

export interface RuntimeConfigValidationResponse {
  ok: boolean
  message?: string | null
  fieldErrors: Record<string, string>
}

export interface RuntimeStorageConfigRequest {
  endpoint?: string
  accessKey?: string
  secretKey?: string
  bucket?: string
  secure?: boolean
  maxFileSizeMb?: number
  maxPdfPages?: number
}

export interface RuntimeKnowledgeGraphConfigRequest {
  enabled?: boolean
  neo4jUri?: string
  neo4jUser?: string
  neo4jPassword?: string
  neo4jDatabase?: string
  workspace?: string
  graphStorage?: string
  summaryLanguage?: string
  llmModelId?: string | null
  llmModelName?: string
  embeddingModelId?: string | null
  embeddingModelName?: string
  embeddingHost?: string
  embeddingDim?: number | null
  embeddingApiKey?: string
  rerankModel?: string
  rerankHost?: string
  rerankApiKey?: string
  rerankRequestFormat?: string
}

export interface RuntimeDocumentParsingConfigRequest {
  workerEnabled?: boolean
  ocrEnabled?: boolean
  ocrLangs?: string
  pictureDescriptionEnabled?: boolean
  pictureDescriptionUrl?: string
  pictureDescriptionApiKey?: string
  pictureDescriptionModel?: string
  pictureDescriptionPrompt?: string
  pictureDescriptionTimeoutSec?: number
  pictureDescriptionParamsJson?: string
  maxFileSizeMb?: number
  maxPdfPages?: number
}

export interface RuntimeAutomationConfigRequest {
  schedulerEnabled?: boolean
}

export interface RuntimeConfigPayloadRequest {
  storage?: RuntimeStorageConfigRequest
  knowledgeGraph?: RuntimeKnowledgeGraphConfigRequest
  documentParsing?: RuntimeDocumentParsingConfigRequest
  automation?: RuntimeAutomationConfigRequest
}

export type RuntimeConfigRequestByGroup = {
  storage: RuntimeStorageConfigRequest
  knowledge_graph: RuntimeKnowledgeGraphConfigRequest
  document_parsing: RuntimeDocumentParsingConfigRequest
  automation: RuntimeAutomationConfigRequest
}

export async function getRuntimeConfig(): Promise<RuntimeConfigResponse> {
  return apiClient.get('/api/system-settings/runtime-config')
}

export async function updateRuntimeConfig<GroupKey extends RuntimeConfigGroupKey>(
  groupKey: GroupKey,
  payload: RuntimeConfigRequestByGroup[GroupKey]
): Promise<RuntimeConfigResponse> {
  return apiClient.put(`/api/system-settings/runtime-config/${groupKey}`, {
    body: payload,
  })
}

export async function validateRuntimeConfig<GroupKey extends Extract<RuntimeConfigGroupKey, 'storage' | 'knowledge_graph'>>(
  groupKey: GroupKey,
  payload: RuntimeConfigRequestByGroup[GroupKey]
): Promise<RuntimeConfigValidationResponse> {
  return apiClient.post(`/api/system-settings/runtime-config/${groupKey}/validate`, {
    body: payload,
  })
}
