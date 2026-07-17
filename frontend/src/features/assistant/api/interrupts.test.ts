import { beforeEach, describe, expect, it, vi } from 'vitest'

const getMock = vi.fn()
const postMock = vi.fn()

vi.mock('@/lib/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
  },
  isApiError: (error: unknown) =>
    Boolean(error && typeof error === 'object' && (error as { name?: string }).name === 'ApiError'),
}))

import {
  getInterruptDetail,
  listPendingInterrupts,
  resolveInterrupt,
  rotateInterruptToken,
} from './index'

describe('durable interrupt API', () => {
  beforeEach(() => {
    getMock.mockReset()
    postMock.mockReset()
  })

  it('lists pending interrupts and normalizes fields', async () => {
    getMock.mockResolvedValueOnce([
      {
        interruptId: 'int-1',
        runId: 'run-1',
        conversationId: 'conv-1',
        messageId: 'msg-1',
        status: 'pending',
        kind: 'approval',
        requestRevision: 1,
        runRevision: 2,
        tokenRevision: 0,
        expiresAt: '2026-07-17T00:00:00Z',
        allowedActions: ['approve', 'reject', 'token', 'resolve'],
        fields: [{ name: 'summary', type: 'textarea', label: 'Summary', required: true }],
        requestPayload: { title: 'Review' },
        initialValues: { summary: 'x' },
        nodeId: 'n1',
        nodeVisitId: 'nv1',
        resolvedAt: null,
      },
    ])

    const items = await listPendingInterrupts('conv-1', 'run-1')
    expect(getMock).toHaveBeenCalledWith(
      '/api/assistant/conversations/conv-1/runs/run-1/interrupts/pending',
    )
    expect(items).toHaveLength(1)
    expect(items[0].source).toBe('durable')
    expect(items[0].fields[0]).toMatchObject({
      name: 'summary',
      type: 'string',
      widget: 'textarea',
      required: true,
    })
  })

  it('gets interrupt detail and exposes terminal resolutionRequestId only', async () => {
    getMock.mockResolvedValueOnce({
      interruptId: 'int-1',
      runId: 'run-1',
      conversationId: 'conv-1',
      status: 'approved',
      kind: 'approval',
      requestRevision: 1,
      runRevision: 2,
      tokenRevision: 1,
      fields: [],
      requestPayload: {},
      initialValues: {},
      nodeId: 'n1',
      nodeVisitId: 'nv1',
      resolvedAt: '2026-07-16T12:00:00Z',
      resolutionRequestId: 'rr-win',
      resolutionDigest: 'must-not-leak',
      submittedValues: { secret: true },
      comment: 'secret',
    })

    const detail = await getInterruptDetail('conv-1', 'run-1', 'int-1')
    expect(getMock).toHaveBeenCalledWith(
      '/api/assistant/conversations/conv-1/runs/run-1/interrupts/int-1',
    )
    expect(detail.resolutionRequestId).toBe('rr-win')
    expect((detail as any).resolutionDigest).toBeUndefined()
    expect((detail as any).submittedValues).toBeUndefined()
    expect((detail as any).comment).toBeUndefined()
  })

  it('rotates token with expected revisions', async () => {
    postMock.mockResolvedValueOnce({ token: 'raw-token-in-memory', tokenRevision: 3 })
    const result = await rotateInterruptToken('conv-1', 'run-1', 'int-1', {
      expectedRequestRevision: 1,
      expectedRunRevision: 2,
    })
    expect(postMock).toHaveBeenCalledWith(
      '/api/assistant/conversations/conv-1/runs/run-1/interrupts/int-1/token',
      {
        body: {
          expectedRequestRevision: 1,
          expectedRunRevision: 2,
        },
      },
    )
    expect(result.token).toBe('raw-token-in-memory')
    expect(result.tokenRevision).toBe(3)
  })

  it('resolves interrupt with token and stable resolutionRequestId', async () => {
    postMock.mockResolvedValueOnce({
      interruptId: 'int-1',
      runId: 'run-1',
      conversationId: 'conv-1',
      status: 'approved',
      kind: 'approval',
      requestRevision: 1,
      runRevision: 2,
      tokenRevision: 3,
      fields: [],
      requestPayload: {},
      initialValues: {},
      nodeId: 'n1',
      nodeVisitId: 'nv1',
      resolvedAt: '2026-07-16T12:00:00Z',
      resolutionRequestId: 'rr-1',
    })

    const resolved = await resolveInterrupt('conv-1', 'run-1', 'int-1', {
      token: 'raw-token',
      resolutionRequestId: 'rr-1',
      expectedTokenRevision: 3,
      expectedRequestRevision: 1,
      expectedRunRevision: 2,
      outcome: 'approved',
      values: { summary: 'ok' },
    })
    expect(postMock).toHaveBeenCalledWith(
      '/api/assistant/conversations/conv-1/runs/run-1/interrupts/int-1/resolve',
      {
        body: {
          token: 'raw-token',
          resolutionRequestId: 'rr-1',
          expectedTokenRevision: 3,
          expectedRequestRevision: 1,
          expectedRunRevision: 2,
          outcome: 'approved',
          values: { summary: 'ok' },
        },
      },
    )
    expect(resolved.status).toBe('approved')
    expect(resolved.resolutionRequestId).toBe('rr-1')
  })
})
