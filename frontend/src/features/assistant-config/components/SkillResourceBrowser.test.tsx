import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import {
  hasExecuteControl,
  resourcePreviewMode,
  SkillResourceBrowser,
} from './SkillResourceBrowser'
import type { SkillResourceInput, SkillResourceMetadata } from '../api/skill-packages'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.resources': 'Resources',
        'settings.universalSkills.noDraftVersion': 'No draft version selected.',
        'settings.universalSkills.noResources': 'No resources on this version.',
        'settings.universalSkills.selectResource': 'Select a resource to preview.',
        'settings.universalSkills.scriptInertBadge': 'Stored as non-executable context resource',
        'settings.universalSkills.scriptInertHint':
          'Package scripts are view/export only. There is no run or terminal control.',
        'settings.universalSkills.resourceDownloadOnly':
          'This resource is download-only and is never injected into the page.',
        'settings.universalSkills.addResource': 'Add resource',
        'settings.universalSkills.replaceResource': 'Replace',
        'settings.universalSkills.removeResource': 'Remove resource',
        'settings.universalSkills.workingCopyResources': 'Working-copy resources',
        'common.download': 'Download',
        'common.remove': 'Remove',
        'messages.loading': 'Loading…',
        'messages.error': 'Error',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

function resource(
  partial: Partial<SkillResourceMetadata> &
    Pick<SkillResourceMetadata, 'path' | 'mediaType' | 'resourceKind'>,
): SkillResourceMetadata {
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

describe('SkillResourceBrowser working-copy mutations', () => {
  it('exposes add/replace/remove controls that update the working copy', () => {
    const workingCopy: SkillResourceInput[] = [
      { path: 'references/a.md', contentBase64: 'YQ==' },
      { path: 'references/b.md', contentBase64: 'Yg==' },
    ]
    const onUpsert = vi.fn()
    const onRemove = vi.fn()

    render(
      <SkillResourceBrowser
        packageId="pkg-1"
        versionId="ver-1"
        resources={[
          resource({ path: 'references/a.md', mediaType: 'text/markdown', resourceKind: 'references' }),
          resource({ path: 'references/b.md', mediaType: 'text/markdown', resourceKind: 'references' }),
        ]}
        workingCopyResources={workingCopy}
        editable
        onUpsertResource={onUpsert}
        onRemoveResource={onRemove}
      />,
    )

    expect(screen.getByRole('button', { name: 'Add resource' })).toBeVisible()
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove resource' })[0])
    expect(onRemove).toHaveBeenCalledWith('references/a.md')
  })
})
