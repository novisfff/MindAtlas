import { describe, expect, it } from 'vitest'
import { createStore } from 'zustand'
import { createChatLogic, type ChatState } from './chat-store'

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
