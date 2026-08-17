import { beforeEach, describe, expect, it, vi } from 'vitest'

import { parseReconciliationPage, reconcileCapabilityCall } from './reconciliation'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/api/client', () => ({ apiClient: api }))

describe('reconciliation typed client', () => {
  beforeEach(() => vi.clearAllMocks())

  it('serializes only the closed reconciliation request body', async () => {
    api.post.mockResolvedValue({
      callId: '00000000-0000-4000-8000-000000000001',
      decision: 'mark_failed',
      resultingCallStatus: 'failed',
      resultingCallRevision: 4,
      resultingRunRevision: 8,
      reconciliationId: '00000000-0000-4000-8000-000000000002',
      created: true,
    })

    await reconcileCapabilityCall('00000000-0000-4000-8000-000000000001', {
      expectedCallRevision: 3,
      expectedRunRevision: 7,
      decision: 'mark_failed',
      evidenceArtifactIds: ['00000000-0000-4000-8000-000000000003'],
      requestId: '00000000-0000-4000-8000-000000000004',
      reason: 'verified local outcome',
    })

    expect(api.post).toHaveBeenCalledWith(
      '/api/capability-calls/00000000-0000-4000-8000-000000000001/reconcile',
      {
        body: {
          expectedCallRevision: 3,
          expectedRunRevision: 7,
          decision: 'mark_failed',
          evidenceArtifactIds: ['00000000-0000-4000-8000-000000000003'],
          requestId: '00000000-0000-4000-8000-000000000004',
          reason: 'verified local outcome',
        },
      },
    )
  })

  it('parses only server-provided safe evidence Artifact references', () => {
    const evidenceArtifactId = '00000000-0000-4000-8000-000000000005'
    const page = parseReconciliationPage({
      items: [{
        callId: '00000000-0000-4000-8000-000000000001',
        runId: '00000000-0000-4000-8000-000000000002',
        status: 'needs_reconciliation',
        stateRevision: 3,
        runRevision: 7,
        failureCode: 'local_commit_outcome_unknown',
        executionMode: 'local_transactional',
        sideEffectStartedAt: '2026-08-14T00:00:00Z',
        createdAt: '2026-08-14T00:00:00Z',
        updatedAt: '2026-08-14T00:01:00Z',
        attemptCount: 1,
        evidenceRequired: true,
        evidenceArtifactIds: [evidenceArtifactId],
      }],
      total: 1,
    })

    expect(page.items[0].evidenceArtifactIds).toEqual([evidenceArtifactId])
    expect(() => parseReconciliationPage({
      items: [{
        callId: '00000000-0000-4000-8000-000000000001',
        runId: '00000000-0000-4000-8000-000000000002',
        status: 'needs_reconciliation',
        stateRevision: 3,
        runRevision: 7,
        failureCode: null,
        executionMode: 'local_transactional',
        sideEffectStartedAt: null,
        createdAt: null,
        updatedAt: null,
        attemptCount: 1,
        evidenceRequired: true,
        evidenceArtifactIds: ['sentinel-artifact'],
      }],
      total: 1,
    })).toThrow('invalid_reconciliation_response')
  })
})
