import { describe, expect, it } from 'vitest'
import { createStore } from 'zustand'
import { createChatLogic, type ChatState } from './chat-store'
import type { DurableInterrupt, HumanApproval } from '../types'

function makeLegacyApproval(overrides: Partial<HumanApproval> = {}): HumanApproval {
  return {
    id: 'appr-1',
    runId: 'run-legacy',
    channelType: 'web',
    conversationId: 'conv-1',
    messageId: 'msg-1',
    workflowId: null,
    skillId: null,
    nodeId: 'n1',
    nodeLabel: 'Approve',
    status: 'pending',
    requestPayload: { title: 'Legacy' },
    fieldSchema: [],
    initialValues: {},
    submittedValues: {},
    decision: null,
    comment: null,
    resolvedAt: null,
    createdAt: '2026-07-16T00:00:00Z',
    updatedAt: '2026-07-16T00:00:00Z',
    source: 'legacy',
    ...overrides,
  }
}

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
    runRevision: 2,
    tokenRevision: 0,
    expiresAt: '2026-07-17T00:00:00Z',
    allowedActions: ['approve', 'reject', 'token', 'resolve'],
    fields: [],
    requestPayload: { title: 'Durable' },
    initialValues: {},
    nodeId: 'n1',
    nodeVisitId: 'nv1',
    resolvedAt: null,
    ...overrides,
  }
}

describe('chat-store event cursor', () => {
  it('setLastEventSeq is monotonic (at-least-once replay)', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().setLastEventSeq(5)
    expect(store.getState().lastEventSeq).toBe(5)
    store.getState().setLastEventSeq(3)
    expect(store.getState().lastEventSeq).toBe(5)
    store.getState().setLastEventSeq(8)
    expect(store.getState().lastEventSeq).toBe(8)
  })

  it('preserves activeRunStatus for waiting/recovering until cleared', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().setActiveRun('run-1', 'recovering', 2)
    expect(store.getState().activeRunStatus).toBe('recovering')
    store.getState().setActiveRunStatus('waiting_input')
    expect(store.getState().activeRunStatus).toBe('waiting_input')
    store.getState().setActiveRunStatus('cancelling')
    expect(store.getState().activeRunStatus).toBe('cancelling')
    store.getState().clearActiveRun()
    expect(store.getState().activeRunId).toBeNull()
    expect(store.getState().activeRunStatus).toBeNull()
  })

  it('setActiveRun does not rewind lastEventSeq for the same Run', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().setActiveRun('run-1', 'running', 10)
    expect(store.getState().lastEventSeq).toBe(10)
    // Re-attach with an older cursor must not rewind.
    store.getState().setActiveRun('run-1', 'recovering', 4)
    expect(store.getState().lastEventSeq).toBe(10)
    expect(store.getState().activeRunStatus).toBe('recovering')
    // Forward progress is allowed.
    store.getState().setActiveRun('run-1', 'running', 12)
    expect(store.getState().lastEventSeq).toBe(12)
    // Switching to a different Run may reset the cursor.
    store.getState().setActiveRun('run-2', 'running', 1)
    expect(store.getState().activeRunId).toBe('run-2')
    expect(store.getState().lastEventSeq).toBe(1)
  })
})

describe('chat-store durable interrupts', () => {
  it('upserts durable interrupt onto the matching assistant message', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().addMessage({
      id: 'msg-1',
      role: 'assistant',
      content: 'hello',
      createdAt: Date.now(),
    })
    store.getState().upsertDurableInterrupt(makeInterrupt())
    const msg = store.getState().messages[0]
    expect(msg.durableInterrupts).toHaveLength(1)
    expect(msg.durableInterrupts?.[0].interruptId).toBe('int-1')
  })

  it('deduplicates durable interrupt by interruptId on replay', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().addMessage({
      id: 'msg-1',
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })
    store.getState().upsertDurableInterrupt(makeInterrupt({ status: 'pending', tokenRevision: 0 }))
    store.getState().upsertDurableInterrupt(makeInterrupt({ status: 'pending', tokenRevision: 1 }))
    store.getState().upsertDurableInterrupt(makeInterrupt({ status: 'approved', tokenRevision: 1, resolutionRequestId: 'rr-1' }))
    const cards = store.getState().messages[0].durableInterrupts ?? []
    expect(cards).toHaveLength(1)
    expect(cards[0].status).toBe('approved')
    expect(cards[0].resolutionRequestId).toBe('rr-1')
  })

  it('setRunPendingInterrupts replaces pending cards without dropping terminal history', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().addMessage({
      id: 'msg-1',
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })
    store.getState().upsertDurableInterrupt(
      makeInterrupt({ interruptId: 'old-terminal', status: 'approved', resolutionRequestId: 'rr-old' }),
    )
    store.getState().upsertDurableInterrupt(makeInterrupt({ interruptId: 'stale-pending', status: 'pending' }))
    store.getState().setRunPendingInterrupts([
      makeInterrupt({ interruptId: 'fresh-pending', status: 'pending', messageId: 'msg-1' }),
    ])
    const cards = store.getState().messages[0].durableInterrupts ?? []
    const ids = cards.map((c) => c.interruptId).sort()
    expect(ids).toEqual(['fresh-pending', 'old-terminal'])
    expect(cards.find((c) => c.interruptId === 'old-terminal')?.status).toBe('approved')
  })

  it('preserves Legacy humanApprovals independently of durable interrupts', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().addMessage({
      id: 'msg-1',
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })
    store.getState().upsertHumanApproval(makeLegacyApproval())
    store.getState().upsertDurableInterrupt(makeInterrupt())
    const msg = store.getState().messages[0]
    expect(msg.humanApprovals).toHaveLength(1)
    expect(msg.humanApprovals?.[0].id).toBe('appr-1')
    expect(msg.durableInterrupts).toHaveLength(1)
    expect(msg.durableInterrupts?.[0].source).toBe('durable')
  })

  it('falls back to last assistant message when interrupt messageId is missing', () => {
    const store = createStore<ChatState>(createChatLogic)
    store.getState().addMessage({
      id: 'user-1',
      role: 'user',
      content: 'hi',
      createdAt: Date.now(),
    })
    store.getState().addMessage({
      id: 'msg-assist',
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    })
    store.getState().upsertDurableInterrupt(makeInterrupt({ messageId: null }))
    expect(store.getState().messages[1].durableInterrupts?.[0].interruptId).toBe('int-1')
  })
})
