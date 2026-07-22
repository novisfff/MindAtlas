/**
 * Skill evaluation + publish gate API client (Plan 09 Task 9).
 * Mounted only under trusted Plan 09 guard with skill-admin.
 * Client never authors digests — server resolves all pins.
 */
import { apiClient } from '@/lib/api/client'
import { withMindAtlasLocale } from '@/lib/api/locale'
import { SSEParser } from '@/lib/sse/SSEParser'
import { skillAdminOperatorHeaders } from './skill-packages'

export const SKILL_EVAL_BASE = '/api/assistant-config/skill-eval'

export type EvalRunMode = 'interactive_scripted' | 'dataset_scripted' | 'dataset_live'
export type EvalSubjectKind =
  | 'skill_draft'
  | 'skill_version'
  | 'main_agent_profile_draft'
  | 'main_agent_profile_version'
  | 'legacy_baseline'

export type PublishGateDecision = 'passed' | 'failed' | 'waived_non_safety'
export type PublishGateAction =
  | 'skill_publish'
  | 'skill_catalog_enable'
  | 'profile_publish'
  | 'profile_runtime_enable'

/**
 * Client admission body — identity + workbench inputs only.
 * Never includes content/binding/environment digests.
 */
export interface CreateEvalRunRequest {
  requestId: string
  subjectKind: EvalSubjectKind
  subjectAggregateId: string
  subjectVersionId: string
  prompt: string
  locale: string
  profileVersionId: string
  mode: EvalRunMode
  datasetVersionIds?: string[]
  providerFixtureRevision?: string | null
  liveModelId?: string | null
}

export interface EvalRunSummary {
  id: string
  subjectKind: EvalSubjectKind
  subjectAggregateId: string
  subjectVersionId: string
  mode: EvalRunMode
  status: string
  stateRevision: number
  lastEventSeq: number
  failureCode?: string | null
  gateEligible?: boolean
  evidenceProvenance?: string | null
  /** Server-authoritative aggregate metrics when present on the run row. */
  aggregateMetrics?: Record<string, unknown>
  createdAt?: string | null
  startedAt?: string | null
  endedAt?: string | null
}

export interface EvalEventSummary {
  sequence: number
  eventType: string
  payload: Record<string, unknown>
  createdAt?: string | null
}

export interface DatasetSummary {
  id: string
  stableKey: string
  displayName: string
  ownership?: string
  aggregateRevision?: number
  draftVersionId?: string | null
  publishedVersionId?: string | null
}

export interface DatasetVersionSummary {
  id: string
  datasetId: string
  sequence: number
  versionName: string
  schemaVersion: number
  contentDigest: string
  sourceFixtureRevision?: string | null
  caseCount: number
  createdBy?: string | null
  createdAt?: string | null
}

export interface CaseResultSummary {
  id: string
  evalRunId: string
  evalCaseId: string
  resultState: string
  assertionDetails: Record<string, unknown>
  actualActiveSkills: string[]
  stopReason?: string | null
  outputArtifactIds: string[]
  evidenceArtifactIds: string[]
  rounds?: number | null
  calls?: number | null
  tokens?: number | null
  latencyMs?: number | null
  safeError?: string | null
  resultDigest: string
  createdAt?: string | null
}

export interface ArtifactSummary {
  id: string
  evalRunId: string
  kind: string
  mediaType: string
  storageKind: string
  contentDigest?: string | null
  byteSize?: number | null
  label?: string | null
  createdAt?: string | null
}

export interface CapabilityCallSummary {
  id: string
  evalCallId: string
  evalCaseId: string
  logicalCallKey: string
  attempt: number
  outcome: string
  bindingDigest?: string | null
  policyDigest?: string | null
}

export interface EvalRunEvidence {
  runId: string
  gateEligible: boolean
  evidenceProvenance?: string | null
  artifacts: ArtifactSummary[]
  capabilityCalls: CapabilityCallSummary[]
}

export interface QualifyingEvidenceSummary {
  evalRunId: string
  mode: string
  status: string
  gateEligible: boolean
  evidenceProvenance: string
  subjectKind: string
  subjectVersionId: string
  providerFixtureRevision?: string | null
  providerFixtureDigest?: string | null
  aggregateMetrics: Record<string, unknown>
}

