/**
 * Typed clients for Plan 01 skill packages + Plan 09 admin lifecycle.
 *
 * Plan 01 (always mounted): /api/assistant-config/skill-packages
 * Plan 09 admin (trusted mount only): /api/assistant-config/skill-admin
 *
 * UI never supplies authority. Optional operator headers from env are
 * trusted-mount local/dev only — not release authentication.
 */
import { apiClient, ApiError, isApiError } from '@/lib/api/client'

function readViteEnv(key: string): string | undefined {
  try {
    const meta = import.meta as ImportMeta & { env?: Record<string, string | undefined> }
    return meta.env?.[key]
  } catch {
    return undefined
  }
}

export const SKILL_PACKAGES_BASE = '/api/assistant-config/skill-packages'
export const SKILL_ADMIN_BASE = '/api/assistant-config/skill-admin'

export type SkillPackageMigrationState = 'shadow' | 'native' | 'cutover'
export type VersionSource = 'save' | 'publish'
export type AliasType = 'canonical' | 'legacy' | 'custom'
export type ResourceKind = 'scripts' | 'references' | 'assets' | 'other'
export type ImportMode = 'create' | 'append_to_existing' | 'fork_as_new'

export interface SkillVersionSummary {
  id: string
  skillPackageId: string
  sequenceNo: number
  versionName: string
  versionSource: VersionSource
  origin: string
  contentDigest: string
  skillMdDigest: string
  manifestDigest: string
  resourceIndexDigest: string
  bindingSetDigest?: string | null
  versionDigest?: string | null
  sourceDraftVersionId?: string | null
  createdAt?: string | null
}

export interface SkillResourceMetadata {
  path: string
  resourceKind: ResourceKind
  mediaType: string
  byteSize: number
  sha256: string
  executable?: boolean
}

export interface SkillVersionDetail extends SkillVersionSummary {
  frontmatter: Record<string, unknown>
  mindatlasManifest?: Record<string, unknown> | null
  resources: SkillResourceMetadata[]
  skillMd?: string | null
  mindatlasYaml?: string | null
}

export interface SkillPackageAliasSummary {
  id: string
  alias: string
  aliasType: AliasType
  disabledAt?: string | null
  disabledBy?: string | null
  createdAt?: string | null
}

