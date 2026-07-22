import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SkillPublishGateDialog } from './SkillPublishGateDialog'
import * as skillEvaluations from '../api/skill-evaluations'

vi.mock('../api/skill-evaluations', async () => {
  const actual = await vi.importActual<typeof import('../api/skill-evaluations')>(
    '../api/skill-evaluations',
  )
  return {
    ...actual,
    createPublishGate: vi.fn(),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.gateDialogTitle': 'Request publish gate',
        'settings.universalSkills.gateDialogHint':
          'Submit evidence references only. The server derives pass/fail/waiver.',
        'settings.universalSkills.gateNeedsSubject': 'Gate subject is required.',
        'settings.universalSkills.gateNeedsRuns':
          'At least one qualifying evaluation run is required.',
        'settings.universalSkills.gateWaiverReasonRequired':
          'Waiver reason is required when non-safety waiver codes are provided.',
        'settings.universalSkills.gateEvidenceRuns': 'Qualifying evaluation runs',
        'settings.universalSkills.gateWaiverCodes': 'Requested non-safety waiver codes',
        'settings.universalSkills.gateWaiverReason': 'Waiver reason',
        'settings.universalSkills.gateNoClientDecision':
          'Do not send passed, decision, assertions, metrics, or safety overrides from the client.',
        'settings.universalSkills.gateDecision': 'Server decision',
        'settings.universalSkills.gateHardSafetyOrFail':
          'Gate failed. Hard safety failures cannot be waived.',
        'settings.universalSkills.gateNonSafetyWaiver':
          'Non-safety waiver accepted by server.',
        'settings.universalSkills.gateAuthoritativeClosure': 'Authoritative closure (server)',
        'settings.universalSkills.requestGate': 'Request gate',
        'common.cancel': 'Cancel',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

/**
 * Gate dialog contract: client payload may only include evidence refs + optional
 * non-safety waiver codes/reason — never passed/decision/metrics/assertions/closure.
 */
function buildGateRequest(input: {
  requestId: string
  action: string
  subjectAggregateId: string
  subjectVersionId: string
  qualifyingEvalRunIds: string[]
  requestedNonSafetyWaiverCodes?: string[]
  waiverReason?: string | null
  // forbidden client fields intentionally accepted in type as any and stripped
  subject?: Record<string, unknown>
  passed?: boolean
  decision?: string
  metrics?: Record<string, unknown>
  assertions?: unknown
  contentDigest?: string
  profileDigest?: string
}) {
  return {
    requestId: input.requestId,
    action: input.action,
    subjectAggregateId: input.subjectAggregateId,
    subjectVersionId: input.subjectVersionId,
    qualifyingEvalRunIds: input.qualifyingEvalRunIds,
    requestedNonSafetyWaiverCodes: input.requestedNonSafetyWaiverCodes ?? [],
    waiverReason: input.waiverReason ?? null,
  }
}

describe('SkillPublishGateDialog payload contract', () => {
  it('strips client-authored decision and closure fields', () => {
    const body = buildGateRequest({
      requestId: 'g1',
      action: 'skill_publish',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      qualifyingEvalRunIds: ['run-1'],
      subject: { schemaVersion: 1, contentDigest: 'abc' },
      passed: true,
      decision: 'passed',
      metrics: { x: 1 },
      assertions: [{ ok: true }],
      contentDigest: 'abc',
      profileDigest: 'def',
    })
    expect(body).toEqual({
      requestId: 'g1',
      action: 'skill_publish',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      qualifyingEvalRunIds: ['run-1'],
      requestedNonSafetyWaiverCodes: [],
      waiverReason: null,
    })
    expect('passed' in body).toBe(false)
    expect('decision' in body).toBe(false)
    expect('metrics' in body).toBe(false)
    expect('assertions' in body).toBe(false)
    expect('subject' in body).toBe(false)
    expect('contentDigest' in body).toBe(false)
    expect('profileDigest' in body).toBe(false)
  })
})

describe('SkillPublishGateDialog component', () => {
  beforeEach(() => {
    vi.mocked(skillEvaluations.createPublishGate).mockReset()
    vi.mocked(skillEvaluations.createPublishGate).mockResolvedValue({
      gate: {
        id: 'gate-1',
        decision: 'passed',
        subjectKind: 'skill_draft',
        subjectAggregateId: 'pkg-1',
        subjectVersionId: 'ver-1',
        waiverCodes: [],
        requestId: 'req-1',
        action: 'skill_publish',
      },
      decision: 'passed',
      acceptedWaiverCodes: [],
      assertionSnapshot: {
        subjectKind: 'skill_draft',
        contentDigest: 'a'.repeat(64),
        resolvedBindingDigest: 'b'.repeat(64),
      },
      metricSnapshot: { caseCount: 2 },
    })
  })

  it('submits identity + evidence only and shows server closure read-only', async () => {
    const onCreated = vi.fn()
    render(
      <SkillPublishGateDialog
        open
        onClose={() => undefined}
        action="skill_publish"
        subjectAggregateId="pkg-1"
        subjectVersionId="ver-1"
        subjectKind="skill_draft"
        qualifyingEvalRunIds={['run-1']}
        onCreated={onCreated}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Request gate' }))

    await waitFor(() => {
      expect(skillEvaluations.createPublishGate).toHaveBeenCalledTimes(1)
    })

    const body = vi.mocked(skillEvaluations.createPublishGate).mock.calls[0][0]
    expect(body).toEqual({
      requestId: expect.any(String),
      action: 'skill_publish',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      qualifyingEvalRunIds: ['run-1'],
      requestedNonSafetyWaiverCodes: [],
      waiverReason: null,
    })
    expect(body).not.toHaveProperty('subject')
    expect(body).not.toHaveProperty('passed')
    expect(body).not.toHaveProperty('decision')

    expect(await screen.findByText(/Server decision/i)).toBeInTheDocument()
    expect(screen.getByText(/Authoritative closure \(server\)/i)).toBeInTheDocument()
    expect(screen.getByText(/contentDigest/i)).toBeInTheDocument()
    expect(onCreated).toHaveBeenCalled()
  })

  it('refuses to submit without subject identity or qualifying runs', () => {
    render(
      <SkillPublishGateDialog
        open
        onClose={() => undefined}
        action="skill_catalog_enable"
        subjectAggregateId={null}
        subjectVersionId={null}
        subjectKind="skill_version"
        qualifyingEvalRunIds={[]}
      />,
    )
    expect(screen.getByRole('button', { name: 'Request gate' })).toBeDisabled()
    expect(skillEvaluations.createPublishGate).not.toHaveBeenCalled()
  })
})
