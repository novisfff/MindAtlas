/**
 * Skill evaluation + publish gate API client (Plan 09 Task 7).
 * Mounted only under trusted Plan 09 guard with skill-admin.
 */
import { apiClient } from '@/lib/api/client'
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
  draftVersionId?: string | null
  publishedVersionId?: string | null
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

export function listEvalDatasets(): Promise<{ items: DatasetSummary[]; total: number }> {
  return apiClient.get(`${SKILL_EVAL_BASE}/datasets`, { headers: skillAdminOperatorHeaders() })
}

export function createEvalRun(body: {
  requestId?: string | null
  subjectKind: EvalSubjectKind
  subjectAggregateId: string
  subjectVersionId: string
  subjectContentDigest: string
  subjectBindingDigest: string
  datasetVersionIds?: string[]
  thresholdPolicyVersion?: string
  mode?: EvalRunMode
  requiredBuildRevision?: string
  isolationDigest?: string
}): Promise<EvalRunSummary> {
  return apiClient.post(`${SKILL_EVAL_BASE}/runs`, {
    body,
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
  body?: { requestId?: string | null; expectedStateRevision?: number | null },
): Promise<EvalRunSummary> {
  return apiClient.post(`${SKILL_EVAL_BASE}/runs/${runId}/cancel`, {
    body: body ?? {},
    headers: skillAdminOperatorHeaders(),
  })
}

/**
 * Create a server-derived publish gate.
 * Client MUST NOT send passed/decision/metrics/assertions — only evidence refs.
 */
export function createPublishGate(body: {
  requestId: string
  subject: PublishGateSubject
  qualifyingEvalRunIds: string[]
  requestedNonSafetyWaiverCodes?: string[]
  waiverReason?: string | null
}): Promise<CreateGateResponse> {
  // Strip any accidental client decision fields.
  const safe = {
    requestId: body.requestId,
    subject: body.subject,
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
