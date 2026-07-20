import { beforeEach, describe, expect, it } from 'vitest'
import { useSkillTestRunStore } from './skill-test-run-store'
import type { EvalRunSummary } from '../api/skill-evaluations'

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

  it('reconciles terminal status and cancel request', () => {
    useSkillTestRunStore.getState().beginRun(run)
    useSkillTestRunStore.getState().markCancelRequested()
    expect(useSkillTestRunStore.getState().status).toBe('cancelling')
    useSkillTestRunStore.getState().reconcileRun({ ...run, status: 'cancelled', lastEventSeq: 3 })
    expect(useSkillTestRunStore.getState().status).toBe('cancelled')
    expect(useSkillTestRunStore.getState().lastSequence).toBe(3)
  })
})
