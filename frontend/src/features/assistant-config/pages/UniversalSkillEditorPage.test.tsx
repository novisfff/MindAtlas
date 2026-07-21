import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { UniversalSkillEditorPage } from './UniversalSkillEditorPage'
import * as skillPackagesApi from '../api/skill-packages'
import * as skillEvaluations from '../api/skill-evaluations'
import { useSkillTestRunStore } from '../stores/skill-test-run-store'
import { useSkillEditorStore } from '../stores/skill-editor-store'

const PACKAGE_ID = 'pkg-1111-2222-3333'
const DRAFT_ID = 'draft-aaaa-bbbb-cccc'
const PUBLISHED_ID = 'pub-dddd-eeee-ffff'
const RUN_ID = 'run-1111-2222-3333'
const PUBLISH_GATE_ID = 'gate-publish-0001'
const PROMOTION_GATE_ID = 'gate-promo-0002'

const gateRequests: Array<Record<string, unknown>> = []

vi.mock('../api/skill-packages', async () => {
  const actual = await vi.importActual<typeof import('../api/skill-packages')>(
    '../api/skill-packages',
  )
  return {
    ...actual,
    probeSkillAdminSurface: vi.fn(),
    getSkillPackage: vi.fn(),
    getSkillPackageVersion: vi.fn(),
    listSkillPackageVersions: vi.fn(),
    publishSkillPackageVersion: vi.fn(),
    enableSkillPackageCatalog: vi.fn(),
    saveSkillPackageDraft: vi.fn(),
    patchSkillPackageMetadata: vi.fn(),
    restoreSkillPackageVersionAsDraft: vi.fn(),
    diffSkillPackageVersions: vi.fn(),
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
  }
})