export interface PublishGateSubject {
  schemaVersion: 1
  subject: {
    schemaVersion: 1
    kind: EvalSubjectKind
    aggregateId: string
    versionId: string
    contentDigest: string
    resolvedBindingDigest: string
  }
  profileDigest: string
  catalogDigest: string
  runtimeContractVersion: number
  policyVersion: string
  thresholdVersion: string
  datasetVersionIds: string[]
  buildRevision: string
}

export interface PublishGateSummary {
  id: string
  decision: PublishGateDecision
  subjectKind: string
  subjectAggregateId: string
  subjectVersionId: string
  /** Server-stored action this gate authorizes (publish vs enable are distinct). */
  action?: PublishGateAction | string | null
  expiresAt?: string | null
  waiverCodes: string[]
  requestId?: string | null
  createdAt?: string | null
}

export interface CreateGateResponse {
  gate: PublishGateSummary
  decision: PublishGateDecision
  acceptedWaiverCodes: string[]
  assertionSnapshot: Record<string, unknown>
  metricSnapshot: Record<string, unknown>
}

/** UI record for a single action+subject-version gate, never reused across actions. */
export interface GateUiState {
  gateId: string
  action: PublishGateAction
  subjectAggregateId: string
  subjectVersionId: string
  subjectKind: string
  decision: PublishGateDecision
  requestId?: string | null
  assertionSnapshot: Record<string, unknown>
  metricSnapshot: Record<string, unknown>
  createdAt?: string | null
}

export function gateUiStateFromResponse(
  result: CreateGateResponse,
  action: PublishGateAction,
): GateUiState {
  return {
    gateId: result.gate.id,
    action: (result.gate.action as PublishGateAction | undefined) ?? action,
    subjectAggregateId: result.gate.subjectAggregateId,
    subjectVersionId: result.gate.subjectVersionId,
    subjectKind: result.gate.subjectKind,
    decision: result.decision,
    requestId: result.gate.requestId,
    assertionSnapshot: { ...(result.assertionSnapshot || {}) },
    metricSnapshot: { ...(result.metricSnapshot || {}) },
    createdAt: result.gate.createdAt,
  }
}

/** True when a completed run is usable as gate evidence for the exact subject. */
export function isQualifyingGateRun(run: {
  id?: string | null
  status?: string | null
  gateEligible?: boolean | null
  evidenceProvenance?: string | null
  subjectKind?: string | null
  subjectAggregateId?: string | null
  subjectVersionId?: string | null
} | null | undefined, subject: {
  subjectKind: string
  subjectAggregateId: string
  subjectVersionId: string
}): boolean {
  if (!run?.id) return false
  if (run.status !== 'completed') return false
  if (!run.gateEligible) return false
  if (run.evidenceProvenance !== 'real_orchestration') return false
  if (run.subjectKind !== subject.subjectKind) return false
  if (run.subjectAggregateId !== subject.subjectAggregateId) return false
  if (run.subjectVersionId !== subject.subjectVersionId) return false
  return true
}

export type EvalStreamTerminalReason = 'closed' | 'transport_failure' | 'aborted'

export interface StreamEvalRunEventsOptions {
  afterSequence?: number
  signal?: AbortSignal
  onEvent?: (event: EvalEventSummary) => void
  onHeartbeat?: (payload: Record<string, unknown>) => void
  onError?: (error: Error) => void
}

