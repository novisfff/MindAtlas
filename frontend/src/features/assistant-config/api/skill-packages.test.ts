import { describe, expect, it } from 'vitest'

import {
  SKILL_ADMIN_BASE,
  SKILL_PACKAGES_BASE,
  isDangerousMarkupMediaType,
  isRasterImageMediaType,
  isScriptResourcePath,
  isTextPreviewMediaType,
  mapSkillPackageError,
  newRequestId,
} from './skill-packages'
import { ApiError } from '@/lib/api/client'

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
})
