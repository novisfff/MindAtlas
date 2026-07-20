import { describe, expect, it } from 'vitest'

/**
 * Gate dialog contract: client payload may only include evidence refs + optional
 * non-safety waiver codes/reason — never passed/decision/metrics/assertions.
 */
function buildGateRequest(input: {
  requestId: string
  subject: Record<string, unknown>
  qualifyingEvalRunIds: string[]
  requestedNonSafetyWaiverCodes?: string[]
  waiverReason?: string | null
  // forbidden client fields intentionally accepted in type as any and stripped
  passed?: boolean
  decision?: string
  metrics?: Record<string, unknown>
  assertions?: unknown
}) {
  return {
    requestId: input.requestId,
    subject: input.subject,
    qualifyingEvalRunIds: input.qualifyingEvalRunIds,
    requestedNonSafetyWaiverCodes: input.requestedNonSafetyWaiverCodes ?? [],
    waiverReason: input.waiverReason ?? null,
  }
}

describe('SkillPublishGateDialog payload contract', () => {
  it('strips client-authored decision fields', () => {
    const body = buildGateRequest({
      requestId: 'g1',
      subject: { schemaVersion: 1 },
      qualifyingEvalRunIds: ['run-1'],
      passed: true,
      decision: 'passed',
      metrics: { x: 1 },
      assertions: [{ ok: true }],
    })
    expect(body).toEqual({
      requestId: 'g1',
      subject: { schemaVersion: 1 },
      qualifyingEvalRunIds: ['run-1'],
      requestedNonSafetyWaiverCodes: [],
      waiverReason: null,
    })
    expect('passed' in body).toBe(false)
    expect('decision' in body).toBe(false)
    expect('metrics' in body).toBe(false)
    expect('assertions' in body).toBe(false)
  })
})
