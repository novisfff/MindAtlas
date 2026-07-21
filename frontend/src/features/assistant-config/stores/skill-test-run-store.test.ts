import { beforeEach, describe, expect, it } from 'vitest'
import { createSkillTestRunStore, useSkillTestRunStore } from './skill-test-run-store'
import type { EvalEventSummary, EvalRunSummary } from '../api/skill-evaluations'

const run: EvalRunSummary = {
  id: 'run-1',
  subjectKind: 'skill_draft',
  subjectAggregateId: 'pkg-1',
  subjectVersionId: 'ver-1',
  mode: 'interactive_scripted',
  status: 'running',
  stateRevision: 1,
  lastEventSeq: 0,
}

function event(sequence: number, eventType = 'case_result'): EvalEventSummary {
  return {
    sequence,
    eventType,
    payload: { sequence, ok: true },
  }
}

describe('skill-test-run-store', () => {
  beforeEach(() => {
    useSkillTestRunStore.getState().reset()
  })

  it('begins a run and ingests deduplicated events', () => {
    useSkillTestRunStore.getState().beginRun(run)
    useSkillTestRunStore.getState().ingestEvents('run-1', [
      { sequence: 1, eventType: 'started', payload: { ok: true } },
      { sequence: 1, eventType: 'started', payload: { ok: true } },
      { sequence: 2, eventType: 'metrics', payload: { score: 1, apiKey: 'secret' } },
    ])
    const state = useSkillTestRunStore.getState()
    expect(state.events).toHaveLength(2)
    expect(state.lastSequence).toBe(2)
    expect(state.events[1].payload.apiKey).toBeUndefined()
    expect(state.metrics.score).toBe(1)
  })

  it('deduplicates SSE replay by run and sequence', () => {
    const store = createSkillTestRunStore()
    store.getState().ingestEvents('run-1', [event(2), event(2), event(3)])
    expect(store.getState().events.map((item) => item.sequence)).toEqual([2, 3])
  })

  it('ignores heartbeat payloads as trace events', () => {
    const store = createSkillTestRunStore()
    store.getState().beginRun(run)
    store.getState().ingestHeartbeat('run-1', { afterSequence: 2, status: 'running' })
    expect(store.getState().events).toHaveLength(0)
    expect(store.getState().lastHeartbeat?.afterSequence).toBe(2)
  })

  it('reconciles terminal status and cancel request', () => {
    useSkillTestRunStore.getState().beginRun(run)
    useSkillTestRunStore.getState().markCancelRequested()
    expect(useSkillTestRunStore.getState().status).toBe('cancelling')
    useSkillTestRunStore.getState().reconcileRun({ ...run, status: 'cancelled', lastEventSeq: 3 })
    expect(useSkillTestRunStore.getState().status).toBe('cancelled')
    expect(useSkillTestRunStore.getState().lastSequence).toBe(3)
  })
})