/** Validate mode/input combinations and strip any accidental digest fields. */
export function buildCreateEvalRunBody(request: CreateEvalRunRequest): CreateEvalRunRequest {
  const mode = request.mode
  const datasetVersionIds = [...(request.datasetVersionIds ?? [])]
  const providerFixtureRevision = request.providerFixtureRevision?.trim() || null
  const liveModelId = request.liveModelId?.trim() || null

  if (!request.requestId?.trim()) throw new Error('requestId is required')
  if (!request.prompt?.trim()) throw new Error('prompt is required')
  if (!request.locale?.trim()) throw new Error('locale is required')
  if (!request.profileVersionId?.trim()) throw new Error('profileVersionId is required')
  if (!request.subjectAggregateId?.trim() || !request.subjectVersionId?.trim()) {
    throw new Error('subject identity is required')
  }

  if (mode === 'dataset_scripted') {
    if (datasetVersionIds.length === 0) {
      throw new Error('dataset_scripted requires a published dataset version')
    }
    if (!providerFixtureRevision) {
      throw new Error('dataset_scripted requires providerFixtureRevision')
    }
    if (liveModelId) {
      throw new Error('dataset_scripted forbids liveModelId')
    }
  }
  if (mode === 'dataset_live') {
    if (datasetVersionIds.length === 0 || !liveModelId) {
      throw new Error('dataset_live requires dataset versions and liveModelId')
    }
    if (providerFixtureRevision) {
      throw new Error('dataset_live forbids providerFixtureRevision')
    }
  }
  if (mode === 'interactive_scripted' && liveModelId) {
    throw new Error('interactive_scripted forbids liveModelId')
  }

  return {
    requestId: request.requestId,
    subjectKind: request.subjectKind,
    subjectAggregateId: request.subjectAggregateId,
    subjectVersionId: request.subjectVersionId,
    prompt: request.prompt,
    locale: request.locale,
    profileVersionId: request.profileVersionId,
    mode,
    datasetVersionIds,
    providerFixtureRevision,
    liveModelId,
  }
}

export function listEvalDatasets(): Promise<{ items: DatasetSummary[]; total: number }> {
  return apiClient.get(`${SKILL_EVAL_BASE}/datasets`, { headers: skillAdminOperatorHeaders() })
}

export function listDatasetVersions(
  datasetId: string,
): Promise<{ items: DatasetVersionSummary[]; total: number }> {
  return apiClient.get(`${SKILL_EVAL_BASE}/datasets/${datasetId}/versions`, {
    headers: skillAdminOperatorHeaders(),
  })
}

export function createEvalRun(body: CreateEvalRunRequest): Promise<EvalRunSummary> {
  const safe = buildCreateEvalRunBody(body)
  return apiClient.post(`${SKILL_EVAL_BASE}/runs`, {
    body: safe,
    headers: skillAdminOperatorHeaders(),
  })
}

export function getEvalRun(runId: string): Promise<EvalRunSummary> {
  return apiClient.get(`${SKILL_EVAL_BASE}/runs/${runId}`, { headers: skillAdminOperatorHeaders() })
}

export function listEvalRunEvents(
  runId: string,
  params?: { afterSequence?: number; limit?: number },
): Promise<{ items: EvalEventSummary[]; afterSequence: number; nextSequence: number }> {
  return apiClient.get(`${SKILL_EVAL_BASE}/runs/${runId}/events`, {
    query: {
      afterSequence: params?.afterSequence ?? 0,
      limit: params?.limit ?? 100,
    },
    headers: skillAdminOperatorHeaders(),
  })
}

export function cancelEvalRun(
  runId: string,
  body: { requestId: string; expectedStateRevision: number },
): Promise<EvalRunSummary> {
  return apiClient.post(`${SKILL_EVAL_BASE}/runs/${runId}/cancel`, {
    body: {
      requestId: body.requestId,
      expectedStateRevision: body.expectedStateRevision,
    },
    headers: skillAdminOperatorHeaders(),
  })
}

export function listEvalRunCaseResults(
  runId: string,
  params?: { limit?: number },
): Promise<{ items: CaseResultSummary[]; total: number }> {
  return apiClient.get(`${SKILL_EVAL_BASE}/runs/${runId}/case-results`, {
    query: { limit: params?.limit ?? 200 },
    headers: skillAdminOperatorHeaders(),
  })
}

export function listEvalRunEvidence(
  runId: string,
  params?: { limit?: number },
): Promise<EvalRunEvidence> {
  return apiClient.get(`${SKILL_EVAL_BASE}/runs/${runId}/evidence`, {
    query: { limit: params?.limit ?? 200 },
    headers: skillAdminOperatorHeaders(),
  })
}

export function listQualifyingEvidence(params?: {
  subjectKind?: string
  subjectAggregateId?: string
  subjectVersionId?: string
  limit?: number
}): Promise<{ items: QualifyingEvidenceSummary[]; total: number }> {
  return apiClient.get(`${SKILL_EVAL_BASE}/qualifying-evidence`, {
    query: {
      subjectKind: params?.subjectKind,
      subjectAggregateId: params?.subjectAggregateId,
      subjectVersionId: params?.subjectVersionId,
      limit: params?.limit ?? 50,
    },
    headers: skillAdminOperatorHeaders(),
  })
}

