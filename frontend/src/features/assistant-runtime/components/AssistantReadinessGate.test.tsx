import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { AssistantReadinessGate } from './AssistantReadinessGate'
import { usePublicAssistantReadinessQuery } from '../queries'

vi.mock('../queries', () => ({
  usePublicAssistantReadinessQuery: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => {
      const map: Record<string, string> = {
        'assistantRuntime.readiness.loadingTitle': 'Checking assistant readiness',
        'assistantRuntime.readiness.loadingDescription':
          'Confirming the active Main Agent runtime can admit new chats.',
        'assistantRuntime.readiness.unavailableTitle': 'Assistant is not ready',
        'assistantRuntime.readiness.unavailableDescription':
          'New chats are blocked until readiness clears. Existing conversation history may still be visible.',
        'assistantRuntime.reasons.worker_unavailable':
          'No compatible worker is online for this build, contract, and codec.',
        'assistantRuntime.reasons.rollout_inactive':
          'No active Main Agent rollout is selected yet.',
        'assistantRuntime.reasons.system_seed_invalid':
          'System seed integrity failed. Stop and inspect deployment integrity; do not bypass.',
      }
      return map[key] ?? fallback ?? key
    },
  }),
}))

const mockedQuery = vi.mocked(usePublicAssistantReadinessQuery)

function renderGate(children: ReactNode = <div>composer-ready</div>) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AssistantReadinessGate>{children}</AssistantReadinessGate>
    </QueryClientProvider>,
  )
}

describe('AssistantReadinessGate', () => {
  it('shows a skeleton while readiness is loading', () => {
    mockedQuery.mockReturnValue({
      isLoading: true,
      data: undefined,
      isError: false,
    } as never)

    renderGate()
    expect(screen.getByText(/checking assistant readiness/i)).toBeInTheDocument()
    expect(screen.queryByText('composer-ready')).not.toBeInTheDocument()
  })

  it('blocks the composer when not ready and lists reason copy', () => {
    mockedQuery.mockReturnValue({
      isLoading: false,
      data: {
        ready: false,
        reasonCodes: ['worker_unavailable', 'rollout_inactive'],
      },
      isError: false,
    } as never)

    renderGate()
    expect(screen.getByText(/assistant is not ready/i)).toBeInTheDocument()
    expect(
      screen.getByText(/no compatible worker is online/i),
    ).toBeInTheDocument()
    expect(screen.queryByText('composer-ready')).not.toBeInTheDocument()
  })

  it('renders children when ready', () => {
    mockedQuery.mockReturnValue({
      isLoading: false,
      data: { ready: true, reasonCodes: [] },
      isError: false,
    } as never)

    renderGate()
    expect(screen.getByText('composer-ready')).toBeInTheDocument()
  })

  it('never offers a bypass for integrity failures', () => {
    mockedQuery.mockReturnValue({
      isLoading: false,
      data: {
        ready: false,
        reasonCodes: ['system_seed_invalid'],
      },
      isError: false,
    } as never)

    renderGate()
    expect(screen.getByText(/stop and inspect deployment integrity/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /bypass|continue anyway/i })).toBeNull()
  })
})
