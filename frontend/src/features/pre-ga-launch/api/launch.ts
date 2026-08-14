import { apiClient } from '@/lib/api/client'

export type EvidenceKind = 'automated_qualification' | 'production_rehearsal'

export interface EvidenceRef {
  schemaVersion: 1
  evidenceKind: EvidenceKind
  manifestDigest: string
  attestationDigest: string
}

export interface CreateLaunchCandidateInput {
  automatedEvidenceRef: EvidenceRef
  rehearsalEvidenceRef: EvidenceRef
  requestId: string
  reason: string
}

export interface ConsumeLaunchCandidateInput {
  expectedControlRevision: number
  requestId: string
  reason: string
}

export type LaunchState = 'unapproved' | 'current' | 'stale' | 'evidence_unavailable'
export type CandidateState =
  | 'passing_unused'
  | 'failed'
  | 'expired_unused'
  | 'consumed_current'
  | 'consumed_stale'

export interface LaunchCandidate {
  candidateId: string
  passed: boolean
  failureCodes: string[]
  qualificationTargetDigest: string
  subjectDigest: string
  buildRevision: string
  imageSetDigest: string
  deployedArtifactSetDigest: string
  schemaFamily: string
  schemaRevision: string
  schemaRuntimeIdentityDigest: string
  rolloutRevisionId: string
  profileVersionId: string
  modelId: string
  runtimeClosureDigest: string
  automatedEvidenceManifestDigest: string
  rehearsalEvidenceManifestDigest: string
  operationalSnapshotDigest: string
  unknownCallCount: number
  needsReconciliationCount: number
  activeRunCount: number
  issuedAt: string | null
  expiresAt: string | null
  usedAt: string | null
  resultingControlRevision: number | null
  active: boolean
}

export interface LaunchStatus {
  launched: boolean
  reasonCode: string | null
  controlRevision: number
  activeSubjectDigest: string | null
  candidate: LaunchCandidate | null
}

export interface LaunchCandidatesPage {
  items: LaunchCandidate[]
  nextCursor: { issuedAt: string; id: string } | null
}

export interface LaunchConsumptionResult {
  controlRevision: number
  launchedAt: string | null
  gateUseId: string
  candidate: LaunchCandidate | null
}

export class ControlPlaneResponseError extends Error {
  constructor() {
    super('invalid_control_plane_response')
    this.name = 'ControlPlaneResponseError'
  }
}

const DIGEST = /^[0-9a-f]{64}$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SAFE_CODE = /^[a-z][a-z0-9_]{0,95}$/

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ControlPlaneResponseError()
  return value as Record<string, unknown>
}

function string(value: unknown, pattern?: RegExp): string {
  if (typeof value !== 'string' || value.length === 0 || (pattern && !pattern.test(value))) {
    throw new ControlPlaneResponseError()
  }
  return value
}

function nullableString(value: unknown, pattern?: RegExp): string | null {
  if (value == null) return null
  return string(value, pattern)
}

function nonNegative(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw new ControlPlaneResponseError()
  return value
}

function timestamp(value: unknown): string | null {
  const result = nullableString(value)
  if (result !== null && !Number.isFinite(Date.parse(result))) throw new ControlPlaneResponseError()
  return result
}

function candidateState(candidate: LaunchCandidate): CandidateState {
  if (!candidate.passed) return 'failed'
  if (candidate.usedAt) return candidate.active ? 'consumed_current' : 'consumed_stale'
  if (candidate.expiresAt && Date.parse(candidate.expiresAt) <= Date.now()) return 'expired_unused'
  return 'passing_unused'
}

export function classifyLaunchCandidate(candidate: LaunchCandidate): CandidateState {
  return candidateState(candidate)
}

