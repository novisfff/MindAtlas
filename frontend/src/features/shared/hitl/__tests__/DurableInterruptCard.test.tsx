import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DurableInterruptCard,
  type DurableInterruptCardModel,
  type DurableInterruptSubmitPayload,
} from '../DurableInterruptCard'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallbackOrOpts?: string | Record<string, unknown>) => {
      if (typeof fallbackOrOpts === 'string') return fallbackOrOpts
      if (fallbackOrOpts && typeof fallbackOrOpts === 'object' && 'field' in fallbackOrOpts) {
        return `${key}:${String((fallbackOrOpts as { field: string }).field)}`
      }
      return key
    },
  }),
}))

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))

function makeInterrupt(overrides: Partial<DurableInterruptCardModel> = {}): DurableInterruptCardModel {
  return {
    interruptId: 'int-1',
    status: 'pending',
    kind: 'approval',
    fields: [
      {
        name: 'summary',
        label: 'Summary',
        type: 'string',
        widget: 'textarea',
        required: true,
      },
    ],
    requestPayload: {
      title: 'Review proposal',
      instruction: 'Please review',
      approveLabel: 'Approve',
      rejectLabel: 'Reject',
      requireRejectComment: false,
    },
    initialValues: { summary: 'draft text' },
    nodeId: 'node-1',
    expiresAt: '2099-01-01T00:00:00Z',
    resolvedAt: null,
    requestRevision: 1,
    runRevision: 2,
    tokenRevision: 0,
    ...overrides,
  }
}

describe('DurableInterruptCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders editable approval fields and approve/reject actions', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt()}
        onSubmit={onSubmit}
        createResolutionRequestId={() => 'rr-fixed-1'}
      />,
    )

    expect(screen.getByTestId('durable-interrupt-card')).toHaveAttribute('data-interrupt-kind', 'approval')
    expect(screen.getByDisplayValue('draft text')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1)
    })
    const payload = onSubmit.mock.calls[0][0] as DurableInterruptSubmitPayload
    expect(payload.outcome).toBe('approved')
    expect(payload.resolutionRequestId).toBe('rr-fixed-1')
    expect(payload.values.summary).toBe('draft text')
  })

  it('submits input kind with submitted outcome', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt({
          kind: 'input',
          requestPayload: { title: 'Need input', submitLabel: 'Send' },
        })}
        onSubmit={onSubmit}
        createResolutionRequestId={() => 'rr-input-1'}
      />,
    )

    expect(screen.getByTestId('durable-interrupt-card')).toHaveAttribute('data-interrupt-kind', 'input')
    fireEvent.click(screen.getByTestId('durable-interrupt-submit'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0].outcome).toBe('submitted')
    expect(onSubmit.mock.calls[0][0].resolutionRequestId).toBe('rr-input-1')
  })

  it('reuses the same resolutionRequestId for retries of the same click', async () => {
    let createCount = 0
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt()}
        onSubmit={onSubmit}
        createResolutionRequestId={() => {
          createCount += 1
          return `rr-${createCount}`
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    const firstId = onSubmit.mock.calls[0][0].resolutionRequestId
    expect(firstId).toBe('rr-1')

    // Second click while still pending (lost-response retry of same UI action path)
    // reuses the retained resolutionRequestId rather than minting a new one.
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2))
    expect(onSubmit.mock.calls[1][0].resolutionRequestId).toBe(firstId)
    expect(createCount).toBe(1)
  })

  it('disables actions for terminal expired status', () => {
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt({ status: 'expired', resolvedAt: '2026-07-16T00:00:00Z' })}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.getByTestId('durable-interrupt-card')).toHaveAttribute('data-interrupt-status', 'expired')
  })

  it('disables actions for cancelled status', () => {
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt({ status: 'cancelled', resolvedAt: '2026-07-16T00:00:00Z' })}
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('shows conflict message when provided', () => {
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt()}
        conflictMessage="interrupt_request_revision_mismatch"
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByTestId('durable-interrupt-conflict')).toHaveTextContent(
      'interrupt_request_revision_mismatch',
    )
  })

  it('rejects with reject outcome', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(
      <DurableInterruptCard
        interrupt={makeInterrupt({
          requestPayload: {
            title: 'Review',
            requireRejectComment: false,
            approveLabel: 'Approve',
            rejectLabel: 'Reject',
          },
        })}
        onSubmit={onSubmit}
        createResolutionRequestId={() => 'rr-reject'}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit.mock.calls[0][0].outcome).toBe('rejected')
  })
})
