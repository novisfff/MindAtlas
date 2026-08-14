import { beforeEach, describe, expect, it, vi } from 'vitest'

import { reconcileCapabilityCall } from './reconciliation'

const api = vi.hoisted(() => ({ post: vi.fn() }))
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
})