export interface SkillPackageSummary {
  id: string
  canonicalName: string
  displayName: string
  description: string
  migrationState: SkillPackageMigrationState
  catalogEnabled: boolean
  isSystem: boolean
  aggregateRevision: number
  archivedAt?: string | null
  archivedBy?: string | null
  catalogEnabledAt?: string | null
  catalogEnabledBy?: string | null
  draftVersion?: SkillVersionSummary | null
  publishedVersion?: SkillVersionSummary | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface SkillPackageDetail extends SkillPackageSummary {
  aliases: SkillPackageAliasSummary[]
  legacySkillId?: string | null
  legacySourceDigest?: string | null
}

export interface PageResult<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface ListSkillPackagesParams {
  migrationState?: SkillPackageMigrationState
  publicationState?: 'unpublished' | 'published'
  catalogEnabled?: boolean
  limit?: number
  offset?: number
}

export interface ListSkillVersionsParams {
  versionSource?: VersionSource
  origin?: string
  limit?: number
  offset?: number
}

export interface SkillResourceInput {
  path: string
  contentBase64: string
}

export interface CreateSkillPackageRequest {
  skillMd: string
  mindatlasYaml?: string | null
  resources?: SkillResourceInput[]
  versionName?: string | null
}

export interface SaveSkillDraftRequest {
  skillMd: string
  mindatlasYaml?: string | null
  /** Omit to preserve previous draft resources server-side. Explicit [] clears. */
  resources?: SkillResourceInput[]
  versionName?: string | null
  expectedAggregateRevision?: number
  requestId?: string
}

export interface AggregateRevisionBody {
  requestId: string
  expectedAggregateRevision: number
  gateId?: string | null
}

export interface MetadataPatchRequest extends AggregateRevisionBody {
  displayName?: string | null
  description?: string | null
}

export interface CatalogEnableRequest extends AggregateRevisionBody {
  expectedPublishedVersionId?: string | null
}

export interface AddAliasRequest extends AggregateRevisionBody {
  alias: string
}

export interface ImportPreviewResult {
  previewId: string
  mode: ImportMode
  uploadDigest: string
  candidateContentDigest: string
  candidateCanonicalName: string
  targetPackageId?: string | null
  expectedAggregateRevision?: number | null
  expiresAt: string
  resourceIndex: Array<Record<string, unknown>>
  capabilityKeys: string[]
  findings: Array<Record<string, unknown>>
  structuralDiff: Array<Record<string, unknown>>
  resourceBytesExcluded: boolean
  rawArchiveExcluded: boolean
}

export interface ImportApplyResult {
  mode: ImportMode
  previewId: string
  requestId: string
  package: SkillPackageDetail
}

export interface SkillAdminSurfaceProbe {
  available: boolean
  packagesReadable: boolean
  adminMounted: boolean
  reason?: string
}

export type SkillPackageErrorKind =
  | 'conflict'
  | 'auth'
  | 'not_found'
  | 'validation'
  | 'unavailable'
  | 'unknown'

export interface MappedSkillPackageError {
  kind: SkillPackageErrorKind
  message: string
  status?: number
  code?: number
  details?: unknown
}

/** Optional trusted-mount operator headers (dev/test only; not release auth). */
export function skillAdminOperatorHeaders(): Record<string, string> {
  const id = readViteEnv('VITE_MINDATLAS_OPERATOR_ID')?.trim()
  const role = readViteEnv('VITE_MINDATLAS_OPERATOR_ROLE')?.trim()
  const headers: Record<string, string> = {}
  if (id) headers['X-MindAtlas-Operator-Id'] = id
  if (role) headers['X-MindAtlas-Operator-Role'] = role
  return headers
}

export function newRequestId(prefix = 'ui'): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function isConflictError(error: unknown): boolean {
  return (
    isApiError(error) &&
    (error.status === 409 || (error.code != null && error.code >= 40900 && error.code < 41000))
  )
}

export function isAuthError(error: unknown): boolean {
  return isApiError(error) && (error.status === 401 || error.status === 403)
}

export function isNotFoundError(error: unknown): boolean {
  return isApiError(error) && error.status === 404
}

export function mapSkillPackageError(error: unknown): MappedSkillPackageError {
  if (!isApiError(error)) {
    return {
      kind: 'unknown',
      message: error instanceof Error ? error.message : 'Unknown error',
    }
  }
  if (isConflictError(error)) {
    return {
      kind: 'conflict',
      message: error.message,
      status: error.status,
      code: error.code,
      details: error.details,
    }
  }
  if (error.status === 401 || error.status === 403) {
    return {
      kind: 'auth',
      message: error.message,
      status: error.status,
      code: error.code,
      details: error.details,
    }
  }
  if (error.status === 404) {
    return {
      kind: 'not_found',
      message: error.message,
      status: error.status,
      code: error.code,
      details: error.details,
    }
  }
  if (error.status === 422) {
    return {
      kind: 'validation',
      message: error.message,
      status: error.status,
      code: error.code,
      details: error.details,
    }
  }
  if (error.status === 503 || error.status === 502) {
    return {
      kind: 'unavailable',
      message: error.message,
      status: error.status,
      code: error.code,
      details: error.details,
    }
  }
  return {
    kind: 'unknown',
    message: error.message,
    status: error.status,
    code: error.code,
    details: error.details,
  }
}

// ---------------------------------------------------------------------------
// Plan 01 package APIs
// ---------------------------------------------------------------------------

export function listSkillPackages(
  params: ListSkillPackagesParams = {},
): Promise<PageResult<SkillPackageSummary>> {
  return apiClient.get<PageResult<SkillPackageSummary>>(SKILL_PACKAGES_BASE, {
    query: {
      migrationState: params.migrationState,
      publicationState: params.publicationState,
      catalogEnabled: params.catalogEnabled,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
}

export function getSkillPackage(packageId: string): Promise<SkillPackageDetail> {
  return apiClient.get<SkillPackageDetail>(`${SKILL_PACKAGES_BASE}/${packageId}`)
}

export function createSkillPackage(body: CreateSkillPackageRequest): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(SKILL_PACKAGES_BASE, { body })
}

export function saveSkillPackageDraft(
  packageId: string,
  body: SaveSkillDraftRequest,
): Promise<SkillVersionSummary> {
  return apiClient.put<SkillVersionSummary>(`${SKILL_PACKAGES_BASE}/${packageId}/draft`, { body })
}

export function listSkillPackageVersions(
  packageId: string,
  params: ListSkillVersionsParams = {},
): Promise<PageResult<SkillVersionSummary>> {
  return apiClient.get<PageResult<SkillVersionSummary>>(
    `${SKILL_PACKAGES_BASE}/${packageId}/versions`,
    {
      query: {
        versionSource: params.versionSource,
        origin: params.origin,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      },
    },
  )
}

export function getSkillPackageVersion(
  packageId: string,
  versionId: string,
): Promise<SkillVersionDetail> {
  return apiClient.get<SkillVersionDetail>(
    `${SKILL_PACKAGES_BASE}/${packageId}/versions/${versionId}`,
  )
}

export function publishSkillPackageVersion(
  packageId: string,
  body: {
    draftVersionId: string
    expectedAggregateRevision: number
    gateId?: string | null
    requestId?: string | null
  },
): Promise<SkillVersionSummary> {
  return apiClient.post<SkillVersionSummary>(`${SKILL_PACKAGES_BASE}/${packageId}/publish`, {
    body: {
      draftVersionId: body.draftVersionId,
      expectedAggregateRevision: body.expectedAggregateRevision,
      gateId: body.gateId ?? null,
      requestId: body.requestId ?? null,
    },
  })
}

export function skillPackageResourceUrl(
  packageId: string,
  versionId: string,
  resourcePath: string,
): string {
  const encoded = resourcePath
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
  return `${SKILL_PACKAGES_BASE}/${packageId}/versions/${versionId}/resources/${encoded}`
}

export function exportSkillPackageVersionUrl(packageId: string, versionId: string): string {
  return `${SKILL_PACKAGES_BASE}/${packageId}/versions/${versionId}/export`
}

/** Resource bytes as Blob (attachment). Never inject into DOM as HTML. */
export async function fetchSkillPackageResourceBlob(
  packageId: string,
  versionId: string,
  resourcePath: string,
): Promise<Blob> {
  const path = skillPackageResourceUrl(packageId, versionId, resourcePath)
  const response = await fetch(path, {
    headers: { accept: '*/*' },
    credentials: 'same-origin',
  })
  if (!response.ok) {
    throw new ApiError({
      message: `Failed to fetch resource ${resourcePath}`,
      status: response.status,
      url: path,
    })
  }
  return response.blob()
}

// ---------------------------------------------------------------------------
// Plan 09 admin APIs (trusted mount)
// ---------------------------------------------------------------------------

export function patchSkillPackageMetadata(
  packageId: string,
  body: MetadataPatchRequest,
): Promise<SkillPackageDetail> {
  return apiClient.patch<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/metadata`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function archiveSkillPackage(
  packageId: string,
  body: AggregateRevisionBody,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/archive`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function unarchiveSkillPackage(
  packageId: string,
  body: AggregateRevisionBody,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/unarchive`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function enableSkillPackageCatalog(
  packageId: string,
  body: CatalogEnableRequest,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/catalog/enable`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function disableSkillPackageCatalog(
  packageId: string,
  body: AggregateRevisionBody,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/catalog/disable`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function addSkillPackageAlias(
  packageId: string,
  body: AddAliasRequest,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/aliases`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function disableSkillPackageAlias(
  packageId: string,
  aliasId: string,
  body: AggregateRevisionBody,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/aliases/${aliasId}/disable`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export function restoreSkillPackageVersionAsDraft(
  packageId: string,
  versionId: string,
  body: AggregateRevisionBody,
): Promise<SkillPackageDetail> {
  return apiClient.post<SkillPackageDetail>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/versions/${versionId}/restore-draft`,
    { body, headers: skillAdminOperatorHeaders() },
  )
}

export async function previewSkillPackageImport(params: {
  file: File
  mode: ImportMode
  targetPackageId?: string
  expectedAggregateRevision?: number
  forkCanonicalName?: string
}): Promise<ImportPreviewResult> {
  const form = new FormData()
  form.append('file', params.file)
  form.append('mode', params.mode)
  if (params.targetPackageId) form.append('targetPackageId', params.targetPackageId)
  if (params.expectedAggregateRevision != null) {
    form.append('expectedAggregateRevision', String(params.expectedAggregateRevision))
  }
  if (params.forkCanonicalName) form.append('forkCanonicalName', params.forkCanonicalName)
  return apiClient.post<ImportPreviewResult>(`${SKILL_ADMIN_BASE}/skill-packages/import/preview`, {
    body: form,
    headers: skillAdminOperatorHeaders(),
  })
}

export function applySkillPackageImport(body: {
  previewId: string
  requestId: string
}): Promise<ImportApplyResult> {
  return apiClient.post<ImportApplyResult>(`${SKILL_ADMIN_BASE}/skill-packages/import/apply`, {
    body,
    headers: skillAdminOperatorHeaders(),
  })
}

/**
 * Fail-closed surface probe for Universal Skills UI.
 *
 * Plan 09 route gate requires BOTH:
 * - admin router mounted, and
 * - trusted principal authorized (probe succeeds as 2xx/404-for-missing-entity/409/422).
 *
 * 401/403 means principal missing/unauthorized → available=false (fail closed).
 * 404/405/5xx on the admin probe path → unmounted or unavailable → available=false.
 * Never treat auth failures as "available".
 */
export async function probeSkillAdminSurface(): Promise<SkillAdminSurfaceProbe> {
  const hardOff = String(readViteEnv('VITE_ASSISTANT_UNIVERSAL_SKILLS') ?? '').trim() === '0'
  if (hardOff) {
    return {
      available: false,
      packagesReadable: false,
      adminMounted: false,
      reason: 'hard_disabled',
    }
  }

  // Non-mutating probe: admin-only GET.
  // Mounted+authorized+missing entity → 404 on this synthetic id (still "mounted").
  // Mounted+no principal → 401/403 (principal fail-closed).
  // Unmounted → typically 404/405, but 404 alone is ambiguous; we treat only
  // success / validation / conflict as authorized mount for availability.
  let adminMounted = false
  let principalAuthorized = false
  let reason: string | undefined

  try {
    await apiClient.get(
      `${SKILL_ADMIN_BASE}/skill-packages/00000000-0000-4000-8000-000000000000/versions/00000000-0000-4000-8000-000000000001/diff/00000000-0000-4000-8000-000000000002`,
      { headers: skillAdminOperatorHeaders() },
    )
    // Unexpected success still means the router is mounted and principal passed.
    adminMounted = true
    principalAuthorized = true
  } catch (error) {
    if (isApiError(error) && error.status != null) {
      if (error.status === 401 || error.status === 403) {
        // Router may be mounted, but principal is missing/unauthorized → fail closed.
        adminMounted = true
        principalAuthorized = false
        reason = 'principal_unauthorized'
      } else if (error.status === 409 || error.status === 422) {
        // Mounted + authorized; business/validation rejection on synthetic ids.
        adminMounted = true
        principalAuthorized = true
      } else if (error.status === 404) {
        // Ambiguous: unmounted OR mounted missing entity. Prefer fail-closed for
        // availability unless Plan 01 list also proves packages surface is readable
        // after a later check — treat as unmounted for Plan 09 gate.
        adminMounted = false
        principalAuthorized = false
        reason = 'admin_unmounted'
      } else if (error.status === 405 || error.status >= 500) {
        adminMounted = false
        principalAuthorized = false
        reason = 'admin_unavailable'
      } else {
        adminMounted = false
        principalAuthorized = false
        reason = 'admin_unavailable'
      }
    } else {
      adminMounted = false
      principalAuthorized = false
      reason = 'admin_unavailable'
    }
  }

  let packagesReadable = false
  // Only attempt package list when principal is authorized for Plan 09 surface.
  if (principalAuthorized) {
    try {
      await listSkillPackages({ limit: 1, offset: 0 })
      packagesReadable = true
    } catch {
      packagesReadable = false
    }
  }

  const available = principalAuthorized
  return {
    available,
    packagesReadable,
    adminMounted,
    reason: available ? undefined : reason ?? (adminMounted ? 'principal_unauthorized' : 'admin_unmounted'),
  }
}

export function isTextPreviewMediaType(mediaType: string): boolean {
  const mt = mediaType.toLowerCase().split(';')[0].trim()
  return (
    mt.startsWith('text/') ||
    mt === 'application/json' ||
    mt === 'application/yaml' ||
    mt === 'application/x-yaml' ||
    mt === 'application/javascript' ||
    mt === 'application/typescript' ||
    mt.endsWith('+json') ||
    mt.endsWith('+yaml')
  )
}

export function isRasterImageMediaType(mediaType: string): boolean {
  const mt = mediaType.toLowerCase().split(';')[0].trim()
  return mt === 'image/png' || mt === 'image/jpeg' || mt === 'image/gif' || mt === 'image/webp'
}

export function isDangerousMarkupMediaType(mediaType: string): boolean {
  const mt = mediaType.toLowerCase().split(';')[0].trim()
  return mt === 'image/svg+xml' || mt === 'text/html' || mt === 'application/xhtml+xml'
}

export function isScriptResourcePath(path: string): boolean {
  const normalized = path.replace(/^\/+/, '')
  return normalized === 'scripts' || normalized.startsWith('scripts/')
}

/** Published Capability identity for Registry-only selection in the Skill editor. */
export interface CapabilityRegistryIdentity {
  key: string
  target: string
  version: string
  resolution: string
  risk: string
  capabilityType: 'tool' | 'workflow' | 'agent'
}

/**
 * Load published capability identities from the shared assistant-config surface.
 * Keys use `{type}:{name}` so free-text outside this set is rejected by the editor.
 * Does not embed Tool/Workflow/Agent editors.
 */
export async function listPublishedCapabilityIdentities(): Promise<CapabilityRegistryIdentity[]> {
  type ToolRow = { name?: string; enabled?: boolean; isSystem?: boolean }
  type WorkflowRow = {
    name?: string
    enabled?: boolean
    publishedVersionId?: string | null
    draftVersionId?: string | null
  }
  type AgentRow = {
    name?: string
    enabled?: boolean
    publishedVersionId?: string | null
    draftVersionId?: string | null
  }

  const [tools, workflows, agents] = await Promise.all([
    apiClient.get<ToolRow[]>('/api/assistant-config/tools').catch(() => [] as ToolRow[]),
    apiClient
      .get<WorkflowRow[]>('/api/assistant-config/workflows')
      .catch(() => [] as WorkflowRow[]),
    apiClient
      .get<AgentRow[]>('/api/assistant-config/agents')
      .catch(() => [] as AgentRow[]),
  ])

  const out: CapabilityRegistryIdentity[] = []

  for (const tool of tools || []) {
    if (!tool?.name || tool.enabled === false) continue
    out.push({
      key: `tool:${tool.name}`,
      target: tool.name,
      version: tool.isSystem ? 'system' : 'config',
      resolution: 'published',
      risk: 'read',
      capabilityType: 'tool',
    })
  }

  for (const workflow of workflows || []) {
    if (!workflow?.name || workflow.enabled === false) continue
    if (!workflow.publishedVersionId) continue
    out.push({
      key: `workflow:${workflow.name}`,
      target: workflow.name,
      version: workflow.publishedVersionId,
      resolution: 'pinned',
      risk: 'compute',
      capabilityType: 'workflow',
    })
  }

  for (const agent of agents || []) {
    if (!agent?.name || agent.enabled === false) continue
    if (!agent.publishedVersionId) continue
    out.push({
      key: `agent:${agent.name}`,
      target: agent.name,
      version: agent.publishedVersionId,
      resolution: 'pinned',
      risk: 'compute',
      capabilityType: 'agent',
    })
  }

  out.sort((a, b) => a.key.localeCompare(b.key))
  return out
}

export function diffSkillPackageVersions(
  packageId: string,
  leftVersionId: string,
  rightVersionId: string,
): Promise<Record<string, unknown>> {
  return apiClient.get<Record<string, unknown>>(
    `${SKILL_ADMIN_BASE}/skill-packages/${packageId}/versions/${leftVersionId}/diff/${rightVersionId}`,
    { headers: skillAdminOperatorHeaders() },
  )
}
