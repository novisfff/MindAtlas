import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api/client'
import type { LaunchCandidate, LaunchStatus, QualificationTargetSummary } from '../api/launch'
import { PreGaLaunchPage } from './PreGaLaunchPage'

const mocks = vi.hoisted(() => ({
  useOperatorSessionQuery: vi.fn(),
  usePreGaLaunchStatusQuery: vi.fn(),
  usePreGaLaunchQualificationTargetQuery: vi.fn(),
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
  usePreGaLaunchQualificationTargetQuery: mocks.usePreGaLaunchQualificationTargetQuery,
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
  schemaFamily: 'pre_ga_v1',
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

const target: QualificationTargetSummary = {
  schemaVersion: 1,
  buildRevision: 'build-safe',
  imageSetDigest: 'a'.repeat(64),
  deployedArtifactSetDigest: 'b'.repeat(64),
  schemaFamily: 'pre_ga_v1',
  schemaRevision: 'pre_ga_v1_0002',
  productionSchemaDeploymentClass: 'production',
  productionSchemaRuntimeIdentityDigest: 'c'.repeat(64),
  rolloutRevisionId: '00000000-0000-4000-8000-000000000021',
  profileVersionId: '00000000-0000-4000-8000-000000000022',
  modelId: '00000000-0000-4000-8000-000000000023',
  runtimeClosureDigest: 'd'.repeat(64),
  dependencyLockSetDigest: 'e'.repeat(64),
  scenarioSetDigest: 'f'.repeat(64),
  requiredAssertionSetDigest: '0'.repeat(64),
  runnerIdentityDigest: '1'.repeat(64),
  evidenceTrustSetDigest: '2'.repeat(64),
  qualificationTargetDigest: '3'.repeat(64),
}

function status(candidateOverride: LaunchCandidate | null = candidate): { data: LaunchStatus; isLoading: boolean; isError: boolean; error: null } {
  return {
    data: {
      launched: false,
      reasonCode: 'launch_control_missing',
      controlRevision: 4,
      activeSubjectDigest: null,
      activeCandidateId: null,
      activeGateUseId: null,
      launchedAt: null,
      updatedAt: '2026-08-14T00:00:00Z',
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
    mocks.usePreGaLaunchQualificationTargetQuery.mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      error: null,
    })
    mocks.usePreGaLaunchCandidatesQuery.mockReturnValue({
      data: { pages: [{ items: [candidate], nextCursor: null }] },
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
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

  it('renders the server-owned target identity summary without exposing target material', () => {
    mocks.usePreGaLaunchQualificationTargetQuery.mockReturnValue({
      data: target,
      isLoading: false,
      isError: false,
      error: null,
    })
    renderPage('viewer')

    const summary = screen.getByTestId('qualification-target-summary')
    expect(summary).toBeInTheDocument()
    expect(within(summary).getByText('build-safe')).toBeInTheDocument()
    expect(within(summary).getByText('pre_ga_v1_0002')).toBeInTheDocument()
    expect(within(summary).getByText('production')).toBeInTheDocument()
    expect(within(summary).getByText('3'.repeat(12) + '…')).toBeInTheDocument()
  })

  it('renders explicit server launch state and safe control identity fields', () => {
    const stale = status()
    stale.data = {
      ...stale.data,
      reasonCode: 'launch_subject_stale',
      activeSubjectDigest: 'a'.repeat(64),
      activeCandidateId: candidate.candidateId,
      activeGateUseId: '00000000-0000-4000-8000-000000000006',
      launchedAt: '2026-08-14T00:00:00Z',
      updatedAt: '2026-08-14T00:01:00Z',
    }
    mocks.usePreGaLaunchStatusQuery.mockReturnValue(stale)
    renderPage('viewer')

    expect(screen.getByText('preGaLaunch.states.stale')).toBeInTheDocument()
    expect(screen.getAllByText('00000000-000…')).toHaveLength(2)
    expect(screen.getByText('2026-08-14T00:00:00Z')).toBeInTheDocument()
  })

  it('loads the next server-paginated candidate history page on demand', () => {
    const fetchNextPage = vi.fn()
    mocks.usePreGaLaunchCandidatesQuery.mockReturnValue({
      data: { pages: [{ items: [candidate], nextCursor: { issuedAt: '2026-08-13T00:00:00Z', id: candidate.candidateId } }] },
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage,
      isLoading: false,
      isError: false,
      error: null,
    })
    renderPage('viewer')

    fireEvent.click(screen.getByRole('button', { name: 'preGaLaunch.history.loadMore' }))

    expect(fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('does not use the browser clock to suppress a server-authoritative consume attempt', () => {
    mocks.usePreGaLaunchStatusQuery.mockReturnValue(status({
      ...candidate,
      expiresAt: '2020-01-01T00:00:00Z',
    }))
    const consume = vi.fn().mockResolvedValue({
      controlRevision: 5,
      launchedAt: '2026-08-14T00:00:00Z',
      gateUseId: '00000000-0000-4000-8000-000000000005',
      candidate: null,
    })
    mocks.useConsumePreGaLaunchCandidateMutation.mockReturnValue({ mutateAsync: consume, isPending: false })
    renderPage('operator')

    expect(screen.getByRole('button', { name: 'preGaLaunch.consume.submit' })).toBeInTheDocument()
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

  it('retains the generated candidate request id through a network-ambiguous retry', async () => {
    const create = vi.fn()
      .mockRejectedValueOnce(new Error('network ambiguity'))
      .mockResolvedValueOnce(candidate)
    mocks.usePreGaLaunchCandidatesQuery.mockReturnValue({
      data: { pages: [{ items: [candidate], nextCursor: null }] },
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      isLoading: false,
      isError: false,
      error: null,
    })
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
    const submit = screen.getByRole('button', { name: 'preGaLaunch.create.submit' })
    fireEvent.click(submit)
    await screen.findByRole('alert')

    const firstRequestId = create.mock.calls[0][0].requestId
    expect(screen.getByTestId('create-request-id')).toHaveTextContent(firstRequestId)
    fireEvent.click(submit)
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2))
    expect(create.mock.calls[1][0].requestId).toBe(firstRequestId)
  })

  it('generates a new candidate request id when the submitted body changes', async () => {
    const create = vi.fn()
      .mockRejectedValueOnce(new Error('network ambiguity'))
      .mockRejectedValueOnce(new Error('changed body still rejected'))
    mocks.useCreatePreGaLaunchCandidateMutation.mockReturnValue({ mutateAsync: create, isPending: false })
    renderPage('operator')

    const fields = [
      ['preGaLaunch.create.automatedManifest', 'a'.repeat(64)],
      ['preGaLaunch.create.automatedAttestation', 'b'.repeat(64)],
      ['preGaLaunch.create.rehearsalManifest', 'c'.repeat(64)],
      ['preGaLaunch.create.rehearsalAttestation', 'd'.repeat(64)],
    ] as const
    for (const [label, value] of fields) fireEvent.change(screen.getByLabelText(label), { target: { value } })
    const reason = screen.getByLabelText('preGaLaunch.create.reason')
    fireEvent.change(reason, { target: { value: 'first body' } })
    const submit = screen.getByRole('button', { name: 'preGaLaunch.create.submit' })
    fireEvent.click(submit)
    await screen.findByRole('alert')
    const firstRequestId = create.mock.calls[0][0].requestId

    fireEvent.change(reason, { target: { value: 'changed body' } })
    fireEvent.click(submit)
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2))
    expect(create.mock.calls[1][0].requestId).not.toBe(firstRequestId)
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

    const confirmation = screen.getByTestId('consume-confirmation')
    expect(within(confirmation).getByText('00000000-000…')).toBeInTheDocument()
    expect(within(confirmation).getByText('4')).toBeInTheDocument()
    expect(within(confirmation).getByText(candidate.expiresAt as string)).toBeInTheDocument()
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
    const refetchStatus = vi.fn()
    const refetchCandidates = vi.fn()
    mocks.usePreGaLaunchStatusQuery.mockReturnValue({ ...status(), refetch: refetchStatus })
    mocks.usePreGaLaunchCandidatesQuery.mockReturnValue({
      data: { pages: [{ items: [candidate], nextCursor: null }] },
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: refetchCandidates,
      isLoading: false,
      isError: false,
      error: null,
    })
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
    expect(refetchStatus).toHaveBeenCalledTimes(1)
    expect(refetchCandidates).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).not.toContain('sentinel-password')
    expect(document.body.textContent).not.toContain('entry-body')
  })
})
