import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { AssistantRuntimeActivationCard } from './AssistantRuntimeActivationCard'
import type { AssistantReadinessDiagnostics } from '../api/runtime'
import * as runtimeApi from '../api/runtime'

const PREPARED_ID = 'prepared-revision-aaaa'
const activateSpy = vi.fn()

vi.mock('../api/runtime', async () => {
  const actual = await vi.importActual<typeof import('../api/runtime')>('../api/runtime')
  return {
    ...actual,
    activateAssistantRollout: (...args: unknown[]) => activateSpy(...args),
    getAssistantReadinessDiagnostics: vi.fn(),
    listAssistantRollouts: vi.fn(),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => {
      const map: Record<string, string> = {
        'assistantRuntime.activation.title': 'Activate Main Agent runtime',
        'assistantRuntime.activation.description':
          'Initialization prepared a rollout. Activate only after a compatible worker is online.',
        'assistantRuntime.activation.waitingWorker': 'Waiting for a compatible worker',
        'assistantRuntime.activation.activate': 'Activate',
        'assistantRuntime.activation.activating': 'Activating…',
        'assistantRuntime.activation.ready': 'Runtime activated',
        'assistantRuntime.activation.conflict':
          'Control revision changed. Refreshing diagnostics — review and try again.',
        'assistantRuntime.activation.error': 'Activation failed. Review readiness and retry.',
        'assistantRuntime.activation.pendingBootstrap': 'Assistant bootstrap: pending worker',
        'assistantRuntime.reasons.rollout_inactive':
          'No active Main Agent rollout is selected yet.',
        'assistantRuntime.reasons.worker_unavailable':
          'No compatible worker is online for this build, contract, and codec.',
        'assistantRuntime.reasons.system_seed_invalid':
          'System seed integrity failed. Stop and inspect deployment integrity; do not bypass.',
        'assistantRuntime.reasons.runtime_closure_drift':
          'Runtime closure drifted from the prepared revision. Stop and inspect deployment integrity; do not bypass.',
        'assistantRuntime.reasons.schema_incompatible':
          'Schema is incompatible with this Main Agent runtime. Stop and inspect deployment integrity; do not bypass.',
        'assistantRuntime.reasons.new_runs_disabled':
          'New chat runs are disabled by the operator switch.',
      }
      return map[key] ?? fallback ?? key
    },
  }),
}))

function diagnostics(
  overrides: Partial<AssistantReadinessDiagnostics> = {},
): AssistantReadinessDiagnostics {
  return {
    ready: false,
    reasonCodes: ['rollout_inactive'],
    activeRolloutRevisionId: null,
    profileVersionId: 'profile-v',
    modelId: 'model-1',
    compatibleWorkerIds: ['worker-1'],
    buildRevision: 'build-1',
    ...overrides,
  }
}

function compatiblePreparedRuntime() {
  return {
    preparedRolloutRevisionId: PREPARED_ID,
    rolloutControlRevision: 0,
    diagnostics: diagnostics({
      reasonCodes: ['rollout_inactive'],
      compatibleWorkerIds: ['worker-1'],
    }),
  }
}

