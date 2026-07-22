/**
 * Plan 09 Task 12 — component-level lifecycle evidence (not full browser E2E).
 *
 * Covers the fail-closed two-gate UI contract:
 * - draft publish gate is not reused for catalog enable
 * - Plan09RouteGate fails closed before protected fetches
 * - gate request body carries no client-authored digests/decisions
 *
 * Reuses patterns from UniversalSkillEditorPage.test.tsx and Plan09RouteGate.test.tsx.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { UniversalSkillEditorPage } from './pages/UniversalSkillEditorPage'
import { Plan09RouteGate } from './components/Plan09RouteGate'
import * as skillPackagesApi from './api/skill-packages'
import * as skillEvaluations from './api/skill-evaluations'
import * as queries from './queries'
import { useSkillTestRunStore } from './stores/skill-test-run-store'
import { useSkillEditorStore } from './stores/skill-editor-store'

const PACKAGE_ID = 'pkg-plan09-life-0001'
const DRAFT_ID = 'draft-plan09-life-0001'
const PUBLISHED_ID = 'pub-plan09-life-0001'
const RUN_ID = 'run-plan09-life-0001'
const PUBLISH_GATE_ID = 'gate-publish-life-1'
const PROMOTION_GATE_ID = 'gate-promo-life-2'

const gateRequests: Array<Record<string, unknown>> = []
const protectedPackageRequests: string[] = []

vi.mock('./api/skill-packages', async () => {
  const actual = await vi.importActual<typeof import('./api/skill-packages')>(
    './api/skill-packages',
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

vi.mock('./api/skill-evaluations', async () => {
  const actual = await vi.importActual<typeof import('./api/skill-evaluations')>(
    './api/skill-evaluations',
  )
  return {
    ...actual,
    createPublishGate: vi.fn(),
    listQualifyingEvidence: vi.fn(),
  }
})

vi.mock('./components/UniversalSkillEditor', () => ({
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
    canonicalName: 'plan09-life-skill',
    displayName: 'Plan09 Life Skill',
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

function ProtectedPackagePage() {
  queries.useSkillPackageQuery('package-1')
  return <div>protected package content</div>
}

function renderRouteGate(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/settings/universal-skills/:packageId"
            element={
              <Plan09RouteGate>
                <ProtectedPackagePage />
              </Plan09RouteGate>
            }
          />
          <Route path="/settings/assistant-skills" element={<div>legacy skills</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Plan 09 lifecycle component evidence', () => {
  beforeEach(() => {
    gateRequests.length = 0
    protectedPackageRequests.length = 0
    useSkillTestRunStore.getState().reset()
    useSkillEditorStore.getState().clear()
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockReset()
    vi.mocked(skillPackagesApi.getSkillPackage).mockReset()
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: true,
      packagesReadable: true,
      adminMounted: true,
    })
    vi.mocked(skillPackagesApi.getSkillPackage).mockImplementation(async (id) => {
      if (id === 'package-1') {
        protectedPackageRequests.push(id)
      }
      return packageDetail() as never
    })
    vi.mocked(skillPackagesApi.getSkillPackageVersion).mockResolvedValue({
      ...packageDetail().draftVersion,
      frontmatter: {},
      resources: [],
      skillMd: '---\nname: plan09-life-skill\n---\n',
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

  it('two-gate: draft gate is not reused for enable after publish', async () => {
    seedPassingDraftRun()
    renderEditor()
    expect(await screen.findByRole('button', { name: 'Enable catalog' })).toBeInTheDocument()

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

    expect(screen.getByRole('button', { name: 'Enable catalog' })).toBeDisabled()
    expect(
      await screen.findByText('Evaluate the published version before enabling'),
    ).toBeVisible()
    expect(skillPackagesApi.enableSkillPackageCatalog).not.toHaveBeenCalled()
  })

  it('gate request has no client digests or decisions', async () => {
    seedPassingDraftRun()
    renderEditor()
    await screen.findByRole('button', { name: 'Open publish gate dialog' })
    fireEvent.click(screen.getByRole('button', { name: 'Open publish gate dialog' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Request gate' }))
    await waitFor(() => {
      expect(gateRequests.length).toBeGreaterThan(0)
    })
    const body = gateRequests[gateRequests.length - 1]
    expect(body).toEqual({
      requestId: expect.any(String),
      action: 'skill_publish',
      subjectAggregateId: PACKAGE_ID,
      subjectVersionId: DRAFT_ID,
      qualifyingEvalRunIds: [RUN_ID],
      requestedNonSafetyWaiverCodes: [],
      waiverReason: null,
    })
    for (const banned of [
      'subject',
      'passed',
      'decision',
      'metrics',
      'assertions',
      'contentDigest',
      'profileDigest',
      'catalogDigest',
      'bindingDigest',
    ]) {
      expect(body).not.toHaveProperty(banned)
    }
  })

  it('Plan09RouteGate fails closed without feature/principal', async () => {
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: false,
      packagesReadable: false,
      adminMounted: false,
      reason: 'admin_unmounted',
    })
    renderRouteGate('/settings/universal-skills/package-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Universal Skills unavailable')
    await waitFor(() => {
      expect(protectedPackageRequests).toHaveLength(0)
    })
    expect(screen.queryByText('protected package content')).toBeNull()
  })

  it('Plan09RouteGate fails closed on principal unauthorized', async () => {
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: false,
      packagesReadable: false,
      adminMounted: true,
      reason: 'principal_unauthorized',
    })
    renderRouteGate('/settings/universal-skills/package-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Universal Skills unavailable')
    await waitFor(() => {
      expect(protectedPackageRequests).toHaveLength(0)
    })
  })

  it('promotion gate targets published version only', async () => {
    vi.mocked(skillPackagesApi.getSkillPackage).mockResolvedValue(
      packageDetail({
        draftVersion: null,
        publishedVersion: publishedVersion(),
        aggregateRevision: 3,
      }) as never,
    )
    useSkillTestRunStore.getState().beginRun({
      id: 'promo-run-life',
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
          evalRunId: 'promo-run-life',
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
      expect(gateRequests[gateRequests.length - 1]?.action).toBe('skill_catalog_enable')
    })
    expect(gateRequests[gateRequests.length - 1]).toMatchObject({
      action: 'skill_catalog_enable',
      subjectAggregateId: PACKAGE_ID,
      subjectVersionId: PUBLISHED_ID,
      qualifyingEvalRunIds: ['promo-run-life'],
    })
  })
})
