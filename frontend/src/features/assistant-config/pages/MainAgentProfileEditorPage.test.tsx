import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MainAgentProfileEditorPage } from './MainAgentProfileEditorPage'
import * as profilesApi from '../api/main-agent-profiles'
import * as skillEvaluations from '../api/skill-evaluations'

const PROFILE_ID = 'profile-1111-2222-3333'
const DRAFT_ID = 'draft-aaaa-bbbb-cccc'
const PUBLISHED_ID = 'pub-dddd-eeee-ffff'
const DRAFT_RUN_ID = 'run-draft-0001'
const PROMO_RUN_ID = 'run-promo-0002'
const PUBLISH_GATE_ID = 'gate-publish-0001'
const PROMOTION_GATE_ID = 'gate-promo-0002'

const gateRequests: Array<Record<string, unknown>> = []

vi.mock('../api/main-agent-profiles', async () => {
  const actual = await vi.importActual<typeof import('../api/main-agent-profiles')>(
    '../api/main-agent-profiles',
  )
  return {
    ...actual,
    getProtectedDefaultMainAgentProfile: vi.fn(),
    listProtectedDefaultMainAgentVersions: vi.fn(),
    getProtectedDefaultMainAgentVersion: vi.fn(),
    saveProtectedDefaultMainAgentDraft: vi.fn(),
    publishProtectedDefaultMainAgent: vi.fn(),
    enableProtectedDefaultMainAgentRuntime: vi.fn(),
    disableProtectedDefaultMainAgentRuntime: vi.fn(),
    // Workbench uses the unprotected default profile helpers for profile pin lists.
    getDefaultMainAgentProfile: vi.fn(async () => ({
      id: 'profile-1111-2222-3333',
      profileKey: 'default',
      displayName: 'Default',
      draftVersion: { id: 'draft-aaaa-bbbb-cccc' },
      publishedVersion: null,
    })),
    listDefaultMainAgentVersions: vi.fn(async () => ({
      items: [
        {
          id: 'draft-aaaa-bbbb-cccc',
          profileId: 'profile-1111-2222-3333',
          sequenceNo: 1,
          versionName: 'draft',
          versionSource: 'save',
          origin: 'ui',
          contentDigest: 'a'.repeat(64),
        },
      ],
      total: 1,
    })),
  }
})

