import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  consumeLaunchCandidate,
  createLaunchCandidate,
  listLaunchCandidates,
  parseLaunchCandidate,
  parseQualificationTarget,
  parseLaunchStatus,
} from './launch'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/lib/api/client', () => ({ apiClient: api }))

const candidate = {
  candidateId: '00000000-0000-4000-8000-000000000001',
  passed: true,
  failureCodes: [],
  qualificationTargetDigest: '1'.repeat(64),
  subjectDigest: '2'.repeat(64),
  buildRevision: 'build-safe',
  imageSetDigest: '3'.repeat(64),
  deployedArtifactSetDigest: '4'.repeat(64),
  schemaFamily: 'pre_ga_v1',
  schemaRevision: 'pre_ga_v1_0002',
  schemaRuntimeIdentityDigest: '5'.repeat(64),
  rolloutRevisionId: '00000000-0000-4000-8000-000000000002',
  profileVersionId: '00000000-0000-4000-8000-000000000003',
  modelId: '00000000-0000-4000-8000-000000000004',
  runtimeClosureDigest: '6'.repeat(64),
  automatedEvidenceManifestDigest: '7'.repeat(64),
  rehearsalEvidenceManifestDigest: '8'.repeat(64),
  operationalSnapshotDigest: '9'.repeat(64),
  unknownCallCount: 0,
  needsReconciliationCount: 0,
  activeRunCount: 0,
  issuedAt: '2026-08-14T00:00:00Z',
  expiresAt: '2099-08-14T00:00:00Z',
  usedAt: null,
  resultingControlRevision: null,
  active: false,
}

describe('pre-GA launch typed client', () => {
  beforeEach(() => vi.clearAllMocks())

  it('serializes only evidence refs, request ID, and reason for candidate creation', async () => {
    api.post.mockResolvedValue(candidate)
    await createLaunchCandidate({
      automatedEvidenceRef: {
        schemaVersion: 1,
        evidenceKind: 'automated_qualification',
        manifestDigest: 'a'.repeat(64),
        attestationDigest: 'b'.repeat(64),
      },
      rehearsalEvidenceRef: {
        schemaVersion: 1,
        evidenceKind: 'production_rehearsal',
        manifestDigest: 'c'.repeat(64),
        attestationDigest: 'd'.repeat(64),
      },
      requestId: '00000000-0000-4000-8000-000000000010',
      reason: 'reviewed evidence',
    })

    expect(api.post).toHaveBeenCalledWith('/api/pre-ga-launch/candidates', {
      body: {
        automatedEvidenceRef: {
          schemaVersion: 1,
          evidenceKind: 'automated_qualification',
          manifestDigest: 'a'.repeat(64),
          attestationDigest: 'b'.repeat(64),
        },
        rehearsalEvidenceRef: {
          schemaVersion: 1,
          evidenceKind: 'production_rehearsal',
          manifestDigest: 'c'.repeat(64),
          attestationDigest: 'd'.repeat(64),
        },
        requestId: '00000000-0000-4000-8000-000000000010',
        reason: 'reviewed evidence',
      },
    })
  })

  it('serializes only CAS revision, request ID, and reason for consumption', async () => {
    api.post.mockResolvedValue({
      controlRevision: 5,
      launchedAt: '2026-08-14T00:00:00Z',
      gateUseId: '00000000-0000-4000-8000-000000000005',
      candidate,
    })
    await consumeLaunchCandidate(candidate.candidateId, {
      expectedControlRevision: 4,
      requestId: '00000000-0000-4000-8000-000000000011',
      reason: 'consume reviewed candidate',
    })

    expect(api.post).toHaveBeenCalledWith(
      `/api/pre-ga-launch/candidates/${candidate.candidateId}/consume`,
      {
        body: {
          expectedControlRevision: 4,
          requestId: '00000000-0000-4000-8000-000000000011',
          reason: 'consume reviewed candidate',
        },
      },
    )
  })

  it('serializes the server cursor when loading the next candidate page', async () => {
    api.get.mockResolvedValue({ items: [candidate], nextCursor: null })
    await listLaunchCandidates({ issuedAt: '2026-08-14T00:00:00Z', id: candidate.candidateId }, 25)

    expect(api.get).toHaveBeenCalledWith('/api/pre-ga-launch/candidates', {
      query: {
        limit: 25,
        cursorIssuedAt: '2026-08-14T00:00:00Z',
        cursorId: candidate.candidateId,
      },
    })
  })

  it('parses safe launch control identity and timing fields', () => {
    const parsed = parseLaunchStatus({
      launched: true,
      reasonCode: null,
      controlRevision: 5,
      activeSubjectDigest: 'a'.repeat(64),
      activeCandidateId: candidate.candidateId,
      activeGateUseId: '00000000-0000-4000-8000-000000000006',
      launchedAt: '2026-08-14T00:00:00Z',
      updatedAt: '2026-08-14T00:01:00Z',
      candidate,
    })

    expect(parsed.activeCandidateId).toBe(candidate.candidateId)
    expect(parsed.activeGateUseId).toBe('00000000-0000-4000-8000-000000000006')
    expect(parsed.launchedAt).toBe('2026-08-14T00:00:00Z')
  })

  it('parses the server-owned qualification target summary and rejects drifted shapes', () => {
    const target = parseQualificationTarget({
      schemaVersion: 1,
      buildRevision: 'build-safe',
      imageSetDigest: '1'.repeat(64),
      deployedArtifactSetDigest: '2'.repeat(64),
      schemaFamily: 'pre_ga_v1',
      schemaRevision: 'pre_ga_v1_0002',
      productionSchemaDeploymentClass: 'production',
      productionSchemaRuntimeIdentityDigest: '3'.repeat(64),
      rolloutRevisionId: '00000000-0000-4000-8000-000000000001',
      profileVersionId: '00000000-0000-4000-8000-000000000002',
      modelId: '00000000-0000-4000-8000-000000000003',
      runtimeClosureDigest: '4'.repeat(64),
      dependencyLockSetDigest: '5'.repeat(64),
      scenarioSetDigest: '6'.repeat(64),
      requiredAssertionSetDigest: '7'.repeat(64),
      runnerIdentityDigest: '8'.repeat(64),
      evidenceTrustSetDigest: '9'.repeat(64),
      qualificationTargetDigest: 'a'.repeat(64),
    })

    expect(target.schemaRevision).toBe('pre_ga_v1_0002')
    expect(target.qualificationTargetDigest).toBe('a'.repeat(64))
    expect(() => parseQualificationTarget({ ...target, imageSetDigest: 'sentinel' })).toThrow(
      'invalid_control_plane_response',
    )
  })

  it('rejects a candidate with a drifted production schema identity', () => {
    expect(() => parseLaunchCandidate({ ...candidate, schemaFamily: 'pre_ga' })).toThrow(
      'invalid_control_plane_response',
    )
  })
})
