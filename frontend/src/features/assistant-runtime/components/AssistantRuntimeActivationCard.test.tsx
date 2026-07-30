import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { AssistantRuntimeActivationCard } from './AssistantRuntimeActivationCard'
import type {
  AssistantReadinessDiagnostics,
  AssistantRolloutActivationReadiness,
} from '../api/runtime'
import * as runtimeApi from '../api/runtime'
import { assistantRuntimeKeys } from '../queries'

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

function renderActivationCard(
  props: {
    preparedRolloutRevisionId?: string | null
    rolloutControlRevision?: number | null
    diagnostics?: AssistantReadinessDiagnostics | null
    candidateReadiness?: AssistantRolloutActivationReadiness | null
    onActivated?: () => void
  },
  options?: { queryClient?: QueryClient },
) {
  const queryClient =
    options?.queryClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return {
    queryClient,
    ...render(
      <AssistantRuntimeActivationCard
        preparedRolloutRevisionId={props.preparedRolloutRevisionId ?? PREPARED_ID}
        rolloutControlRevision={props.rolloutControlRevision ?? 0}
        diagnostics={props.diagnostics ?? diagnostics()}
        candidateReadiness={props.candidateReadiness}
        onActivated={props.onActivated}
      />,
      { wrapper: Wrapper },
    ),
  }
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

  afterEach(() => {
    vi.unstubAllGlobals()
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

  it('uses candidate worker diagnostics instead of a different active rollout', () => {
    const candidateReadiness: AssistantRolloutActivationReadiness = {
      rolloutRevisionId: PREPARED_ID,
      ready: true,
      reasonCodes: [],
      profileVersionId: 'profile-v',
      modelId: 'model-1',
      compatibleWorkerIds: ['candidate-worker'],
      buildRevision: 'build-candidate',
    }

    renderActivationCard({
      diagnostics: diagnostics({
        ready: false,
        reasonCodes: ['worker_unavailable'],
        activeRolloutRevisionId: 'old-active-rollout',
        compatibleWorkerIds: [],
        buildRevision: 'build-old',
      }),
      candidateReadiness,
    })

    expect(screen.getByRole('button', { name: /activate/i })).toBeEnabled()
  })

  it('uses an RFC 4122 UUID request id when crypto.randomUUID is unavailable', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0)
        return bytes
      },
    })
    renderActivationCard(compatiblePreparedRuntime())

    fireEvent.click(screen.getByRole('button', { name: /activate/i }))

    await waitFor(() => expect(activateSpy).toHaveBeenCalledTimes(1))
    const body = activateSpy.mock.calls[0][1] as { requestId: string }
    expect(body.requestId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
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

  it('unfreezes diagnostics after 409 when parent props update with a compatible worker', async () => {
    const { ApiError } = await import('@/lib/api/client')
    activateSpy.mockRejectedValueOnce(
      new ApiError({ message: 'control_conflict', status: 409, code: 40960 }),
    )

    // 409 refresh returns empty workers — a local-preferred snapshot would freeze later prop updates.
    vi.mocked(runtimeApi.getAssistantReadinessDiagnostics).mockResolvedValue(
      diagnostics({
        ready: false,
        reasonCodes: ['rollout_inactive', 'worker_unavailable'],
        compatibleWorkerIds: [],
      }),
    )
    vi.mocked(runtimeApi.listAssistantRollouts).mockResolvedValue({
      control: {
        // Deliberately different from prepared id — must not overwrite prepared target.
        activeRolloutRevisionId: 'some-other-active',
        controlRevision: 3,
        newRunsEnabled: true,
      },
      revisions: [],
    })

    const noWorker = diagnostics({
      ready: false,
      reasonCodes: ['rollout_inactive', 'worker_unavailable'],
      compatibleWorkerIds: [],
    })
    const withWorker = diagnostics({
      ready: false,
      reasonCodes: ['rollout_inactive'],
      compatibleWorkerIds: ['worker-later'],
    })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { rerender } = render(
      <AssistantRuntimeActivationCard
        preparedRolloutRevisionId={PREPARED_ID}
        rolloutControlRevision={0}
        diagnostics={withWorker}
      />,
      { wrapper: Wrapper },
    )

    expect(screen.getByRole('button', { name: /activate/i })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: /activate/i }))

    await waitFor(() => {
      expect(screen.getByText(/control revision changed/i)).toBeVisible()
    })

    // Parent poll still has no worker. Local empty snapshot exists from 409 refresh.
    rerender(
      <AssistantRuntimeActivationCard
        preparedRolloutRevisionId={PREPARED_ID}
        rolloutControlRevision={0}
        diagnostics={noWorker}
      />,
    )
    expect(screen.getByText(/waiting for a compatible worker/i)).toBeVisible()
    expect(screen.getByRole('button', { name: /activate/i })).toBeDisabled()

    // Later poll brings a compatible worker — live props must win over frozen local snapshot.
    rerender(
      <AssistantRuntimeActivationCard
        preparedRolloutRevisionId={PREPARED_ID}
        rolloutControlRevision={0}
        diagnostics={withWorker}
      />,
    )

    expect(screen.queryByText(/waiting for a compatible worker/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /activate/i })).toBeEnabled()

    // Retry still targets the prepared revision, not the active id from the 409 control refresh.
    activateSpy.mockResolvedValueOnce({
      activeRolloutRevisionId: PREPARED_ID,
      revisionLabel: 'prepared',
      revisionDigest: 'a'.repeat(64),
      controlRevision: 4,
      newRunsEnabled: true,
    })
    fireEvent.click(screen.getByRole('button', { name: /activate/i }))
    await waitFor(() => expect(activateSpy).toHaveBeenCalledTimes(2))
    expect(activateSpy).toHaveBeenLastCalledWith(
      PREPARED_ID,
      expect.objectContaining({
        expectedControlRevision: 3,
      }),
    )
  })

  it('invalidates runtime query keys on successful activate', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    renderActivationCard(compatiblePreparedRuntime(), { queryClient })
    fireEvent.click(screen.getByRole('button', { name: /activate/i }))

    await waitFor(() => {
      expect(activateSpy).toHaveBeenCalledTimes(1)
    })

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalled()
    })

    const invalidatedKeys = invalidateSpy.mock.calls.map(
      (call) => (call[0] as { queryKey?: unknown })?.queryKey,
    )
    expect(invalidatedKeys).toEqual(
      expect.arrayContaining([
        assistantRuntimeKeys.publicReadiness(),
        assistantRuntimeKeys.diagnostics(),
        assistantRuntimeKeys.rollouts(),
      ]),
    )
  })
})
