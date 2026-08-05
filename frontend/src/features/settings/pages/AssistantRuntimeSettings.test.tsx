import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { AssistantRuntimeSettingsPage } from './AssistantRuntimeSettings'

const runtimeMocks = vi.hoisted(() => ({
  useAssistantReadinessDiagnosticsQuery: vi.fn(),
  useAssistantRolloutActivationReadinessQuery: vi.fn(),
  useAssistantRolloutsQuery: vi.fn(),
  useSetAssistantNewRunsEnabledMutation: vi.fn(),
}))

vi.mock('@/features/assistant-runtime', () => ({
  AssistantRuntimeActivationCard: ({
    preparedRolloutRevisionId,
    rolloutControlRevision,
    candidateReadiness,
  }: {
    preparedRolloutRevisionId: string | null
    rolloutControlRevision: number | null
    candidateReadiness?: { rolloutRevisionId: string; compatibleWorkerIds: string[] } | null
  }) => (
    <div data-testid="activation-card">
      prepared={preparedRolloutRevisionId ?? 'none'} control={rolloutControlRevision ?? 'none'}
      candidate={candidateReadiness?.rolloutRevisionId ?? 'none'} candidateWorkers=
      {candidateReadiness?.compatibleWorkerIds.join(',') ?? 'none'}
    </div>
  ),
  ...runtimeMocks,
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        'common.back': 'Back',
        'common.refresh': 'Refresh',
        'assistantRuntime.settings.title': 'Assistant runtime',
        'assistantRuntime.settings.description': 'Review prepared and active runtime control.',
        'assistantRuntime.settings.prepared.title': 'Prepared rollout',
        'assistantRuntime.settings.prepared.description': 'A prepared rollout can be activated.',
        'assistantRuntime.settings.prepared.badge': 'Prepared',
        'assistantRuntime.settings.prepared.empty': 'No prepared rollout is available.',
        'assistantRuntime.settings.active.title': 'Active rollout',
        'assistantRuntime.settings.active.description': 'The active rollout admits new chats.',
        'assistantRuntime.settings.active.badge': 'Active',
        'assistantRuntime.settings.active.inactiveBadge': 'Inactive',
        'assistantRuntime.settings.active.empty': 'No rollout is active.',
        'assistantRuntime.settings.rollout.build': 'Build {{build}}',
        'assistantRuntime.settings.newRuns.title': 'New chat runs',
        'assistantRuntime.settings.newRuns.description': 'Control durable admission for future chats.',
        'assistantRuntime.settings.newRuns.switchLabel': 'Accept new chat runs',
        'assistantRuntime.settings.newRuns.enabled': 'New chat runs are enabled.',
        'assistantRuntime.settings.newRuns.disabled': 'New chat runs are disabled.',
        'assistantRuntime.settings.newRuns.hint': 'Existing durable runs are unchanged.',
        'assistantRuntime.settings.newRuns.error': 'Could not change new chat setting.',
        'assistantRuntime.settings.newRuns.conflict': 'Control changed. The latest state was refreshed.',
        'assistantRuntime.settings.loadError': 'Could not load runtime control.',
      } as Record<string, string>)[key] ?? key,
  }),
}))

const preparedRevision = {
  rolloutRevisionId: 'prepared-rollout-id',
  revisionLabel: 'prepared-rollout',
  revisionDigest: 'a'.repeat(64),
  profileVersionId: 'profile-1',
  modelId: 'model-1',
  buildRevision: 'build-prepared',
  preparedReason: 'system_bootstrap',
  preparedByOperatorId: null,
  createdAt: '2026-07-30T00:00:00+00:00',
}

const activeRevision = {
  ...preparedRevision,
  rolloutRevisionId: 'active-rollout-id',
  revisionLabel: 'active-rollout',
  buildRevision: 'build-active',
}

function rollouts(overrides: Record<string, unknown> = {}) {
  return {
    control: {
      activeRolloutRevisionId: activeRevision.rolloutRevisionId,
      controlRevision: 7,
      newRunsEnabled: true,
    },
    revisions: [preparedRevision, activeRevision],
    ...overrides,
  }
}

