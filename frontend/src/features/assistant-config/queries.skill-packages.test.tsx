import type { PropsWithChildren } from 'react'
import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  skillAdminSurfaceQueryKey,
  skillPackageQueryKey,
  skillPackagesQueryKey,
  useArchiveSkillPackageMutation,
  useSaveSkillPackageDraftMutation,
} from './queries'
import * as skillPackagesApi from './api/skill-packages'

vi.mock('./api/skill-packages', async () => {
  const actual = await vi.importActual<typeof import('./api/skill-packages')>('./api/skill-packages')
  return {
    ...actual,
    saveSkillPackageDraft: vi.fn(),
    archiveSkillPackage: vi.fn(),
    probeSkillAdminSurface: vi.fn(),
  }
})

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('skill package query keys and invalidation', () => {
  beforeEach(() => {
    vi.mocked(skillPackagesApi.saveSkillPackageDraft).mockReset()
    vi.mocked(skillPackagesApi.archiveSkillPackage).mockReset()
  })

  it('pins central query keys', () => {
    expect(skillAdminSurfaceQueryKey).toEqual(['skill-admin-surface'])
    expect(skillPackagesQueryKey({ limit: 50, offset: 0 })).toEqual([
      'skill-packages',
      { limit: 50, offset: 0 },
    ])
    expect(skillPackageQueryKey('pkg-1')).toEqual(['skill-package', 'pkg-1'])
  })

  it('invalidates package caches after draft save', async () => {
    vi.mocked(skillPackagesApi.saveSkillPackageDraft).mockResolvedValue({
      id: 'ver-2',
      skillPackageId: 'pkg-1',
      sequenceNo: 2,
      versionName: 'v2',
      versionSource: 'save',
      origin: 'api',
      contentDigest: 'c',
      skillMdDigest: 's',
      manifestDigest: 'm',
      resourceIndexDigest: 'r',
    })

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(skillPackageQueryKey('pkg-1'), { id: 'pkg-1' })
    queryClient.setQueryData(['skill-packages'], { items: [] })

    const { result } = renderHook(() => useSaveSkillPackageDraftMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        packageId: 'pkg-1',
        body: { skillMd: '# x', mindatlasYaml: null, resources: [], versionName: null },
      })
    })

    expect(queryClient.getQueryState(skillPackageQueryKey('pkg-1'))?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(['skill-packages'])?.isInvalidated).toBe(true)
  })

  it('invalidates package caches after archive', async () => {
    vi.mocked(skillPackagesApi.archiveSkillPackage).mockResolvedValue({
      id: 'pkg-1',
      canonicalName: 'demo',
      displayName: 'Demo',
      description: '',
      migrationState: 'native',
      catalogEnabled: false,
      isSystem: false,
      aggregateRevision: 4,
      aliases: [],
      archivedAt: '2026-07-20T00:00:00Z',
    })

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(skillPackageQueryKey('pkg-1'), { id: 'pkg-1' })

    const { result } = renderHook(() => useArchiveSkillPackageMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        packageId: 'pkg-1',
        body: { requestId: 'r1', expectedAggregateRevision: 3 },
      })
    })

    expect(queryClient.getQueryState(skillPackageQueryKey('pkg-1'))?.isInvalidated).toBe(true)
  })
})