export function parseLaunchCandidate(value: unknown): LaunchCandidate {
  const raw = object(value)
  const failureCodes = raw.failureCodes
  if (!Array.isArray(failureCodes) || failureCodes.length > 32 || failureCodes.some((item) => typeof item !== 'string' || !SAFE_CODE.test(item))) {
    throw new ControlPlaneResponseError()
  }
  return {
    candidateId: string(raw.candidateId, UUID),
    passed: raw.passed === true || raw.passed === false ? raw.passed : (() => { throw new ControlPlaneResponseError() })(),
    failureCodes: [...failureCodes],
    qualificationTargetDigest: string(raw.qualificationTargetDigest, DIGEST),
    subjectDigest: string(raw.subjectDigest, DIGEST),
    buildRevision: string(raw.buildRevision),
    imageSetDigest: string(raw.imageSetDigest, DIGEST),
    deployedArtifactSetDigest: string(raw.deployedArtifactSetDigest, DIGEST),
    schemaFamily: string(raw.schemaFamily),
    schemaRevision: string(raw.schemaRevision),
    schemaRuntimeIdentityDigest: string(raw.schemaRuntimeIdentityDigest, DIGEST),
    rolloutRevisionId: string(raw.rolloutRevisionId, UUID),
    profileVersionId: string(raw.profileVersionId, UUID),
    modelId: string(raw.modelId, UUID),
    runtimeClosureDigest: string(raw.runtimeClosureDigest, DIGEST),
    automatedEvidenceManifestDigest: string(raw.automatedEvidenceManifestDigest, DIGEST),
    rehearsalEvidenceManifestDigest: string(raw.rehearsalEvidenceManifestDigest, DIGEST),
    operationalSnapshotDigest: string(raw.operationalSnapshotDigest, DIGEST),
    unknownCallCount: nonNegative(raw.unknownCallCount),
    needsReconciliationCount: nonNegative(raw.needsReconciliationCount),
    activeRunCount: nonNegative(raw.activeRunCount),
    issuedAt: timestamp(raw.issuedAt),
    expiresAt: timestamp(raw.expiresAt),
    usedAt: timestamp(raw.usedAt),
    resultingControlRevision: raw.resultingControlRevision == null ? null : nonNegative(raw.resultingControlRevision),
    active: raw.active === true,
  }
}

export function parseLaunchStatus(value: unknown): LaunchStatus {
  const raw = object(value)
  if (typeof raw.launched !== 'boolean') throw new ControlPlaneResponseError()
  const candidate = raw.candidate == null ? null : parseLaunchCandidate(raw.candidate)
  return {
    launched: raw.launched,
    reasonCode: nullableString(raw.reasonCode, SAFE_CODE),
    controlRevision: nonNegative(raw.controlRevision),
    activeSubjectDigest: nullableString(raw.activeSubjectDigest, DIGEST),
    candidate,
  }
}

function parseCandidatesPage(value: unknown): LaunchCandidatesPage {
  const raw = object(value)
  if (!Array.isArray(raw.items)) throw new ControlPlaneResponseError()
  const cursor = raw.nextCursor == null ? null : object(raw.nextCursor)
  return {
    items: raw.items.map(parseLaunchCandidate),
    nextCursor: cursor
      ? { issuedAt: string(cursor.issuedAt), id: string(cursor.id, UUID) }
      : null,
  }
}

export function getLaunchStatus(): Promise<LaunchStatus> {
  return apiClient.get<unknown>('/api/pre-ga-launch/status').then(parseLaunchStatus)
}

export function listLaunchCandidates(): Promise<LaunchCandidatesPage> {
  return apiClient.get<unknown>('/api/pre-ga-launch/candidates').then(parseCandidatesPage)
}

export function createLaunchCandidate(input: CreateLaunchCandidateInput): Promise<LaunchCandidate> {
  return apiClient
    .post<unknown>('/api/pre-ga-launch/candidates', {
      body: {
        automatedEvidenceRef: input.automatedEvidenceRef,
        rehearsalEvidenceRef: input.rehearsalEvidenceRef,
        requestId: input.requestId,
        reason: input.reason,
      },
    })
    .then(parseLaunchCandidate)
}

export function consumeLaunchCandidate(
  candidateId: string,
  input: ConsumeLaunchCandidateInput,
): Promise<LaunchConsumptionResult> {
  return apiClient
    .post<unknown>(`/api/pre-ga-launch/candidates/${candidateId}/consume`, {
      body: {
        expectedControlRevision: input.expectedControlRevision,
        requestId: input.requestId,
        reason: input.reason,
      },
    })
    .then((value) => {
      const raw = object(value)
      return {
        controlRevision: nonNegative(raw.controlRevision),
        launchedAt: timestamp(raw.launchedAt),
        gateUseId: string(raw.gateUseId, UUID),
        candidate: raw.candidate == null ? null : parseLaunchCandidate(raw.candidate),
      }
    })
}

export { candidateState }