vi.mock('../components/UniversalSkillEditor', () => ({
  UniversalSkillEditor: ({
    evalSubjectKind,
    evalVersionId,
  }: {
    evalSubjectKind?: string
    evalVersionId?: string | null
  }) => (
    <div data-testid="skill-editor">
      workbench={evalSubjectKind ?? 'skill_draft'}:{evalVersionId ?? 'none'}
    </div>
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.editorTitle': 'Skill package editor',
        'settings.universalSkills.unavailableDesc': 'Universal Skills unavailable',
        'settings.universalSkills.unavailableBody':
          'The skill package admin surface is not mounted or not reachable.',
        'settings.universalSkills.openLegacy': 'Open legacy Skill Library',
        'settings.universalSkills.noDraftVersion': 'No draft version selected.',
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
        'settings.universalSkills.publishDraft': 'Publish draft',
        'settings.universalSkills.enableCatalog': 'Enable catalog',
        'settings.universalSkills.evaluatePublishedBeforeEnable':
          'Evaluate the published version before enabling',
        'settings.universalSkills.adminUnmountedHint':
          'Admin lifecycle routes are unmounted.',
        'settings.universalSkills.draftLoadFailed': 'Draft version details could not be loaded.',
        'settings.universalSkills.unsavedConfirm': 'You have unsaved changes. Leave this page?',
        'messages.loading': 'Loading…',
        'common.back': 'Back',
        'common.cancel': 'Cancel',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

function packageDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: PACKAGE_ID,
    canonicalName: 'demo-skill',
    displayName: 'Demo Skill',
    description: '',
    migrationState: 'native' as const,
    catalogEnabled: false,
    isSystem: false,
    aggregateRevision: 2,
    aliases: [],
    draftVersion: {
      id: DRAFT_ID,
      skillPackageId: PACKAGE_ID,
      sequenceNo: 2,
      versionName: 'draft',
      versionSource: 'save' as const,
      origin: 'ui',
      contentDigest: 'a'.repeat(64),
      skillMdDigest: 'b'.repeat(64),
      manifestDigest: 'c'.repeat(64),
      resourceIndexDigest: 'd'.repeat(64),
      bindingSetDigest: 'e'.repeat(64),
    },
    publishedVersion: null as null | Record<string, unknown>,
    ...overrides,
  }
}

function publishedVersion() {
  return {
    id: PUBLISHED_ID,
    skillPackageId: PACKAGE_ID,
    sequenceNo: 1,
    versionName: 'v1',
    versionSource: 'publish' as const,
    origin: 'ui',
    contentDigest: 'f'.repeat(64),
    skillMdDigest: 'g'.repeat(64),
    manifestDigest: 'h'.repeat(64),
    resourceIndexDigest: 'i'.repeat(64),
    bindingSetDigest: 'j'.repeat(64),
  }
}

function seedPassingDraftRun() {
  useSkillTestRunStore.getState().beginRun({
    id: RUN_ID,
    subjectKind: 'skill_draft',
    subjectAggregateId: PACKAGE_ID,
    subjectVersionId: DRAFT_ID,
    mode: 'dataset_scripted',
    status: 'completed',
    stateRevision: 3,
    lastEventSeq: 4,
    gateEligible: true,
    evidenceProvenance: 'real_orchestration',
  })
}

function renderEditor() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/settings/universal-skills/${PACKAGE_ID}`]}>
        <Routes>
          <Route
            path="/settings/universal-skills/:packageId"
            element={<UniversalSkillEditorPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

async function renderEditorWithPassingDraftRun() {
  seedPassingDraftRun()
  renderEditor()
  expect(await screen.findByRole('button', { name: 'Enable catalog' })).toBeInTheDocument()
}

async function requestGateAndPublish() {
  fireEvent.click(screen.getByRole('button', { name: 'Open publish gate dialog' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
  await waitFor(() => {
    expect(gateRequests.length).toBeGreaterThan(0)
  })
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  fireEvent.click(screen.getByRole('button', { name: 'Publish draft' }))
  await waitFor(() => {
    expect(skillPackagesApi.publishSkillPackageVersion).toHaveBeenCalled()
  })
}

async function requestDraftPublishGate() {
  seedPassingDraftRun()
  renderEditor()
  await screen.findByRole('button', { name: 'Open publish gate dialog' })
  fireEvent.click(screen.getByRole('button', { name: 'Open publish gate dialog' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
  await waitFor(() => {
    expect(gateRequests.length).toBeGreaterThan(0)
  })
}

function lastGateRequest() {
  return gateRequests[gateRequests.length - 1]
}

describe('UniversalSkillEditorPage two-gate lifecycle', () => {
  beforeEach(() => {
    gateRequests.length = 0
    useSkillTestRunStore.getState().reset()
    useSkillEditorStore.getState().clear()
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: true,
      packagesReadable: true,
      adminMounted: true,
    })
    vi.mocked(skillPackagesApi.getSkillPackage).mockResolvedValue(packageDetail() as never)
    vi.mocked(skillPackagesApi.getSkillPackageVersion).mockResolvedValue({
      ...packageDetail().draftVersion,
      frontmatter: {},
      resources: [],
      skillMd: '---\nname: demo\n---\n',
      mindatlasYaml: 'schemaVersion: 1\n',
    } as never)
    vi.mocked(skillPackagesApi.listSkillPackageVersions).mockResolvedValue({
      items: [packageDetail().draftVersion as never],
      total: 1,
      limit: 50,
      offset: 0,
    })
    vi.mocked(skillEvaluations.listQualifyingEvidence).mockResolvedValue({
      items: [
        {
          evalRunId: RUN_ID,
          mode: 'dataset_scripted',
          status: 'completed',
          gateEligible: true,
          evidenceProvenance: 'real_orchestration',
          subjectKind: 'skill_draft',
          subjectVersionId: DRAFT_ID,
          aggregateMetrics: {},
        },
      ],
      total: 1,
    })
    vi.mocked(skillEvaluations.createPublishGate).mockImplementation(async (body) => {
      gateRequests.push({ ...body })
      const isPromo = body.action === 'skill_catalog_enable'
      return {
        gate: {
          id: isPromo ? PROMOTION_GATE_ID : PUBLISH_GATE_ID,
          decision: 'passed',
          subjectKind: isPromo ? 'skill_version' : 'skill_draft',
          subjectAggregateId: body.subjectAggregateId,
          subjectVersionId: body.subjectVersionId,
          waiverCodes: [],
          requestId: body.requestId,
          action: body.action,
        },
        decision: 'passed',
        acceptedWaiverCodes: [],
        assertionSnapshot: {
          subjectKind: isPromo ? 'skill_version' : 'skill_draft',
          subjectVersionId: body.subjectVersionId,
          contentDigest: 'a'.repeat(64),
        },
        metricSnapshot: { caseCount: 3 },
      }
    })
    vi.mocked(skillPackagesApi.publishSkillPackageVersion).mockImplementation(async () => {
      const published = publishedVersion()
      vi.mocked(skillPackagesApi.getSkillPackage).mockResolvedValue(
        packageDetail({
          aggregateRevision: 3,
          draftVersion: null,
          publishedVersion: published,
        }) as never,
      )
      return published as never
    })
    vi.mocked(skillPackagesApi.enableSkillPackageCatalog).mockResolvedValue(
      packageDetail({
        catalogEnabled: true,
        publishedVersion: publishedVersion(),
        draftVersion: null,
      }) as never,
    )
  })

  it('does not reuse the draft gate after publish', async () => {
    await renderEditorWithPassingDraftRun()
    await requestGateAndPublish()
    expect(screen.getByRole('button', { name: 'Enable catalog' })).toBeDisabled()
    expect(
      await screen.findByText('Evaluate the published version before enabling'),
    ).toBeVisible()
    // Publish must not leave a usable promotion gate from the draft gate.
    expect(skillPackagesApi.enableSkillPackageCatalog).not.toHaveBeenCalled()
  })

  it('gate request contains no client-authored closure', async () => {
    await requestDraftPublishGate()
    expect(lastGateRequest()).toEqual({
      requestId: expect.any(String),
      action: 'skill_publish',
      subjectAggregateId: PACKAGE_ID,
      subjectVersionId: DRAFT_ID,
      qualifyingEvalRunIds: [RUN_ID],
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

  it('switches workbench to published subject after publish', async () => {
    await renderEditorWithPassingDraftRun()
    expect(screen.getByTestId('skill-editor')).toHaveTextContent(`workbench=skill_draft:${DRAFT_ID}`)
    await requestGateAndPublish()
    await waitFor(() => {
      expect(screen.getByTestId('skill-editor')).toHaveTextContent(
        `workbench=skill_version:${PUBLISHED_ID}`,
      )
    })
    // Draft eval run must be cleared after publish.
    expect(useSkillTestRunStore.getState().run).toBeNull()
  })

  it('enable uses a skill_catalog_enable gate on the published version', async () => {
    // Start already published with a promotion-qualifying run.
    vi.mocked(skillPackagesApi.getSkillPackage).mockResolvedValue(
      packageDetail({
        draftVersion: null,
        publishedVersion: publishedVersion(),
        aggregateRevision: 3,
      }) as never,
    )
    useSkillTestRunStore.getState().beginRun({
      id: 'promo-run-1',
      subjectKind: 'skill_version',
      subjectAggregateId: PACKAGE_ID,
      subjectVersionId: PUBLISHED_ID,
      mode: 'dataset_scripted',
      status: 'completed',
      stateRevision: 2,
      lastEventSeq: 2,
      gateEligible: true,
      evidenceProvenance: 'real_orchestration',
    })
    vi.mocked(skillEvaluations.listQualifyingEvidence).mockResolvedValue({
      items: [
        {
          evalRunId: 'promo-run-1',
          mode: 'dataset_scripted',
          status: 'completed',
          gateEligible: true,
          evidenceProvenance: 'real_orchestration',
          subjectKind: 'skill_version',
          subjectVersionId: PUBLISHED_ID,
          aggregateMetrics: {},
        },
      ],
      total: 1,
    })

    renderEditor()
    await screen.findByRole('button', { name: 'Open promotion gate dialog' })
    fireEvent.click(screen.getByRole('button', { name: 'Open promotion gate dialog' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
    await waitFor(() => {
      expect(lastGateRequest()?.action).toBe('skill_catalog_enable')
    })
    expect(lastGateRequest()).toMatchObject({
      action: 'skill_catalog_enable',
      subjectAggregateId: PACKAGE_ID,
      subjectVersionId: PUBLISHED_ID,
      qualifyingEvalRunIds: ['promo-run-1'],
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Enable catalog' })).not.toBeDisabled()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enable catalog' }))
    await waitFor(() => {
      expect(skillPackagesApi.enableSkillPackageCatalog).toHaveBeenCalledWith(
        PACKAGE_ID,
        expect.objectContaining({
          gateId: PROMOTION_GATE_ID,
          expectedAggregateRevision: 3,
        }),
      )
    })
  })
})
