import type { PropsWithChildren, ReactNode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Plan09RouteGate } from './Plan09RouteGate'
import * as skillPackagesApi from '../api/skill-packages'
import * as queries from '../queries'

vi.mock('../api/skill-packages', async () => {
  const actual = await vi.importActual<typeof import('../api/skill-packages')>(
    '../api/skill-packages',
  )
  return {
    ...actual,
    probeSkillAdminSurface: vi.fn(),
    getSkillPackage: vi.fn(),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.unavailableDesc': 'Universal Skills unavailable',
        'settings.universalSkills.unavailableBody':
          'The skill package admin surface is not mounted or not reachable.',
        'settings.universalSkills.openLegacy': 'Open legacy Skill Library',
        'messages.loading': 'Loading…',
        'common.back': 'Back',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

const protectedPackageRequests: string[] = []

function ProtectedPackagePage() {
  // Mimic a page that would fetch a package once mounted.
  queries.useSkillPackageQuery('package-1')
  return <div>protected package content</div>
}

function renderAt(path: string, children?: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/settings/universal-skills/:packageId"
            element={<Plan09RouteGate>{children ?? <ProtectedPackagePage />}</Plan09RouteGate>}
          />
          <Route path="/settings/assistant-skills" element={<div>legacy skills</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Plan09RouteGate', () => {
  beforeEach(() => {
    protectedPackageRequests.length = 0
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockReset()
    vi.mocked(skillPackagesApi.getSkillPackage).mockReset()
    vi.mocked(skillPackagesApi.getSkillPackage).mockImplementation(async (id) => {
      protectedPackageRequests.push(id)
      return {
        id,
        canonicalName: 'demo',
        displayName: 'Demo',
        description: '',
        migrationState: 'native',
        catalogEnabled: false,
        isSystem: false,
        aggregateRevision: 1,
        aliases: [],
      }
    })
  })

  it('direct URL fails closed before protected package fetch', async () => {
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: false,
      packagesReadable: false,
      adminMounted: false,
      reason: 'admin_unmounted',
    })

    renderAt('/settings/universal-skills/package-1')

    expect(await screen.findByRole('alert')).toHaveTextContent('Universal Skills unavailable')
    await waitFor(() => {
      expect(protectedPackageRequests).toHaveLength(0)
    })
    expect(skillPackagesApi.getSkillPackage).not.toHaveBeenCalled()
    expect(screen.queryByText('protected package content')).toBeNull()
  })

  it('principal unauthorized (401/403) fails closed without protected package fetch', async () => {
    // Probe maps 401/403 → available=false + principal_unauthorized (admin may still be mounted).
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: false,
      packagesReadable: false,
      adminMounted: true,
      reason: 'principal_unauthorized',
    })

    renderAt('/settings/universal-skills/package-1')

    expect(await screen.findByRole('alert')).toHaveTextContent('Universal Skills unavailable')
    await waitFor(() => {
      expect(protectedPackageRequests).toHaveLength(0)
    })
    expect(skillPackagesApi.getSkillPackage).not.toHaveBeenCalled()
    expect(screen.queryByText('protected package content')).toBeNull()
  })

  it('renders children only after feature/principal probe succeeds', async () => {
    vi.mocked(skillPackagesApi.probeSkillAdminSurface).mockResolvedValue({
      available: true,
      packagesReadable: true,
      adminMounted: true,
    })

    renderAt('/settings/universal-skills/package-1')

    expect(await screen.findByText('protected package content')).toBeVisible()
    await waitFor(() => {
      expect(protectedPackageRequests).toContain('package-1')
    })
  })
})
