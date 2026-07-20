import { describe, expect, it } from 'vitest'

import {
  hasExecuteControl,
  resourcePreviewMode,
} from './SkillResourceBrowser'
import type { SkillResourceMetadata } from '../api/skill-packages'

function resource(partial: Partial<SkillResourceMetadata> & Pick<SkillResourceMetadata, 'path' | 'mediaType' | 'resourceKind'>): SkillResourceMetadata {
  return {
    byteSize: 10,
    sha256: 'abc',
    ...partial,
  }
}

describe('SkillResourceBrowser safety', () => {
  it('never exposes an execute control', () => {
    expect(hasExecuteControl()).toBe(false)
  })

  it('forces download-only for HTML/SVG', () => {
    expect(
      resourcePreviewMode(
        resource({ path: 'assets/x.svg', mediaType: 'image/svg+xml', resourceKind: 'assets' }),
      ),
    ).toBe('download')
    expect(
      resourcePreviewMode(
        resource({ path: 'assets/x.html', mediaType: 'text/html', resourceKind: 'assets' }),
      ),
    ).toBe('download')
  })

  it('allows text preview for scripts without execution', () => {
    expect(
      resourcePreviewMode(
        resource({ path: 'scripts/helper.py', mediaType: 'text/x-python', resourceKind: 'scripts' }),
      ),
    ).toBe('text')
  })

  it('allows raster image preview', () => {
    expect(
      resourcePreviewMode(
        resource({ path: 'assets/logo.png', mediaType: 'image/png', resourceKind: 'assets' }),
      ),
    ).toBe('image')
  })
})
