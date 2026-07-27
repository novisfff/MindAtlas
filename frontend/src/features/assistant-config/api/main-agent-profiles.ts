/**
 * Main Agent Profile API client.
 * Plan 01 always-mounted routes under /main-agent-profiles.
 * Plan 09 protected lifecycle under /skill-admin/main-agent-profiles (trusted mount).
 * No single-target Skill fields — Profile is not an embedded Skill executor.
 */
import { apiClient } from '@/lib/api/client'
import { newRequestId, skillAdminOperatorHeaders, SKILL_ADMIN_BASE } from './skill-packages'

export const MAIN_AGENT_PROFILES_BASE = '/api/assistant-config/main-agent-profiles'
export const MAIN_AGENT_PROFILES_ADMIN_BASE = `${SKILL_ADMIN_BASE}/main-agent-profiles`

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
  aggregateRevision?: number
  draftVersion?: MainAgentProfileVersionSummary | null
  publishedVersion?: MainAgentProfileVersionSummary | null
  legacySkillId?: string | null
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
  expectedAggregateRevision: number
  gateId?: string | null
  requestId?: string | null
}): Promise<MainAgentProfileVersionSummary> {
  return apiClient.post<MainAgentProfileVersionSummary>(`${MAIN_AGENT_PROFILES_BASE}/default/publish`, {
    body: {
      draftVersionId: body.draftVersionId,
      expectedAggregateRevision: body.expectedAggregateRevision,
      gateId: body.gateId ?? null,
      requestId: body.requestId ?? null,
    },
  })
}

// ---------------------------------------------------------------------------
// Plan 09 protected Profile lifecycle (trusted mount; distinct commands)
// ---------------------------------------------------------------------------

/** Protected draft save. Does not touch runtime_enabled or published pointer. */
export function saveProtectedDefaultMainAgentDraft(body: {
  snapshot: MainAgentProfileSnapshot
  versionName?: string | null
  expectedAggregateRevision: number
  requestId?: string | null
}): Promise<MainAgentProfileVersionSummary> {
  return apiClient.put<MainAgentProfileVersionSummary>(
    `${MAIN_AGENT_PROFILES_ADMIN_BASE}/default/draft`,
    {
      body: {
        snapshot: body.snapshot,
        versionName: body.versionName ?? null,
        expectedAggregateRevision: body.expectedAggregateRevision,
        requestId: body.requestId ?? newRequestId('profile-draft'),
      },
      headers: skillAdminOperatorHeaders(),
    },
  )
}

/** Protected publish (content stage). Not runtime enable. */
export function publishProtectedDefaultMainAgent(body: {
  draftVersionId: string
  expectedAggregateRevision: number
  gateId?: string | null
  requestId?: string | null
}): Promise<MainAgentProfileVersionSummary> {
  return apiClient.post<MainAgentProfileVersionSummary>(
    `${MAIN_AGENT_PROFILES_ADMIN_BASE}/default/publish`,
    {
      body: {
        draftVersionId: body.draftVersionId,
        expectedAggregateRevision: body.expectedAggregateRevision,
        gateId: body.gateId ?? null,
        requestId: body.requestId ?? newRequestId('profile-pub'),
      },
      headers: skillAdminOperatorHeaders(),
    },
  )
}

/** Protected runtime enable — requires promotion gate + published version CAS. */
export function enableProtectedDefaultMainAgentRuntime(body: {
  expectedAggregateRevision: number
  expectedPublishedVersionId?: string | null
  gateId?: string | null
  requestId?: string | null
}): Promise<MainAgentProfileSummary> {
  return apiClient.post<MainAgentProfileSummary>(
    `${MAIN_AGENT_PROFILES_ADMIN_BASE}/default/runtime/enable`,
    {
      body: {
        expectedAggregateRevision: body.expectedAggregateRevision,
        expectedPublishedVersionId: body.expectedPublishedVersionId ?? null,
        gateId: body.gateId ?? null,
        requestId: body.requestId ?? newRequestId('profile-en'),
      },
      headers: skillAdminOperatorHeaders(),
    },
  )
}

/**
 * Protected runtime disable — explicit confirmation + request ID.
 * No promotion gate required.
 */
export function disableProtectedDefaultMainAgentRuntime(body: {
  expectedAggregateRevision: number
  expectedPublishedVersionId?: string | null
  requestId?: string | null
}): Promise<MainAgentProfileSummary> {
  return apiClient.post<MainAgentProfileSummary>(
    `${MAIN_AGENT_PROFILES_ADMIN_BASE}/default/runtime/disable`,
    {
      body: {
        expectedAggregateRevision: body.expectedAggregateRevision,
        expectedPublishedVersionId: body.expectedPublishedVersionId ?? null,
        gateId: null,
        requestId: body.requestId ?? newRequestId('profile-dis'),
      },
      headers: skillAdminOperatorHeaders(),
    },
  )
}

/** Protected default profile summary (principal required). */
export function getProtectedDefaultMainAgentProfile(): Promise<MainAgentProfileSummary> {
  return apiClient.get<MainAgentProfileSummary>(`${MAIN_AGENT_PROFILES_ADMIN_BASE}/default`, {
    headers: skillAdminOperatorHeaders(),
  })
}

/** Protected version list. */
export function listProtectedDefaultMainAgentVersions(): Promise<{
  items: MainAgentProfileVersionSummary[]
  total: number
}> {
  return apiClient.get<{ items: MainAgentProfileVersionSummary[]; total: number }>(
    `${MAIN_AGENT_PROFILES_ADMIN_BASE}/default/versions`,
    { headers: skillAdminOperatorHeaders() },
  )
}

/** Protected version detail. */
export function getProtectedDefaultMainAgentVersion(
  versionId: string,
): Promise<MainAgentProfileVersionDetail> {
  return apiClient.get<MainAgentProfileVersionDetail>(
    `${MAIN_AGENT_PROFILES_ADMIN_BASE}/default/versions/${versionId}`,
    { headers: skillAdminOperatorHeaders() },
  )
}

/** Assert snapshot has no single-target skill execution fields. */
export function assertNoSingleTargetFields(snapshot: Record<string, unknown>): string[] {
  const forbidden = ['skillId', 'workflowId', 'agentProfileId', 'targetType', 'targetId']
  return forbidden.filter((k) => k in snapshot && snapshot[k] != null)
}

export function getDefaultMainAgentVersion(
  versionId: string,
): Promise<MainAgentProfileVersionDetail> {
  return apiClient.get<MainAgentProfileVersionDetail>(
    `${MAIN_AGENT_PROFILES_BASE}/default/versions/${versionId}`,
  )
}
