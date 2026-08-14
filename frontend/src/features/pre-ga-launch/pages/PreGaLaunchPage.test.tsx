import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api/client'
import type { LaunchCandidate } from '../api/launch'
import { PreGaLaunchPage } from './PreGaLaunchPage'

const mocks = vi.hoisted(() => ({
  useOperatorSessionQuery: vi.fn(),
  usePreGaLaunchStatusQuery: vi.fn(),
  usePreGaLaunchCandidatesQuery: vi.fn(),
  useCreatePreGaLaunchCandidateMutation: vi.fn(),
  useConsumePreGaLaunchCandidateMutation: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('@/features/operator-auth', () => ({
  useOperatorSessionQuery: mocks.useOperatorSessionQuery,
}))

vi.mock('../queries', () => ({
  usePreGaLaunchStatusQuery: mocks.usePreGaLaunchStatusQuery,
  usePreGaLaunchCandidatesQuery: mocks.usePreGaLaunchCandidatesQuery,
  useCreatePreGaLaunchCandidateMutation: mocks.useCreatePreGaLaunchCandidateMutation,
  useConsumePreGaLaunchCandidateMutation: mocks.useConsumePreGaLaunchCandidateMutation,
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

const candidate: LaunchCandidate = {
  candidateId: '00000000-0000-4000-8000-000000000001',
  passed: true,
  failureCodes: [],
  qualificationTargetDigest: '1'.repeat(64),
  subjectDigest: '2'.repeat(64),
  buildRevision: 'build-safe',
  imageSetDigest: '3'.repeat(64),
  deployedArtifactSetDigest: '4'.repeat(64),
  schemaFamily: 'pre_ga',
  schemaRevision: 'pre_ga_v1_0002',
  schemaRuntimeIdentityDigest: '5'.repeat(64),
  rolloutRevisionId: '00000000-0000-4000-8000-000000000002',
  profileVersionId: '00000000-0000-4000-8000-000000000003',
  modelId: '00000000-0000-4000-8000-000000000004',
  runtimeClosureDigest: '6'.repeat(64),
  automatedEvidenceManifestDigest: '7'.repeat(64),
  rehearsalEvidenceManifestDigest: '8'.repeat(64),
  operationalSnapshotDigest: '9'.repeat(64),
  unknownCallCount: 0,
  needsReconciliationCount: 0,
  activeRunCount: 0,
  issuedAt: '2026-08-14T00:00:00Z',
  expiresAt: '2099-08-14T00:00:00Z',
  usedAt: null,
  resultingControlRevision: null,
  active: false,
}

function status(candidateOverride: LaunchCandidate | null = candidate) {
  return {
    data: {
      launched: false,
      reasonCode: 'launch_control_missing',
      controlRevision: 4,
      activeSubjectDigest: null,
      candidate: candidateOverride,
    },
    isLoading: false,
    isError: false,
    error: null,
  }
}

function renderPage(role: 'operator' | 'viewer' = 'viewer') {
  mocks.useOperatorSessionQuery.mockReturnValue({
    data: { authenticated: true, role },
    isLoading: false,
  })
  return render(<PreGaLaunchPage />)
}

describe('PreGaLaunchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.usePreGaLaunchStatusQuery.mockReturnValue(status())
    mocks.usePreGaLaunchCandidatesQuery.mockReturnValue({
      data: { items: [candidate], nextCursor: null },
      isLoading: false,
      isError: false,
      error: null,
    })
    mocks.useCreatePreGaLaunchCandidateMutation.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(candidate),
      isPending: false,
    })
    mocks.useConsumePreGaLaunchCandidateMutation.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        controlRevision: 5,
        launchedAt: '2026-08-14T00:00:00Z',
        gateUseId: '00000000-0000-4000-8000-000000000005',
        candidate: { ...candidate, usedAt: '2026-08-14T00:00:00Z', active: true, resultingControlRevision: 5 },
      }),
      isPending: false,
    })
  })

  it('keeps launch history readable but disables mutations for viewers', () => {
    renderPage('viewer')

    expect(screen.getByTestId('launch-candidate')).toBeInTheDocument()
    expect(screen.getByText('preGaLaunch.candidateStates.passing_unused')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'preGaLaunch.operatorOnly' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'preGaLaunch.consume.submit' })).not.toBeInTheDocument()
  })

  it('sends only the four evidence-reference candidate fields for an operator', async () => {
    const create = vi.fn().mockResolvedValue(candidate)
    mocks.useCreatePreGaLaunchCandidateMutation.mockReturnValue({ mutateAsync: create, isPending: false })
    renderPage('operator')

    const fields = [
      ['preGaLaunch.create.automatedManifest', 'a'.repeat(64)],
      ['preGaLaunch.create.automatedAttestation', 'b'.repeat(64)],
      ['preGaLaunch.create.rehearsalManifest', 'c'.repeat(64)],
      ['preGaLaunch.create.rehearsalAttestation', 'd'.repeat(64)],
    ] as const
    for (const [label, value] of fields) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } })
    }
    fireEvent.change(screen.getByLabelText('preGaLaunch.create.reason'), { target: { value: 'reviewed evidence' } })
    fireEvent.click(screen.getByRole('button', { name: 'preGaLaunch.create.submit' }))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    const input = create.mock.calls[0][0]
    expect(Object.keys(input).sort()).toEqual(['automatedEvidenceRef', 'reason', 'rehearsalEvidenceRef', 'requestId'])
    expect(input.automatedEvidenceRef).toEqual({
      schemaVersion: 1,
      evidenceKind: 'automated_qualification',
      manifestDigest: 'a'.repeat(64),
      attestationDigest: 'b'.repeat(64),
    })
    expect(input.rehearsalEvidenceRef).toEqual({
      schemaVersion: 1,
      evidenceKind: 'production_rehearsal',
      manifestDigest: 'c'.repeat(64),
      attestationDigest: 'd'.repeat(64),
    })
    expect(screen.getByTestId('launch-candidate')).toBeInTheDocument()
  })

  it('consumes only the current control revision and bounded reason', async () => {
    const consume = vi.fn().mockResolvedValue({
      controlRevision: 5,
      launchedAt: '2026-08-14T00:00:00Z',
      gateUseId: '00000000-0000-4000-8000-000000000005',
      candidate: { ...candidate, usedAt: '2026-08-14T00:00:00Z', active: true, resultingControlRevision: 5 },
    })
    mocks.useConsumePreGaLaunchCandidateMutation.mockReturnValue({ mutateAsync: consume, isPending: false })
    renderPage('operator')

    fireEvent.click(screen.getByRole('button', { name: 'preGaLaunch.consume.submit' }))
    await waitFor(() => expect(consume).toHaveBeenCalledTimes(1))
    expect(consume.mock.calls[0][0]).toMatchObject({
      candidateId: candidate.candidateId,
      input: {
        expectedControlRevision: 4,
        reason: 'preGaLaunch.defaultConsumeReason',
      },
    })
    expect(Object.keys(consume.mock.calls[0][0].input).sort()).toEqual([
      'expectedControlRevision',
      'reason',
      'requestId',
    ])
  })

  it('maps a CAS conflict to fixed copy and never renders rejected server content', async () => {
    const create = vi.fn().mockRejectedValue(
      new ApiError({ status: 409, message: 'sentinel-password prompt entry-body' }),
    )
    mocks.useCreatePreGaLaunchCandidateMutation.mockReturnValue({ mutateAsync: create, isPending: false })
    renderPage('operator')

    const fields = [
      ['preGaLaunch.create.automatedManifest', 'a'.repeat(64)],
      ['preGaLaunch.create.automatedAttestation', 'b'.repeat(64)],
      ['preGaLaunch.create.rehearsalManifest', 'c'.repeat(64)],
      ['preGaLaunch.create.rehearsalAttestation', 'd'.repeat(64)],
    ] as const
    for (const [label, value] of fields) fireEvent.change(screen.getByLabelText(label), { target: { value } })
    fireEvent.change(screen.getByLabelText('preGaLaunch.create.reason'), { target: { value: 'reviewed evidence' } })
    fireEvent.click(screen.getByRole('button', { name: 'preGaLaunch.create.submit' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('preGaLaunch.errors.conflict')
    expect(document.body.textContent).not.toContain('sentinel-password')
    expect(document.body.textContent).not.toContain('entry-body')
  })
})
