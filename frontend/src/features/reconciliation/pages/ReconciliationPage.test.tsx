import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api/client'
import type { ReconciliationCall } from '../api/reconciliation'
import { ReconciliationPage } from './ReconciliationPage'

const mocks = vi.hoisted(() => ({
  useOperatorSessionQuery: vi.fn(),
  useReconciliationQuery: vi.fn(),
  useReconcileCapabilityCallMutation: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('@/features/operator-auth', () => ({
  useOperatorSessionQuery: mocks.useOperatorSessionQuery,
}))

vi.mock('../queries', () => ({
  useReconciliationQuery: mocks.useReconciliationQuery,
  useReconcileCapabilityCallMutation: mocks.useReconcileCapabilityCallMutation,
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

const call: ReconciliationCall = {
  callId: '00000000-0000-4000-8000-000000000011',
  runId: '00000000-0000-4000-8000-000000000012',
  status: 'needs_reconciliation',
  stateRevision: 3,
  runRevision: 7,
  failureCode: 'local_commit_outcome_unknown',
  executionMode: 'local_create_entry',
  sideEffectStartedAt: '2026-08-14T00:00:00Z',
  createdAt: '2026-08-14T00:00:00Z',
  updatedAt: '2026-08-14T00:01:00Z',
  attemptCount: 1,
  evidenceRequired: true,
}

function renderPage(role: 'operator' | 'viewer' = 'viewer') {
  mocks.useOperatorSessionQuery.mockReturnValue({ data: { authenticated: true, role } })
  return render(<ReconciliationPage />)
}

describe('ReconciliationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useReconciliationQuery.mockReturnValue({
      data: { items: [call], total: 1 },
      isLoading: false,
      isError: false,
      error: null,
    })
    mocks.useReconcileCapabilityCallMutation.mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        callId: call.callId,
        decision: 'mark_failed',
        resultingCallStatus: 'failed',
        resultingCallRevision: 4,
        resultingRunRevision: 8,
        reconciliationId: '00000000-0000-4000-8000-000000000013',
        created: true,
      }),
      isPending: false,
    })
  })

  it('renders a safe queue for viewers without an enabled mutation form', () => {
    renderPage('viewer')

    expect(screen.getByText('reconciliation.localCreateEntry')).toBeInTheDocument()
    expect(screen.getByText('reconciliation.operatorOnly')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'reconciliation.form.submit' })).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('sentinel-password')
  })

  it('sends only bounded decision, evidence, revision, request, and reason fields', async () => {
    const reconcile = vi.fn().mockResolvedValue({
      callId: call.callId,
      decision: 'mark_failed',
      resultingCallStatus: 'failed',
      resultingCallRevision: 4,
      resultingRunRevision: 8,
      reconciliationId: '00000000-0000-4000-8000-000000000013',
      created: true,
    })
    mocks.useReconcileCapabilityCallMutation.mockReturnValue({ mutateAsync: reconcile, isPending: false })
    renderPage('operator')

    fireEvent.change(screen.getByLabelText('reconciliation.form.artifacts'), {
      target: { value: '00000000-0000-4000-8000-000000000021' },
    })
    fireEvent.change(screen.getByLabelText('reconciliation.form.reason'), {
      target: { value: 'verified local outcome' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'reconciliation.form.submit' }))

    await waitFor(() => expect(reconcile).toHaveBeenCalledTimes(1))
    const input = reconcile.mock.calls[0][0]
    expect(Object.keys(input).sort()).toEqual(['callId', 'input'])
    expect(Object.keys(input.input).sort()).toEqual([
      'decision',
      'evidenceArtifactIds',
      'expectedCallRevision',
      'expectedRunRevision',
      'reason',
      'requestId',
    ])
    expect(input.input).toMatchObject({
      expectedCallRevision: 3,
      expectedRunRevision: 7,
      decision: 'mark_failed',
      evidenceArtifactIds: ['00000000-0000-4000-8000-000000000021'],
      reason: 'verified local outcome',
    })
    expect(input.callId).toBe(call.callId)
  })

  it('maps a viewer/CSRF denial to fixed copy without exposing server detail', async () => {
    const reconcile = vi.fn().mockRejectedValue(
      new ApiError({ status: 403, message: 'sentinel-token prompt body' }),
    )
    mocks.useReconcileCapabilityCallMutation.mockReturnValue({ mutateAsync: reconcile, isPending: false })
    renderPage('operator')

    fireEvent.change(screen.getByLabelText('reconciliation.form.artifacts'), {
      target: { value: '00000000-0000-4000-8000-000000000021' },
    })
    fireEvent.change(screen.getByLabelText('reconciliation.form.reason'), {
      target: { value: 'verified local outcome' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'reconciliation.form.submit' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('reconciliation.errors.forbidden')
    expect(document.body.textContent).not.toContain('sentinel-token')
    expect(document.body.textContent).not.toContain('prompt body')
  })
})