vi.mock('../api/skill-evaluations', async () => {
  const actual = await vi.importActual<typeof import('../api/skill-evaluations')>(
    '../api/skill-evaluations',
  )
  return {
    ...actual,
    createPublishGate: vi.fn(),
    listQualifyingEvidence: vi.fn(),
    createEvalRun: vi.fn(),
    listEvalDatasets: vi.fn(async () => ({ items: [], total: 0 })),
    listDatasetVersions: vi.fn(async () => ({ items: [], total: 0 })),
    listEvalRunEvents: vi.fn(async () => ({ items: [], lastSequence: 0 })),
    listEvalRunCaseResults: vi.fn(async () => ({ items: [], total: 0 })),
    listEvalRunEvidence: vi.fn(async () => null),
    streamEvalRunEvents: vi.fn(() => ({ close: () => undefined })),
    getEvalRun: vi.fn(),
    cancelEvalRun: vi.fn(),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.profileTitle': 'Main Agent Profile',
        'settings.universalSkills.profileNoSingleTarget':
          'Profile must not embed single-target Skill/Workflow/Agent fields',
        'settings.universalSkills.basePrompt': 'Base prompt',
        'settings.universalSkills.controlCapabilities': 'Control capability keys',
        'settings.universalSkills.catalogScopeMode': 'Catalog scope mode',
        'settings.universalSkills.publishProfile': 'Publish profile',
        'settings.universalSkills.profilePublished': 'Profile publish requested',
        'settings.universalSkills.saveDraft': 'Save draft',
        'settings.universalSkills.enableRuntime': 'Enable runtime',
        'settings.universalSkills.disableRuntime': 'Disable runtime',
        'settings.universalSkills.profileRuntimeEnabled': 'Profile runtime enabled',
        'settings.universalSkills.profileRuntimeDisabled': 'Profile runtime disabled',
        'settings.universalSkills.profileNeedsPublished':
          'Publish a profile version before enabling runtime.',
        'settings.universalSkills.profileNeedsPromotionGate':
          'A promotion gate is required to enable runtime.',
        'settings.universalSkills.profileDisableConfirm':
          'Disable Main Agent Profile runtime?',
        'settings.universalSkills.promotionGateId': 'Promotion gate ID',
        'settings.universalSkills.versionHistory': 'Version history',
        'settings.universalSkills.evaluationWorkbench': 'Evaluation workbench',
        'settings.universalSkills.noVersions': 'No versions yet.',
        'settings.universalSkills.noDraftVersion': 'No draft version selected.',
        'settings.universalSkills.dirty': 'Unsaved changes',
        'settings.universalSkills.gateNeedsRuns':
          'At least one qualifying evaluation run is required.',
        'settings.universalSkills.gateNeedsSubject': 'Gate subject is required.',
        'settings.universalSkills.gateWaiverReasonRequired':
          'Waiver reason is required when non-safety waiver codes are provided.',
        'settings.universalSkills.gateDialogTitle': 'Request publish gate',
        'settings.universalSkills.gateDialogHint':
          'Submit evidence references only. The server derives pass/fail/waiver.',
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
        'settings.universalSkills.openGateDialog': 'Open publish gate dialog',
        'settings.universalSkills.openPromotionGateDialog': 'Open promotion gate dialog',
        'settings.universalSkills.evaluatePublishedBeforeEnable':
          'Evaluate the published version before enabling',
        'settings.universalSkills.evalTargetLabel': 'Evaluation target',
        'settings.universalSkills.evalTargetDraft': 'Draft',
        'settings.universalSkills.evalTargetPublished': 'Published',
        'settings.universalSkills.evalPrompt': 'Prompt',
        'settings.universalSkills.evalLocale': 'Locale',
        'settings.universalSkills.evalMode': 'Evaluation mode',
        'settings.universalSkills.profileVersion': 'Profile version',
        'settings.universalSkills.datasetVersion': 'Dataset version',
        'settings.universalSkills.providerFixture': 'Provider fixture',
        'settings.universalSkills.providerFixtureStructuralDefault':
          'Structural synthetic (no fixture pin)',
        'settings.universalSkills.liveModelId': 'Live model ID',
        'settings.universalSkills.startEval': 'Start evaluation',
        'settings.universalSkills.cancelEval': 'Cancel evaluation',
        'settings.universalSkills.loadingProfiles': 'Loading profiles…',
        'settings.universalSkills.selectDatasetVersion': 'Select dataset version',
        'settings.universalSkills.workbenchNeedsDraft':
          'A draft version is required to start evaluation.',
        'settings.universalSkills.workbenchInvalidInputs':
          'Complete required workbench inputs for the selected mode.',
        'messages.loading': 'Loading…',
        'common.back': 'Back',
        'common.cancel': 'Cancel',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

function draftVersion() {
  return {
    id: DRAFT_ID,
    profileId: PROFILE_ID,
    sequenceNo: 2,
    versionName: 'draft',
    versionSource: 'save' as const,
    origin: 'ui',
    contentDigest: 'a'.repeat(64),
  }
}

function publishedVersion() {
  return {
    id: PUBLISHED_ID,
    profileId: PROFILE_ID,
    sequenceNo: 1,
    versionName: 'v1',
    versionSource: 'publish' as const,
    origin: 'ui',
    contentDigest: 'f'.repeat(64),
  }
}

function profileSummary(overrides: Record<string, unknown> = {}) {
  return {
    id: PROFILE_ID,
    profileKey: 'default',
    displayName: 'Default Main Agent',
    isDefault: true,
    migrationState: 'native',
    runtimeEnabled: false,
    aggregateRevision: 2,
    draftVersion: draftVersion(),
    publishedVersion: null as null | Record<string, unknown>,
    ...overrides,
  }
}

function snapshot() {
  return {
    schemaVersion: 1 as const,
    basePrompt: 'You are the MindAtlas main assistant.',
    responseStyle: {},
    supportedEntrypoints: ['assistant_chat'],
    modelRequirements: {
      toolCalling: true,
      streaming: true,
      multiToolCalls: true,
      jsonSchema: true,
    },
    controlCapabilityKeys: [],
    skillCatalogScope: { mode: 'all_published' as const, packageIds: [] },
    contextBudget: {
      maxPromptCharacters: 72000,
      maxActiveSkills: 4,
      maxSkillInstructionCharacters: 24000,
      maxSingleSkillInstructionCharacters: 12000,
      maxHistoryCharacters: 24000,
      maxToolSummaryCharacters: 24000,
      maxResourceBytesPerCall: 65536,
    },
    outputBudget: {
      maxCompletionTokens: 4096,
      maxProviderRounds: 8,
      maxOuterAgentRounds: 8,
      maxTotalCapabilityCalls: 16,
      maxParallelCalls: 4,
      maxCapabilityDepth: 4,
      maxAgentDepth: 2,
      maxSameReadSignature: 3,
      maxCompletionFollowupRounds: 2,
      maxWallTimeMs: 120000,
    },
    globalSafetyPolicy: { denyByDefault: true },
    fallbackPolicy: { legacyRuntimeAllowed: true, beforeSideEffectsOnly: true },
  }
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/settings/main-agent-profile']}>
        <Routes>
          <Route path="/settings/main-agent-profile" element={<MainAgentProfileEditorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function lastGateRequest() {
  return gateRequests[gateRequests.length - 1]
}

describe('MainAgentProfileEditorPage two-gate lifecycle', () => {
  beforeEach(() => {
    gateRequests.length = 0
    vi.clearAllMocks()
    vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
      profileSummary() as never,
    )
    vi.mocked(profilesApi.listProtectedDefaultMainAgentVersions).mockResolvedValue({
      items: [draftVersion()],
      total: 1,
    })
    vi.mocked(profilesApi.getProtectedDefaultMainAgentVersion).mockResolvedValue({
      ...draftVersion(),
      snapshot: snapshot(),
    } as never)
    vi.mocked(profilesApi.saveProtectedDefaultMainAgentDraft).mockImplementation(async () => {
      // Draft save must not flip runtime; keep runtimeEnabled true if it was true.
      return draftVersion() as never
    })
    vi.mocked(profilesApi.publishProtectedDefaultMainAgent).mockImplementation(async () => {
      const published = publishedVersion()
      vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
        profileSummary({
          aggregateRevision: 3,
          draftVersion: null,
          publishedVersion: published,
        }) as never,
      )
      vi.mocked(profilesApi.listProtectedDefaultMainAgentVersions).mockResolvedValue({
        items: [published],
        total: 1,
      })
      return published as never
    })
    vi.mocked(profilesApi.enableProtectedDefaultMainAgentRuntime).mockResolvedValue(
      profileSummary({
        runtimeEnabled: true,
        draftVersion: null,
        publishedVersion: publishedVersion(),
        aggregateRevision: 4,
      }) as never,
    )
    vi.mocked(profilesApi.disableProtectedDefaultMainAgentRuntime).mockResolvedValue(
      profileSummary({
        runtimeEnabled: false,
        draftVersion: null,
        publishedVersion: publishedVersion(),
        aggregateRevision: 5,
      }) as never,
    )
    vi.mocked(skillEvaluations.listQualifyingEvidence).mockImplementation(async (params) => {
      if (params?.subjectKind === 'main_agent_profile_draft') {
        return {
          items: [
            {
              evalRunId: DRAFT_RUN_ID,
              mode: 'dataset_scripted',
              status: 'completed',
              gateEligible: true,
              evidenceProvenance: 'real_orchestration',
              subjectKind: 'main_agent_profile_draft',
              subjectVersionId: DRAFT_ID,
              aggregateMetrics: {},
            },
          ],
          total: 1,
        }
      }
      if (params?.subjectKind === 'main_agent_profile_version') {
        return {
          items: [
            {
              evalRunId: PROMO_RUN_ID,
              mode: 'dataset_scripted',
              status: 'completed',
              gateEligible: true,
              evidenceProvenance: 'real_orchestration',
              subjectKind: 'main_agent_profile_version',
              subjectVersionId: PUBLISHED_ID,
              aggregateMetrics: {},
            },
          ],
          total: 1,
        }
      }
      return { items: [], total: 0 }
    })
    vi.mocked(skillEvaluations.createPublishGate).mockImplementation(async (body) => {
      gateRequests.push({ ...body })
      const isPromo = body.action === 'profile_runtime_enable'
      return {
        gate: {
          id: isPromo ? PROMOTION_GATE_ID : PUBLISH_GATE_ID,
          decision: 'passed',
          subjectKind: isPromo ? 'main_agent_profile_version' : 'main_agent_profile_draft',
          subjectAggregateId: body.subjectAggregateId,
          subjectVersionId: body.subjectVersionId,
          waiverCodes: [],
          requestId: body.requestId,
          action: body.action,
        },
        decision: 'passed',
        acceptedWaiverCodes: [],
        assertionSnapshot: {
          subjectKind: isPromo ? 'main_agent_profile_version' : 'main_agent_profile_draft',
          subjectVersionId: body.subjectVersionId,
          contentDigest: 'a'.repeat(64),
        },
        metricSnapshot: { caseCount: 2 },
      }
    })
  })

  it('does not expose a manual promotionGateId text input', async () => {
    renderPage()
    await screen.findByRole('button', { name: 'Enable runtime' })
    expect(screen.queryByPlaceholderText('gate-uuid')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Promotion gate ID')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue(/gate-/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open publish gate dialog' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open promotion gate dialog' })).toBeInTheDocument()
  })

  it('gate request contains no client-authored closure fields', async () => {
    renderPage()
    await screen.findByRole('button', { name: 'Open publish gate dialog' })
    fireEvent.click(screen.getByRole('button', { name: 'Open publish gate dialog' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
    await waitFor(() => {
      expect(gateRequests.length).toBeGreaterThan(0)
    })
    expect(lastGateRequest()).toEqual({
      requestId: expect.any(String),
      action: 'profile_publish',
      subjectAggregateId: PROFILE_ID,
      subjectVersionId: DRAFT_ID,
      qualifyingEvalRunIds: [DRAFT_RUN_ID],
      requestedNonSafetyWaiverCodes: [],
      waiverReason: null,
    })
    expect(lastGateRequest()).not.toHaveProperty('subject')
    expect(lastGateRequest()).not.toHaveProperty('passed')
    expect(lastGateRequest()).not.toHaveProperty('decision')
    expect(lastGateRequest()).not.toHaveProperty('metrics')
    expect(lastGateRequest()).not.toHaveProperty('assertions')
    expect(lastGateRequest()).not.toHaveProperty('contentDigest')
    expect(lastGateRequest()).not.toHaveProperty('profileDigest')
  })

  it('does not reuse the draft publish gate after publish for runtime enable', async () => {
    renderPage()
    await screen.findByRole('button', { name: 'Open publish gate dialog' })

    fireEvent.click(screen.getByRole('button', { name: 'Open publish gate dialog' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
    await waitFor(() => {
      expect(lastGateRequest()?.action).toBe('profile_publish')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.click(screen.getByRole('button', { name: 'Publish profile' }))
    await waitFor(() => {
      expect(profilesApi.publishProtectedDefaultMainAgent).toHaveBeenCalledWith(
        expect.objectContaining({
          draftVersionId: DRAFT_ID,
          gateId: PUBLISH_GATE_ID,
        }),
      )
    })

    // After publish, enable must stay disabled until a fresh promotion gate on published subject.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Enable runtime' })).toBeDisabled()
    })
    expect(
      await screen.findByText('Evaluate the published version before enabling'),
    ).toBeVisible()
    expect(profilesApi.enableProtectedDefaultMainAgentRuntime).not.toHaveBeenCalled()
  })

  it('enable uses a profile_runtime_enable gate on the published version', async () => {
    vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
      profileSummary({
        draftVersion: null,
        publishedVersion: publishedVersion(),
        aggregateRevision: 3,
      }) as never,
    )
    vi.mocked(profilesApi.listProtectedDefaultMainAgentVersions).mockResolvedValue({
      items: [publishedVersion()],
      total: 1,
    })
    vi.mocked(profilesApi.getProtectedDefaultMainAgentVersion).mockResolvedValue({
      ...publishedVersion(),
      snapshot: snapshot(),
    } as never)

    renderPage()
    await screen.findByRole('button', { name: 'Open promotion gate dialog' })

    // Without promotion gate, enable stays disabled.
    expect(screen.getByRole('button', { name: 'Enable runtime' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Open promotion gate dialog' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
    await waitFor(() => {
      expect(lastGateRequest()?.action).toBe('profile_runtime_enable')
    })
    expect(lastGateRequest()).toMatchObject({
      action: 'profile_runtime_enable',
      subjectAggregateId: PROFILE_ID,
      subjectVersionId: PUBLISHED_ID,
      qualifyingEvalRunIds: [PROMO_RUN_ID],
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Enable runtime' })).not.toBeDisabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enable runtime' }))
    await waitFor(() => {
      expect(profilesApi.enableProtectedDefaultMainAgentRuntime).toHaveBeenCalledWith(
        expect.objectContaining({
          gateId: PROMOTION_GATE_ID,
          expectedPublishedVersionId: PUBLISHED_ID,
          expectedAggregateRevision: 3,
        }),
      )
    })
  })

  it('draft save never demotes runtimeEnabled UI state', async () => {
    vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
      profileSummary({
        runtimeEnabled: true,
        draftVersion: draftVersion(),
        publishedVersion: publishedVersion(),
        aggregateRevision: 4,
      }) as never,
    )
    vi.mocked(profilesApi.listProtectedDefaultMainAgentVersions).mockResolvedValue({
      items: [draftVersion(), publishedVersion()],
      total: 2,
    })
    // After draft save, server still reports runtimeEnabled true.
    vi.mocked(profilesApi.saveProtectedDefaultMainAgentDraft).mockImplementation(async () => {
      vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
        profileSummary({
          runtimeEnabled: true,
          draftVersion: { ...draftVersion(), id: 'draft-new-9999' },
          publishedVersion: publishedVersion(),
          aggregateRevision: 5,
        }) as never,
      )
      return { ...draftVersion(), id: 'draft-new-9999' } as never
    })

    renderPage()
    await screen.findByText(/runtime=enabled/)
    const prompt = await screen.findByDisplayValue('You are the MindAtlas main assistant.')
    fireEvent.change(prompt, { target: { value: 'Updated base prompt for draft only.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => {
      expect(profilesApi.saveProtectedDefaultMainAgentDraft).toHaveBeenCalled()
    })
    expect(profilesApi.enableProtectedDefaultMainAgentRuntime).not.toHaveBeenCalled()
    expect(profilesApi.disableProtectedDefaultMainAgentRuntime).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.getByText(/runtime=enabled/)).toBeInTheDocument()
    })
  })

  it('renders evaluation workbench for profile draft subject', async () => {
    renderPage()
    const workbench = await screen.findByTestId('profile-eval-workbench')
    expect(workbench).toBeInTheDocument()
    expect(screen.getByText('Evaluation workbench')).toBeInTheDocument()
    const inner = await screen.findByTestId('skill-test-workbench')
    expect(inner).toHaveAttribute('data-subject-kind', 'main_agent_profile_draft')
  })

  it('exposes dual-pointer eval target selector when draft and published both exist', async () => {
    vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
      profileSummary({
        draftVersion: draftVersion(),
        publishedVersion: publishedVersion(),
        aggregateRevision: 4,
      }) as never,
    )
    vi.mocked(profilesApi.listProtectedDefaultMainAgentVersions).mockResolvedValue({
      items: [draftVersion(), publishedVersion()],
      total: 2,
    })
    renderPage()
    const selector = await screen.findByTestId('profile-eval-target-selector')
    expect(selector).toBeInTheDocument()
    const inner = await screen.findByTestId('skill-test-workbench')
    // Default dual-pointer target is draft for publish evidence.
    expect(inner).toHaveAttribute('data-subject-kind', 'main_agent_profile_draft')

    fireEvent.click(screen.getByRole('button', { name: 'Published' }))
    await waitFor(() => {
      expect(screen.getByTestId('skill-test-workbench')).toHaveAttribute(
        'data-subject-kind',
        'main_agent_profile_version',
      )
    })

    fireEvent.click(screen.getByRole('button', { name: 'Draft' }))
    await waitFor(() => {
      expect(screen.getByTestId('skill-test-workbench')).toHaveAttribute(
        'data-subject-kind',
        'main_agent_profile_draft',
      )
    })
  })

  it('switches workbench to published subject after publish while draft remains', async () => {
    // Dual pointer after publish: draft still present alongside published.
    vi.mocked(profilesApi.publishProtectedDefaultMainAgent).mockImplementation(async () => {
      const published = publishedVersion()
      vi.mocked(profilesApi.getProtectedDefaultMainAgentProfile).mockResolvedValue(
        profileSummary({
          aggregateRevision: 3,
          draftVersion: draftVersion(),
          publishedVersion: published,
        }) as never,
      )
      vi.mocked(profilesApi.listProtectedDefaultMainAgentVersions).mockResolvedValue({
        items: [draftVersion(), published],
        total: 2,
      })
      return published as never
    })

    renderPage()
    await screen.findByTestId('skill-test-workbench')
    // Request publish gate then publish.
    fireEvent.click(screen.getByRole('button', { name: 'Open publish gate dialog' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
    await waitFor(() => {
      expect(skillEvaluations.createPublishGate).toHaveBeenCalled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Publish profile' }))
    await waitFor(() => {
      expect(profilesApi.publishProtectedDefaultMainAgent).toHaveBeenCalled()
    })
    // After publish, dual-pointer selector defaults to published for promotion eval.
    await waitFor(() => {
      expect(screen.getByTestId('skill-test-workbench')).toHaveAttribute(
        'data-subject-kind',
        'main_agent_profile_version',
      )
    })
    expect(screen.getByTestId('profile-eval-target-selector')).toBeInTheDocument()
  })

  it('can start a profile draft eval run via workbench (mocked API)', async () => {
    const createEvalRun = vi.mocked(skillEvaluations.createEvalRun)
    createEvalRun.mockResolvedValue({
      id: 'run-new-profile-eval',
      status: 'queued',
      mode: 'interactive_scripted',
      subjectKind: 'main_agent_profile_draft',
      subjectAggregateId: PROFILE_ID,
      subjectVersionId: DRAFT_ID,
      stateRevision: 0,
      gateEligible: false,
      evidenceProvenance: 'structural_synthetic',
      aggregateMetrics: {},
    } as never)

    renderPage()
    await screen.findByTestId('skill-test-workbench')
    // Wait for profile pin list so start becomes enabled.
    await screen.findByLabelText('Profile version')
    await waitFor(() => {
      const select = screen.getByLabelText('Profile version') as HTMLSelectElement
      expect(select.value).toBeTruthy()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Start evaluation' }))
    await waitFor(() => {
      expect(createEvalRun).toHaveBeenCalled()
    })
    const body = createEvalRun.mock.calls[0][0]
    expect(body.subjectKind).toBe('main_agent_profile_draft')
    expect(body.subjectAggregateId).toBe(PROFILE_ID)
    expect(body.subjectVersionId).toBe(DRAFT_ID)
    // Profile subject pins the evaluated version when no separate pin selected.
    expect(body.profileVersionId).toBeTruthy()
  })
})
