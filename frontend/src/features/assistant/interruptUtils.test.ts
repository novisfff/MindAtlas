import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api/client'
import {
  createResolutionRequestId,
  extractInterruptReasonCode,
  isDurableInterruptPending,
  isDurableInterruptTerminal,
  mapDurableFieldsToSchema,
  normalizeDurableInterrupt,
  recoverFromLostResolveResponse,
} from './interruptUtils'
import type { DurableInterrupt } from './types'

function makeInterrupt(overrides: Partial<DurableInterrupt> = {}): DurableInterrupt {
  return {
    source: 'durable',
    interruptId: 'int-1',
    runId: 'run-1',
    conversationId: 'conv-1',
    messageId: 'msg-1',
    status: 'pending',
    kind: 'approval',
    requestRevision: 1,
    runRevision: 3,
    tokenRevision: 0,
    expiresAt: '2026-07-16T12:00:00Z',
    allowedActions: ['approve', 'reject', 'token', 'resolve'],
    fields: [],
    requestPayload: { title: 'Review proposal' },
    initialValues: {},
    nodeId: 'n1',
    nodeVisitId: 'nv1',
    resolvedAt: null,
    ...overrides,
  }
}

describe('mapDurableFieldsToSchema', () => {
  it('maps backend widget-type fields onto shared HITL data type + widget', () => {
    const schema = mapDurableFieldsToSchema([
      { name: 'summary', type: 'textarea', label: 'Summary', required: true },
      { name: 'ok', type: 'switch', label: 'OK' },
      { name: 'tags', type: 'checkbox_group', options: ['a', 'b'], required: false },
      { name: 'choice', type: 'select', options: ['x', 'y'] },
    ])
    expect(schema).toEqual([
      { name: 'summary', label: 'Summary', type: 'string', widget: 'textarea', options: undefined, allowCustom: undefined, placeholder: undefined, required: true },
      { name: 'ok', label: 'OK', type: 'boolean', widget: 'switch', options: undefined, allowCustom: undefined, placeholder: undefined, required: undefined },
      { name: 'tags', label: undefined, type: 'array', widget: 'checkbox_group', options: ['a', 'b'], allowCustom: undefined, placeholder: undefined, required: false },
      { name: 'choice', label: undefined, type: 'string', widget: 'select', options: ['x', 'y'], allowCustom: undefined, placeholder: undefined, required: undefined },
    ])
  })

  it('passes through already-shared field schema shapes', () => {
    const schema = mapDurableFieldsToSchema([
      { name: 'n', type: 'integer', widget: 'input', required: true },
    ] as any)
    expect(schema[0]).toMatchObject({ name: 'n', type: 'integer', widget: 'input', required: true })
  })
})

describe('normalizeDurableInterrupt', () => {
  it('normalizes safe public payload without inventing digests/values/comments', () => {
    const interrupt = normalizeDurableInterrupt({
      interruptId: 'int-9',
      runId: 'run-9',
      conversationId: 'conv-9',
      messageId: 'msg-9',
      status: 'pending',
      kind: 'input',
      requestRevision: 2,
      runRevision: 5,
      tokenRevision: 1,
      expiresAt: '2026-07-16T13:00:00Z',
      allowedActions: ['submit', 'token', 'resolve'],
      fields: [{ name: 'answer', type: 'input', label: 'Answer', required: true }],
      requestPayload: { title: 'Need input' },
      initialValues: { answer: 'draft' },
      nodeId: 'node-a',
      nodeVisitId: 'visit-a',
      resolvedAt: null,
      // must be ignored if present by mistake
      resolutionDigest: 'secret',
      tokenDigest: 'secret',
      submittedValues: { answer: 'secret' },
      comment: 'secret',
    })
    expect(interrupt.source).toBe('durable')
    expect(interrupt.kind).toBe('input')
    expect(interrupt.fields[0]).toMatchObject({ name: 'answer', type: 'string', widget: 'input' })
    expect(interrupt.resolutionRequestId).toBeUndefined()
    expect((interrupt as any).resolutionDigest).toBeUndefined()
    expect((interrupt as any).submittedValues).toBeUndefined()
    expect((interrupt as any).comment).toBeUndefined()
  })

  it('exposes terminal resolutionRequestId when present', () => {
    const interrupt = normalizeDurableInterrupt({
      interruptId: 'int-1',
      runId: 'run-1',
      conversationId: 'conv-1',
      status: 'approved',
      kind: 'approval',
      requestRevision: 1,
      runRevision: 1,
      tokenRevision: 2,
      fields: [],
      requestPayload: {},
      initialValues: {},
      nodeId: 'n',
      nodeVisitId: 'v',
      resolvedAt: '2026-07-16T14:00:00Z',
      resolutionRequestId: 'rr-winner',
    })
    expect(interrupt.resolutionRequestId).toBe('rr-winner')
    expect(isDurableInterruptTerminal(interrupt.status)).toBe(true)
  })
})

describe('lost response recovery', () => {
  it('treats matching resolutionRequestId as this click winning', () => {
    const result = recoverFromLostResolveResponse({
      retainedResolutionRequestId: 'rr-1',
      current: makeInterrupt({ status: 'approved', resolutionRequestId: 'rr-1' }),
    })
    expect(result.kind).toBe('won')
  })

  it('treats different winning request ID as another action having won', () => {
    const result = recoverFromLostResolveResponse({
      retainedResolutionRequestId: 'rr-mine',
      current: makeInterrupt({ status: 'approved', resolutionRequestId: 'rr-other' }),
    })
    expect(result.kind).toBe('lost_to_other')
  })

  it('allows retry when still pending', () => {
    const result = recoverFromLostResolveResponse({
      retainedResolutionRequestId: 'rr-1',
      current: makeInterrupt({ status: 'pending' }),
    })
    expect(result.kind).toBe('still_pending')
    expect(isDurableInterruptPending(result.kind === 'still_pending' ? result.interrupt.status : '')).toBe(true)
  })
})

describe('createResolutionRequestId', () => {
  it('returns a non-empty stable-format id', () => {
    const id = createResolutionRequestId()
    expect(typeof id).toBe('string')
    expect(id.length).toBeGreaterThan(8)
  })
})

describe('extractInterruptReasonCode', () => {
  it('reads reasonCode from ApiError details', () => {
    const err = new ApiError({
      message: 'conflict',
      status: 409,
      details: { reasonCode: 'interrupt_request_revision_mismatch' },
    })
    expect(extractInterruptReasonCode(err)).toBe('interrupt_request_revision_mismatch')
  })

  it('returns null for non-api errors', () => {
    expect(extractInterruptReasonCode(new Error('x'))).toBeNull()
  })
})
