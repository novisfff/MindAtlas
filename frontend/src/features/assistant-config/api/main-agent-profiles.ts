/**
 * Main Agent Profile API client (Plan 01 routes under /main-agent-profiles).
 * No single-target Skill fields — Profile is not an embedded Skill executor.
 */
import { apiClient } from '@/lib/api/client'

export const MAIN_AGENT_PROFILES_BASE = '/api/assistant-config/main-agent-profiles'

export type VersionSource = 'save' | 'publish'

export interface MainAgentProfileVersionSummary {
  id: string
  profileId: string
  sequenceNo: number
  versionName: string
  versionSource: VersionSource
  origin: string
  contentDigest: string
  sourceDraftVersionId?: string | null
  createdAt?: string | null
}

export interface MainAgentProfileVersionDetail extends MainAgentProfileVersionSummary {
  snapshot: MainAgentProfileSnapshot
  sourceRef?: Record<string, unknown> | null
}

export interface MainAgentProfileSummary {
  id: string
  profileKey: string
  displayName: string
  isDefault: boolean
  migrationState: string
  runtimeEnabled: boolean
  draftVersion?: MainAgentProfileVersionSummary | null
  publishedVersion?: MainAgentProfileVersionSummary | null
  legacySkillId?: string | null
  legacySourceDigest?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface MainAgentProfileSnapshot {
  schemaVersion: 1
  basePrompt: string
  responseStyle: Record<string, string>
  supportedEntrypoints: string[]
  modelRequirements: {
    toolCalling: boolean
    streaming: boolean
    multiToolCalls: boolean
    jsonSchema: boolean
  }
  controlCapabilityKeys: string[]
  skillCatalogScope: {
    mode: 'all_published' | 'allowlist'
    packageIds: string[]
  }
  contextBudget: Record<string, number>
  outputBudget: Record<string, number>
  globalSafetyPolicy: { denyByDefault: boolean }
  fallbackPolicy: {
    legacyRuntimeAllowed: boolean
    beforeSideEffectsOnly: boolean
  }
}

export interface PageResult<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export function getDefaultMainAgentProfile(): Promise<MainAgentProfileSummary> {
  return apiClient.get<MainAgentProfileSummary>(`${MAIN_AGENT_PROFILES_BASE}/default`)
}

export function saveDefaultMainAgentDraft(body: {
  snapshot: MainAgentProfileSnapshot
  versionName?: string | null
}): Promise<MainAgentProfileVersionSummary> {
  return apiClient.put<MainAgentProfileVersionSummary>(`${MAIN_AGENT_PROFILES_BASE}/default/draft`, {
    body: {
      snapshot: body.snapshot,
      versionName: body.versionName ?? null,
    },
  })
}

export function listDefaultMainAgentVersions(params?: {
  limit?: number
  offset?: number
}): Promise<PageResult<MainAgentProfileVersionSummary>> {
  return apiClient.get<PageResult<MainAgentProfileVersionSummary>>(
    `${MAIN_AGENT_PROFILES_BASE}/default/versions`,
    { query: { limit: params?.limit ?? 50, offset: params?.offset ?? 0 } },
  )
}

export function publishDefaultMainAgent(body: {
  draftVersionId: string
  gateId?: string | null
  requestId?: string | null
}): Promise<MainAgentProfileVersionSummary> {
  return apiClient.post<MainAgentProfileVersionSummary>(`${MAIN_AGENT_PROFILES_BASE}/default/publish`, {
    body: {
      draftVersionId: body.draftVersionId,
      gateId: body.gateId ?? null,
      requestId: body.requestId ?? null,
    },
  })
}

/** Assert snapshot has no single-target skill execution fields. */
export function assertNoSingleTargetFields(snapshot: Record<string, unknown>): string[] {
  const forbidden = ['skillId', 'workflowId', 'agentProfileId', 'targetType', 'targetId']
  return forbidden.filter((k) => k in snapshot && snapshot[k] != null)
}
