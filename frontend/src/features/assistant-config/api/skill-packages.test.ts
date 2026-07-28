import { describe, expect, it, vi } from 'vitest'

import {
  SKILL_ADMIN_BASE,
  SKILL_PACKAGES_BASE,
  archiveSkillPackage,
  isDangerousMarkupMediaType,
  isRasterImageMediaType,
  isScriptResourcePath,
  isTextPreviewMediaType,
  mapSkillPackageError,
  newRequestId,
} from './skill-packages'
import { ApiError, apiClient } from '@/lib/api/client'

describe('skill-packages API contract', () => {
  it('pins Plan 01 and Plan 09 path prefixes', () => {
    expect(SKILL_PACKAGES_BASE).toBe('/api/assistant-config/skill-packages')
    expect(SKILL_ADMIN_BASE).toBe('/api/assistant-config/skill-admin')
  })

  it('generates non-empty request ids', () => {
    const id = newRequestId('test')
    expect(id.startsWith('test-')).toBe(true)
    expect(id.length).toBeGreaterThan(8)
  })

  it('maps conflict/auth/not_found errors', () => {
    expect(
      mapSkillPackageError(new ApiError({ message: 'conflict', status: 409, code: 40994 })).kind,
    ).toBe('conflict')
    expect(mapSkillPackageError(new ApiError({ message: 'nope', status: 401 })).kind).toBe('auth')
    expect(mapSkillPackageError(new ApiError({ message: 'missing', status: 404 })).kind).toBe(
      'not_found',
    )
  })

  it('classifies resource preview safety helpers', () => {
    expect(isScriptResourcePath('scripts/run.sh')).toBe(true)
    expect(isScriptResourcePath('references/a.md')).toBe(false)
    expect(isTextPreviewMediaType('text/plain')).toBe(true)
    expect(isRasterImageMediaType('image/png')).toBe(true)
    expect(isDangerousMarkupMediaType('image/svg+xml')).toBe(true)
    expect(isDangerousMarkupMediaType('text/html')).toBe(true)
  })

  it('does not generate X-MindAtlas-Operator-* headers on admin mutations', async () => {
    const spy = vi.spyOn(apiClient, 'post').mockResolvedValue({
      id: 'pkg-1',
      canonicalName: 'demo',
      displayName: 'Demo',
      description: '',
      migrationState: 'native',
      catalogEnabled: false,
      isSystem: false,
      aggregateRevision: 2,
    })

    await archiveSkillPackage('pkg-1', {
      requestId: 'req-1',
      expectedAggregateRevision: 1,
    })

    expect(spy).toHaveBeenCalledTimes(1)
    const options = spy.mock.calls[0][1] as { headers?: HeadersInit } | undefined
    const headers = new Headers(options?.headers)
    for (const key of headers.keys()) {
      expect(key.toLowerCase().startsWith('x-mindatlas-operator-')).toBe(false)
    }
    expect(headers.has('X-MindAtlas-Operator-Id')).toBe(false)
    expect(headers.has('X-MindAtlas-Operator-Role')).toBe(false)
    spy.mockRestore()
  })
})