/**
 * Fetch-stream SSE client for eval events.
 * Carries afterSequence; heartbeats are separate; returns terminal reason.
 * Polling is only a fallback after transport_failure.
 */
export async function streamEvalRunEvents(
  runId: string,
  options: StreamEvalRunEventsOptions = {},
): Promise<EvalStreamTerminalReason> {
  const afterSequence = options.afterSequence ?? 0
  const url = `${SKILL_EVAL_BASE}/runs/${runId}/events/stream?afterSequence=${afterSequence}`
  const headers = withMindAtlasLocale(skillAdminOperatorHeaders())
  headers.set('accept', 'text/event-stream')

  let response: Response
  try {
    response = await fetch(url, {
      method: 'GET',
      headers,
      signal: options.signal,
    })
  } catch (error) {
    if (options.signal?.aborted) return 'aborted'
    options.onError?.(error instanceof Error ? error : new Error(String(error)))
    return 'transport_failure'
  }

  if (!response.ok || !response.body) {
    const message = `SSE transport failed: HTTP ${response.status}`
    options.onError?.(new Error(message))
    return 'transport_failure'
  }

  const parser = new SSEParser()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  try {
    while (true) {
      if (options.signal?.aborted) return 'aborted'
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        chunk = await reader.read()
      } catch (error) {
        if (options.signal?.aborted) return 'aborted'
        options.onError?.(error instanceof Error ? error : new Error(String(error)))
        return 'transport_failure'
      }
      if (chunk.done) break
      const text = decoder.decode(chunk.value, { stream: true })
      const events = parser.parse(text)
      for (const evt of events) {
        if (evt.event === 'heartbeat') {
          options.onHeartbeat?.((evt.data || {}) as Record<string, unknown>)
          continue
        }
        if (evt.event === 'error') {
          const data = (evt.data || {}) as Record<string, unknown>
          options.onError?.(
            new Error(typeof data.message === 'string' ? data.message : 'SSE stream error'),
          )
          return 'transport_failure'
        }
        const data = (evt.data || {}) as Record<string, unknown>
        const sequence =
          typeof data.sequence === 'number'
            ? data.sequence
            : Number.parseInt(String(data.sequence ?? ''), 10)
        if (!Number.isFinite(sequence)) continue
        const eventType =
          typeof data.eventType === 'string' && data.eventType
            ? data.eventType
            : evt.event || 'message'
        const payload =
          data.payload && typeof data.payload === 'object' && !Array.isArray(data.payload)
            ? (data.payload as Record<string, unknown>)
            : {}
        options.onEvent?.({
          sequence,
          eventType,
          payload,
        })
      }
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // ignore
    }
  }

  return options.signal?.aborted ? 'aborted' : 'closed'
}

/**
 * Create a server-derived publish gate.
 * Client MUST NOT send passed/decision/metrics/assertions — only action + identity + evidence refs.
 */
export function createPublishGate(body: {
  requestId: string
  action: PublishGateAction
  subjectAggregateId: string
  subjectVersionId: string
  qualifyingEvalRunIds: string[]
  requestedNonSafetyWaiverCodes?: string[]
  waiverReason?: string | null
}): Promise<CreateGateResponse> {
  const safe = {
    requestId: body.requestId,
    action: body.action,
    subjectAggregateId: body.subjectAggregateId,
    subjectVersionId: body.subjectVersionId,
    qualifyingEvalRunIds: body.qualifyingEvalRunIds,
    requestedNonSafetyWaiverCodes: body.requestedNonSafetyWaiverCodes ?? [],
    waiverReason: body.waiverReason ?? null,
  }
  return apiClient.post(`${SKILL_EVAL_BASE}/gates`, {
    body: safe,
    headers: skillAdminOperatorHeaders(),
  })
}

export function getPublishGate(gateId: string): Promise<{
  gate: PublishGateSummary
  assertionSnapshot: Record<string, unknown>
  metricSnapshot: Record<string, unknown>
}> {
  return apiClient.get(`${SKILL_EVAL_BASE}/gates/${gateId}`, {
    headers: skillAdminOperatorHeaders(),
  })
}