describe('AssistantRuntimeSettingsPage', () => {
  const rolloutRefetch = vi.fn()
  const diagnosticsRefetch = vi.fn()
  const candidateReadinessRefetch = vi.fn()
  const setNewRuns = vi.fn()

  beforeEach(() => {
    rolloutRefetch.mockReset()
    rolloutRefetch.mockResolvedValue({ data: rollouts() })
    diagnosticsRefetch.mockReset()
    diagnosticsRefetch.mockResolvedValue({ data: null })
    candidateReadinessRefetch.mockReset()
    candidateReadinessRefetch.mockResolvedValue({ data: null })
    setNewRuns.mockReset()
    setNewRuns.mockResolvedValue({
      activeRolloutRevisionId: activeRevision.rolloutRevisionId,
      controlRevision: 8,
      newRunsEnabled: false,
    })

    runtimeMocks.useAssistantRolloutsQuery.mockReturnValue({
      data: rollouts(),
      isLoading: false,
      isError: false,
      refetch: rolloutRefetch,
    })
    runtimeMocks.useAssistantReadinessDiagnosticsQuery.mockReturnValue({
      data: {
        ready: false,
        reasonCodes: ['rollout_inactive'],
        activeRolloutRevisionId: activeRevision.rolloutRevisionId,
        profileVersionId: 'profile-1',
        modelId: 'model-1',
        compatibleWorkerIds: ['worker-1'],
        buildRevision: 'build-active',
      },
      refetch: diagnosticsRefetch,
    })
    runtimeMocks.useAssistantRolloutActivationReadinessQuery.mockReturnValue({
      data: {
        rolloutRevisionId: preparedRevision.rolloutRevisionId,
        ready: true,
        reasonCodes: [],
        profileVersionId: preparedRevision.profileVersionId,
        modelId: preparedRevision.modelId,
        compatibleWorkerIds: ['prepared-worker'],
        buildRevision: preparedRevision.buildRevision,
      },
      isLoading: false,
      isError: false,
      refetch: candidateReadinessRefetch,
    })
    runtimeMocks.useSetAssistantNewRunsEnabledMutation.mockReturnValue({
      mutateAsync: setNewRuns,
      isPending: false,
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('restores the latest prepared rollout from server state after leaving initialization', () => {
    render(<AssistantRuntimeSettingsPage />)

    expect(screen.getByTestId('activation-card')).toHaveTextContent(
      'prepared=prepared-rollout-id control=7',
    )
    expect(screen.getByText('prepared-rollout')).toBeVisible()
    expect(screen.getByText('active-rollout')).toBeVisible()
  })

  it('uses readiness for the prepared rollout rather than the active rollout', () => {
    render(<AssistantRuntimeSettingsPage />)

    expect(runtimeMocks.useAssistantRolloutActivationReadinessQuery).toHaveBeenCalledWith(
      preparedRevision.rolloutRevisionId,
    )
    expect(screen.getByTestId('activation-card')).toHaveTextContent(
      'candidate=prepared-rollout-id candidateWorkers=prepared-worker',
    )
  })

  it('uses the durable control revision when disabling future chat runs', async () => {
    render(<AssistantRuntimeSettingsPage />)

    fireEvent.click(screen.getByRole('switch', { name: 'Accept new chat runs' }))

    await waitFor(() => expect(setNewRuns).toHaveBeenCalledTimes(1))
    expect(setNewRuns).toHaveBeenCalledWith(
      expect.objectContaining({
        enabled: false,
        expectedControlRevision: 7,
        reason: 'disable new Main Agent runs',
      }),
    )
    const body = setNewRuns.mock.calls[0][0] as { requestId: string }
    expect(body.requestId).toMatch(/^[0-9a-f-]{16,}$/)
  })

  it('uses an RFC 4122 UUID request id when crypto.randomUUID is unavailable', async () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0)
        return bytes
      },
    })
    render(<AssistantRuntimeSettingsPage />)

    fireEvent.click(screen.getByRole('switch', { name: 'Accept new chat runs' }))

    await waitFor(() => expect(setNewRuns).toHaveBeenCalledTimes(1))
    const body = setNewRuns.mock.calls[0][0] as { requestId: string }
    expect(body.requestId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
  })

  it('keeps server state and shows an error when the durable switch fails', async () => {
    setNewRuns.mockRejectedValueOnce(new Error('network failure'))
    render(<AssistantRuntimeSettingsPage />)

    const toggle = screen.getByRole('switch', { name: 'Accept new chat runs' })
    fireEvent.click(toggle)

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Could not change new chat setting.')
    })
    expect(toggle).toBeChecked()
  })

  it('refreshes rollout and readiness state after a control-revision conflict', async () => {
    setNewRuns.mockRejectedValueOnce({ status: 409 })
    render(<AssistantRuntimeSettingsPage />)

    fireEvent.click(screen.getByRole('switch', { name: 'Accept new chat runs' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Control changed. The latest state was refreshed.',
      )
    })
    expect(rolloutRefetch).toHaveBeenCalledTimes(1)
    expect(diagnosticsRefetch).toHaveBeenCalledTimes(1)
  })
})
