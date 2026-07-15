import { describe, expect, it } from 'vitest'
import {
  bindEventDedupeRun,
  createEventDedupeState,
  identityFromPayload,
  isActiveRunStatus,
  isPreservedWaitingStatus,
  isTerminalRunStatus,
  shouldApplyEvent,
} from './eventIdentity'

describe('eventIdentity', () => {
  it('classifies active / waiting / terminal statuses', () => {
    expect(isActiveRunStatus('queued')).toBe(true)
    expect(isActiveRunStatus('running')).toBe(true)
    expect(isActiveRunStatus('recovering')).toBe(true)
    expect(isActiveRunStatus('waiting_approval')).toBe(true)
    expect(isActiveRunStatus('waiting_input')).toBe(true)
    expect(isActiveRunStatus('cancelling')).toBe(true)
    expect(isActiveRunStatus('needs_reconciliation')).toBe(true)
    expect(isActiveRunStatus('completed')).toBe(false)

    expect(isPreservedWaitingStatus('recovering')).toBe(true)
    expect(isPreservedWaitingStatus('waiting_input')).toBe(true)
    expect(isPreservedWaitingStatus('cancelling')).toBe(true)
    expect(isPreservedWaitingStatus('running')).toBe(false)

    expect(isTerminalRunStatus('completed')).toBe(true)
    expect(isTerminalRunStatus('failed')).toBe(true)
    expect(isTerminalRunStatus('cancelled')).toBe(true)
    expect(isTerminalRunStatus('cancelling')).toBe(false)
  })

  it('dedupes by sequence (older/equal rejected, newer accepted)', () => {
    const state = createEventDedupeState('run-1')
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 1, eventKey: 'e:1' }),
    ).toBe(true)
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 2, eventKey: 'e:2' }),
    ).toBe(true)
    // equal
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 2, eventKey: 'e:2-dup' }),
    ).toBe(false)
    // older
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 1, eventKey: 'e:1-old' }),
    ).toBe(false)
    // newer
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 3, eventKey: 'e:3' }),
    ).toBe(true)
    expect(state.lastAppliedSeq).toBe(3)
  })

  it('dedupes duplicate eventKey from uncertain cursor persistence', () => {
    const state = createEventDedupeState('run-1')
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 5, eventKey: 'content:1' }),
    ).toBe(true)
    // Same key, higher seq (at-least-once redelivery) — reject apply
    expect(
      shouldApplyEvent(state, { runId: 'run-1', seq: 6, eventKey: 'content:1' }),
    ).toBe(false)
    // lastAppliedSeq still advances so cursor does not stall
    expect(state.lastAppliedSeq).toBe(6)
  })

  it('rejects events for a different Run', () => {
    const state = createEventDedupeState('run-a')
    expect(
      shouldApplyEvent(state, { runId: 'run-b', seq: 1, eventKey: 'x' }),
    ).toBe(false)
  })

  it('bindEventDedupeRun resets identity when Run changes', () => {
    let state = createEventDedupeState('run-a')
    shouldApplyEvent(state, { runId: 'run-a', seq: 4, eventKey: 'k' })
    state = bindEventDedupeRun(state, 'run-b')
    expect(state.runId).toBe('run-b')
    expect(state.lastAppliedSeq).toBe(0)
    expect(state.appliedEventKeys.size).toBe(0)
  })

  it('identityFromPayload reads seq/eventKey/runId', () => {
    const id = identityFromPayload(
      { seq: 7, eventKey: 'run.stop:1', runId: 'r1' },
      'fallback',
    )
    expect(id).toEqual({ runId: 'r1', seq: 7, eventKey: 'run.stop:1' })
    const id2 = identityFromPayload({ seq: 2 }, 'fallback-run')
    expect(id2.runId).toBe('fallback-run')
    expect(id2.seq).toBe(2)
  })
})
