import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SkillTestWorkbench } from './SkillTestWorkbench'
import * as skillEvaluations from '../api/skill-evaluations'
import * as mainAgentProfiles from '../api/main-agent-profiles'
import { useSkillTestRunStore } from '../stores/skill-test-run-store'

vi.mock('../api/skill-evaluations', async () => {
  const actual = await vi.importActual<typeof import('../api/skill-evaluations')>(
    '../api/skill-evaluations',
  )
  return {
    ...actual,
    listEvalDatasets: vi.fn(),
    listDatasetVersions: vi.fn(),
    createEvalRun: vi.fn(),
    cancelEvalRun: vi.fn(),
    getEvalRun: vi.fn(),
    listEvalRunEvents: vi.fn(),
    streamEvalRunEvents: vi.fn(),
    listEvalRunCaseResults: vi.fn(),
    listEvalRunEvidence: vi.fn(),
  }
})

vi.mock('../api/main-agent-profiles', async () => {
  const actual = await vi.importActual<typeof import('../api/main-agent-profiles')>(
    '../api/main-agent-profiles',
  )
  return {
    ...actual,
    getDefaultMainAgentProfile: vi.fn(),
    listDefaultMainAgentVersions: vi.fn(),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.evalMode': 'Evaluation mode',
        'settings.universalSkills.datasetVersion': 'Dataset version',
        'settings.universalSkills.startEval': 'Start evaluation',
        'settings.universalSkills.cancelEval': 'Cancel',
        'settings.universalSkills.evalPrompt': 'Prompt',
        'settings.universalSkills.evalLocale': 'Locale',
        'settings.universalSkills.profileVersion': 'Profile version',
        'settings.universalSkills.providerFixture': 'Provider fixture',
        'settings.universalSkills.providerFixtureStructuralDefault':
          'Structural default (no fixture pin)',
        'settings.universalSkills.liveModelId': 'Live model',
        'settings.universalSkills.selectDatasetVersion': 'Select published dataset version…',
        'settings.universalSkills.loadingProfiles': 'Loading profiles…',
        'settings.universalSkills.workbenchNeedsDraft':
          'A draft version is required to start evaluation.',
        'settings.universalSkills.workbenchTransportFallback':
          'Event stream interrupted; falling back to polling.',
        'settings.universalSkills.workbenchSseReconnecting':
          'Event stream interrupted; reconnecting…',
        'settings.universalSkills.workbenchPollTimeout':
          'Polling timed out while waiting for a terminal run status.',
        'settings.universalSkills.noEvalEvents': 'No evaluation events yet.',
        'settings.universalSkills.evalTrace': 'Evaluation trace',
        'settings.universalSkills.evidenceTitle': 'Evaluation evidence',
        'settings.universalSkills.aggregateMetrics': 'Aggregate metrics',
        'settings.universalSkills.actualSkills': 'Actual active skills',
        'settings.universalSkills.capabilityTraces': 'Capability traces',
        'settings.universalSkills.completionObligations': 'Completion and obligations',
        'settings.universalSkills.assertionFailures': 'Assertion failures',
        'settings.universalSkills.missingSafety': 'Missing safety evidence',
        'settings.universalSkills.promotionEligible': 'Promotion eligible',
        'settings.universalSkills.retentionExpiry': 'Retention / expiry',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

const publishedDataset = {
  id: 'ds-1',
  stableKey: 'plan04-read-only',
  displayName: 'Plan 04 read-only',
  publishedVersionId: 'ds-ver-1',
  versionId: 'ds-ver-1',
}

function renderWorkbench(options?: {
  datasets?: Array<typeof publishedDataset>
  versionId?: string | null
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const datasets = options?.datasets ?? []
  vi.mocked(skillEvaluations.listEvalDatasets).mockResolvedValue({
    items: datasets.map(({ versionId: _v, ...rest }) => rest),
    total: datasets.length,
  })
  vi.mocked(skillEvaluations.listDatasetVersions).mockImplementation(async (datasetId) => ({
    items: datasets
      .filter((d) => d.id === datasetId && d.publishedVersionId)
      .map((d) => ({
        id: d.publishedVersionId!,
        datasetId: d.id,
        sequence: 1,
        versionName: 'v1',
        schemaVersion: 1,
        contentDigest: 'a'.repeat(64),
        caseCount: 3,
      })),
    total: 1,
  }))
  vi.mocked(mainAgentProfiles.getDefaultMainAgentProfile).mockResolvedValue({
    id: 'profile-1',
    profileKey: 'default',
    displayName: 'Default',
    isDefault: true,
    migrationState: 'native',
    runtimeEnabled: true,
    draftVersion: {
      id: 'profile-ver-1',
      profileId: 'profile-1',
      sequenceNo: 1,
      versionName: 'draft',
      versionSource: 'save',
      origin: 'ui',
      contentDigest: 'b'.repeat(64),
    },
    publishedVersion: {
      id: 'profile-ver-1',
      profileId: 'profile-1',
      sequenceNo: 1,
      versionName: 'v1',
      versionSource: 'publish',
      origin: 'ui',
      contentDigest: 'b'.repeat(64),
    },
  })
  vi.mocked(mainAgentProfiles.listDefaultMainAgentVersions).mockResolvedValue({
    items: [
      {
        id: 'profile-ver-1',
        profileId: 'profile-1',
        sequenceNo: 1,
        versionName: 'v1',
        versionSource: 'publish',
        origin: 'ui',
        contentDigest: 'b'.repeat(64),
      },
    ],
    total: 1,
    limit: 50,
    offset: 0,
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <SkillTestWorkbench
        packageId="pkg-1"
        versionId={options?.versionId === undefined ? 'ver-1' : options.versionId}
      />
    </QueryClientProvider>,
  )
}

describe('SkillTestWorkbench', () => {
  beforeEach(() => {
    useSkillTestRunStore.getState().reset()
    vi.clearAllMocks()
  })

  it('requires a published dataset for dataset_scripted', async () => {
    renderWorkbench({ datasets: [publishedDataset] })

    // Wait for profile versions to load so profileVersionId is populated.
    await screen.findByLabelText('Profile version')
    await vi.waitFor(() => {
      expect((screen.getByLabelText('Profile version') as HTMLSelectElement).value).toBe(
        'profile-ver-1',
      )
    })

    fireEvent.change(await screen.findByLabelText('Evaluation mode'), {
      target: { value: 'dataset_scripted' },
    })
    expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()

    const datasetSelect = await screen.findByLabelText('Dataset version')
    await vi.waitFor(() => {
      const options = Array.from((datasetSelect as HTMLSelectElement).options).map((o) => o.value)
      expect(options).toContain(publishedDataset.versionId)
    })
    fireEvent.change(datasetSelect, {
      target: { value: publishedDataset.versionId },
    })
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeEnabled()
    })
  })

  it('does not send client digests when starting an evaluation', async () => {
    vi.mocked(skillEvaluations.createEvalRun).mockResolvedValue({
      id: 'run-1',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'queued',
      stateRevision: 0,
      lastEventSeq: 0,
    })
    vi.mocked(skillEvaluations.streamEvalRunEvents).mockResolvedValue('closed')
    vi.mocked(skillEvaluations.getEvalRun).mockResolvedValue({
      id: 'run-1',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'completed',
      stateRevision: 2,
      lastEventSeq: 1,
      gateEligible: false,
    })
    vi.mocked(skillEvaluations.listEvalRunCaseResults).mockResolvedValue({ items: [], total: 0 })
    vi.mocked(skillEvaluations.listEvalRunEvidence).mockResolvedValue({
      runId: 'run-1',
      gateEligible: false,
      evidenceProvenance: 'structural_synthetic',
      artifacts: [],
      capabilityCalls: [],
    })

    renderWorkbench()
    fireEvent.change(await screen.findByLabelText('Prompt'), {
      target: { value: 'evaluate this skill' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start evaluation' }))

    await vi.waitFor(() => {
      expect(skillEvaluations.createEvalRun).toHaveBeenCalled()
    })
    const body = vi.mocked(skillEvaluations.createEvalRun).mock.calls[0][0]
    expect(body).not.toHaveProperty('subjectContentDigest')
    expect(body).not.toHaveProperty('subjectBindingDigest')
    expect(body.prompt).toBe('evaluate this skill')
    expect(body.profileVersionId).toBe('profile-ver-1')
    // interactive default keeps fixture optional (structural_synthetic path).
    expect(body.providerFixtureRevision).toBeNull()
  })

  it('allows optional provider fixture pin for interactive_scripted', async () => {
    vi.mocked(skillEvaluations.createEvalRun).mockResolvedValue({
      id: 'run-2',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'queued',
      stateRevision: 0,
      lastEventSeq: 0,
    })
    vi.mocked(skillEvaluations.streamEvalRunEvents).mockResolvedValue('closed')
    vi.mocked(skillEvaluations.getEvalRun).mockResolvedValue({
      id: 'run-2',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'completed',
      stateRevision: 1,
      lastEventSeq: 0,
    })
    vi.mocked(skillEvaluations.listEvalRunCaseResults).mockResolvedValue({ items: [], total: 0 })
    vi.mocked(skillEvaluations.listEvalRunEvidence).mockResolvedValue({
      runId: 'run-2',
      gateEligible: true,
      evidenceProvenance: 'real_orchestration',
      artifacts: [],
      capabilityCalls: [],
    })

    renderWorkbench()
    await screen.findByLabelText('Provider fixture')
    fireEvent.change(screen.getByLabelText('Provider fixture'), {
      target: { value: 'provider-direct-answer@eval-v1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start evaluation' }))

    await vi.waitFor(() => {
      expect(skillEvaluations.createEvalRun).toHaveBeenCalled()
    })
    const body = vi.mocked(skillEvaluations.createEvalRun).mock.calls[0][0]
    expect(body.providerFixtureRevision).toBe('provider-direct-answer@eval-v1')
  })

  it('refreshes stateRevision before cancel CAS', async () => {
    useSkillTestRunStore.getState().beginRun({
      id: 'run-cancel',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'running',
      stateRevision: 1,
      lastEventSeq: 0,
    })

    vi.mocked(skillEvaluations.getEvalRun).mockResolvedValue({
      id: 'run-cancel',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'running',
      stateRevision: 4,
      lastEventSeq: 2,
    })
    vi.mocked(skillEvaluations.cancelEvalRun).mockResolvedValue({
      id: 'run-cancel',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'cancelling',
      stateRevision: 5,
      lastEventSeq: 2,
    })

    renderWorkbench()
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await vi.waitFor(() => {
      expect(skillEvaluations.getEvalRun).toHaveBeenCalledWith('run-cancel')
      expect(skillEvaluations.cancelEvalRun).toHaveBeenCalledWith(
        'run-cancel',
        expect.objectContaining({ expectedStateRevision: 4 }),
      )
    })
  })

  it('keeps Start locked and falls back to polling on SSE transport failure', async () => {
    vi.mocked(skillEvaluations.createEvalRun).mockResolvedValue({
      id: 'run-poll',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'running',
      stateRevision: 1,
      lastEventSeq: 0,
    })
    // Exhaust reconnects then fall through to polling.
    vi.mocked(skillEvaluations.streamEvalRunEvents).mockImplementation(
      async (_runId, options) => {
        options.onError?.(new Error('network down'))
        return 'transport_failure'
      },
    )
    vi.mocked(skillEvaluations.listEvalRunEvents).mockResolvedValue({
      items: [],
      afterSequence: 0,
      nextSequence: 0,
    })
    vi.mocked(skillEvaluations.getEvalRun).mockResolvedValue({
      id: 'run-poll',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'running',
      stateRevision: 1,
      lastEventSeq: 0,
    })

    renderWorkbench()
    await screen.findByLabelText('Profile version')
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start evaluation' }))

    await vi.waitFor(() => {
      expect(skillEvaluations.createEvalRun).toHaveBeenCalled()
      expect(skillEvaluations.streamEvalRunEvents).toHaveBeenCalled()
    })
    await vi.waitFor(() => {
      expect(useSkillTestRunStore.getState().transportMode).toBe('polling')
      expect(useSkillTestRunStore.getState().status).toBe('running')
    })
    expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  })

  it('populates aggregate metrics from terminal case results', async () => {
    vi.mocked(skillEvaluations.createEvalRun).mockResolvedValue({
      id: 'run-metrics',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'queued',
      stateRevision: 0,
      lastEventSeq: 0,
    })
    vi.mocked(skillEvaluations.streamEvalRunEvents).mockResolvedValue('closed')
    vi.mocked(skillEvaluations.getEvalRun).mockResolvedValue({
      id: 'run-metrics',
      subjectKind: 'skill_draft',
      subjectAggregateId: 'pkg-1',
      subjectVersionId: 'ver-1',
      mode: 'interactive_scripted',
      status: 'completed',
      stateRevision: 2,
      lastEventSeq: 1,
      gateEligible: false,
    })
    vi.mocked(skillEvaluations.listEvalRunCaseResults).mockResolvedValue({
      items: [
        {
          id: 'cr-1',
          evalRunId: 'run-metrics',
          evalCaseId: 'case-1',
          resultState: 'passed',
          assertionDetails: {},
          actualActiveSkills: ['skill.a'],
          stopReason: 'completed',
          outputArtifactIds: [],
          evidenceArtifactIds: [],
          rounds: 2,
          calls: 3,
          tokens: 11,
          latencyMs: 40,
          resultDigest: 'd'.repeat(64),
        },
      ],
      total: 1,
    })
    vi.mocked(skillEvaluations.listEvalRunEvidence).mockResolvedValue({
      runId: 'run-metrics',
      gateEligible: false,
      evidenceProvenance: 'structural_synthetic',
      artifacts: [],
      capabilityCalls: [],
    })

    renderWorkbench()
    await screen.findByLabelText('Profile version')
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeEnabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start evaluation' }))

    await vi.waitFor(() => {
      expect(skillEvaluations.createEvalRun).toHaveBeenCalled()
    })
    await vi.waitFor(() => {
      const metrics = useSkillTestRunStore.getState().metrics
      expect(metrics.caseCount).toBe(1)
      expect(metrics.passedCount).toBe(1)
      expect(metrics.tokens).toBe(11)
      expect(metrics.calls).toBe(3)
    })
  })
})