function renderActivationCard(props: {
  preparedRolloutRevisionId?: string | null
  rolloutControlRevision?: number | null
  diagnostics?: AssistantReadinessDiagnostics | null
  onActivated?: () => void
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return render(
    <AssistantRuntimeActivationCard
      preparedRolloutRevisionId={props.preparedRolloutRevisionId ?? PREPARED_ID}
      rolloutControlRevision={props.rolloutControlRevision ?? 0}
      diagnostics={props.diagnostics ?? diagnostics()}
      onActivated={props.onActivated}
    />,
    { wrapper: Wrapper },
  )
}

describe('AssistantRuntimeActivationCard', () => {
  beforeEach(() => {
    activateSpy.mockReset()
    activateSpy.mockResolvedValue({
      activeRolloutRevisionId: PREPARED_ID,
      revisionLabel: 'prepared',
      revisionDigest: 'a'.repeat(64),
      controlRevision: 1,
      newRunsEnabled: true,
    })
    vi.mocked(runtimeApi.getAssistantReadinessDiagnostics).mockResolvedValue(
      diagnostics({
        ready: true,
        reasonCodes: [],
        activeRolloutRevisionId: PREPARED_ID,
        compatibleWorkerIds: ['worker-1'],
      }),
    )
    vi.mocked(runtimeApi.listAssistantRollouts).mockResolvedValue({
      control: {
        activeRolloutRevisionId: null,
        controlRevision: 0,
        newRunsEnabled: true,
      },
      revisions: [],
    })
  })

  it('waits for a compatible worker and does not auto-activate', async () => {
    renderActivationCard({
      diagnostics: diagnostics({
        ready: false,
        reasonCodes: ['rollout_inactive', 'worker_unavailable'],
        compatibleWorkerIds: [],
      }),
    })
    expect(screen.getByText(/waiting for a compatible worker/i)).toBeVisible()
    expect(screen.getByRole('button', { name: /activate/i })).toBeDisabled()
    expect(activateSpy).not.toHaveBeenCalled()
  })

  it('requires an explicit click and sends current control revision', async () => {
    renderActivationCard(compatiblePreparedRuntime())
    const button = screen.getByRole('button', { name: /activate/i })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    await waitFor(() => {
      expect(activateSpy).toHaveBeenCalledTimes(1)
    })
    expect(activateSpy).toHaveBeenCalledWith(
      PREPARED_ID,
      expect.objectContaining({
        expectedControlRevision: 0,
        reason: 'activate prepared Main Agent runtime',
      }),
    )
    const body = activateSpy.mock.calls[0][1] as { requestId: string }
    expect(typeof body.requestId).toBe('string')
    expect(body.requestId.length).toBeGreaterThan(8)
  })

  it('creates a fresh request id per click', async () => {
    renderActivationCard(compatiblePreparedRuntime())
    const button = screen.getByRole('button', { name: /activate/i })
    fireEvent.click(button)
    await waitFor(() => expect(activateSpy).toHaveBeenCalledTimes(1))
    const firstId = (activateSpy.mock.calls[0][1] as { requestId: string }).requestId

    // Re-render a still-inactive card and click again.
    activateSpy.mockClear()
    activateSpy.mockResolvedValue({
      activeRolloutRevisionId: PREPARED_ID,
      revisionLabel: 'prepared',
      revisionDigest: 'a'.repeat(64),
      controlRevision: 2,
      newRunsEnabled: true,
    })
    renderActivationCard(compatiblePreparedRuntime())
    fireEvent.click(screen.getAllByRole('button', { name: /activate/i }).at(-1)!)
    await waitFor(() => expect(activateSpy).toHaveBeenCalledTimes(1))
    const secondId = (activateSpy.mock.calls[0][1] as { requestId: string }).requestId
    expect(secondId).not.toEqual(firstId)
  })

  it('refreshes on 409 instead of overwriting', async () => {
    const onActivated = vi.fn()
    const { ApiError } = await import('@/lib/api/client')
    activateSpy.mockRejectedValueOnce(
      new ApiError({ message: 'control_conflict', status: 409, code: 40960 }),
    )

    renderActivationCard({
      ...compatiblePreparedRuntime(),
      onActivated,
    })
    fireEvent.click(screen.getByRole('button', { name: /activate/i }))
    await waitFor(() => {
      expect(screen.getByText(/control revision changed/i)).toBeVisible()
    })
    expect(onActivated).not.toHaveBeenCalled()
    expect(runtimeApi.listAssistantRollouts).toHaveBeenCalled()
    expect(runtimeApi.getAssistantReadinessDiagnostics).toHaveBeenCalled()
  })
})
