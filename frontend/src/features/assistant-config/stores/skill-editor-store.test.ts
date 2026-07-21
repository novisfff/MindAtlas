import { beforeEach, describe, expect, it } from 'vitest'

import { useSkillEditorStore } from './skill-editor-store'
import type { SkillPackageDetail, SkillVersionDetail } from '../api/skill-packages'

const pkg: SkillPackageDetail = {
  id: 'pkg-1',
  canonicalName: 'demo-skill',
  displayName: 'Demo',
  description: 'A demo package',
  migrationState: 'native',
  catalogEnabled: false,
  isSystem: false,
  aggregateRevision: 3,
  aliases: [],
  draftVersion: {
    id: 'ver-1',
    skillPackageId: 'pkg-1',
    sequenceNo: 1,
    versionName: 'v1',
    versionSource: 'save',
    origin: 'api',
    contentDigest: 'c',
    skillMdDigest: 's',
    manifestDigest: 'm',
    resourceIndexDigest: 'r',
  },
}

const draft: SkillVersionDetail = {
  ...(pkg.draftVersion as NonNullable<typeof pkg.draftVersion>),
  frontmatter: { name: 'demo-skill' },
  resources: [],
  skillMd: '---\nname: demo-skill\n---\n\n# Demo\n',
  mindatlasYaml: 'capabilities:\n  - tool.search\n',
}

describe('skill-editor-store', () => {
  beforeEach(() => {
    useSkillEditorStore.getState().clear()
  })

  it('loads package into a clean working copy', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    const state = useSkillEditorStore.getState()
    expect(state.packageId).toBe('pkg-1')
    expect(state.draftVersionId).toBe('ver-1')
    expect(state.expectedAggregateRevision).toBe(3)
    expect(state.workingCopy.skillMd).toContain('demo-skill')
    expect(state.isDirty).toBe(false)
  })

  it('marks dirty on edits and always includes requestId + expected revision', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setSkillMd('# changed')
    useSkillEditorStore.getState().setMindatlasYaml('capabilities: []\n')
    const state = useSkillEditorStore.getState()
    expect(state.isDirty).toBe(true)
    const body = state.buildSaveBody()
    expect(body.skillMd).toBe('# changed')
    expect(body.mindatlasYaml).toBe('capabilities: []\n')
    expect(body.versionName).toBe('v1')
    expect(body.expectedAggregateRevision).toBe(3)
    expect(body.requestId).toBeTruthy()
    // Content-only edit preserves prior resources by omitting the field.
    expect(body.resources).toBeUndefined()
  })

  it('includes complete resource snapshot after explicit resource mutation', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setResources([
      { path: 'references/a.md', contentBase64: 'YQ==' },
    ])
    const body = useSkillEditorStore.getState().buildSaveBody()
    expect(body.resources).toEqual([{ path: 'references/a.md', contentBase64: 'YQ==' }])
    expect(body.requestId).toBeTruthy()
    expect(body.expectedAggregateRevision).toBe(3)
  })

  it('resource removal is serialized as an explicit replacement snapshot', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setResources([
      { path: 'a.txt', contentBase64: 'YQ==' },
      { path: 'b.txt', contentBase64: 'Yg==' },
    ])
    useSkillEditorStore.getState().removeResource('a.txt')
    const body = useSkillEditorStore.getState().buildSaveBody()
    expect(body.resources?.map((r) => r.path)).toEqual(['b.txt'])
    expect(body.requestId).toBeTruthy()
    expect(body.expectedAggregateRevision).toBe(3)
  })

  it('upsertResource replaces by path and preserves other resources', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setResources([
      { path: 'a.txt', contentBase64: 'YQ==' },
      { path: 'b.txt', contentBase64: 'Yg==' },
    ])
    useSkillEditorStore.getState().upsertResource({ path: 'a.txt', contentBase64: 'YQEy' })
    const body = useSkillEditorStore.getState().buildSaveBody()
    expect(body.resources).toEqual([
      { path: 'b.txt', contentBase64: 'Yg==' },
      { path: 'a.txt', contentBase64: 'YQEy' },
    ])
  })

  it('preserves local work on conflict and clears dirty on markSaved', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setSkillMd('# local')
    useSkillEditorStore.getState().setConflict({ message: 'stale revision', serverRevision: 4 })
    expect(useSkillEditorStore.getState().workingCopy.skillMd).toBe('# local')
    expect(useSkillEditorStore.getState().lastConflict?.message).toBe('stale revision')

    useSkillEditorStore.getState().markSaved({
      expectedAggregateRevision: 4,
      draftVersionId: 'ver-2',
      requestId: 'req-1',
    })
    const state = useSkillEditorStore.getState()
    expect(state.isDirty).toBe(false)
    expect(state.lastConflict).toBeNull()
    expect(state.expectedAggregateRevision).toBe(4)
    expect(state.draftVersionId).toBe('ver-2')
    expect(state.lastRequestId).toBe('req-1')
  })

  it('resetFromServer reloads server snapshot without destructive remote write', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setSkillMd('# dirty')
    useSkillEditorStore.getState().resetFromServer()
    expect(useSkillEditorStore.getState().isDirty).toBe(false)
    expect(useSkillEditorStore.getState().workingCopy.skillMd).toContain('demo-skill')
  })

  it('blocks resource mutation and empty-seed CAS until hydrate succeeds', () => {
    const draftWithResources: SkillVersionDetail = {
      ...draft,
      resources: [
        {
          path: 'references/a.md',
          resourceKind: 'references',
          mediaType: 'text/markdown',
          byteSize: 1,
          sha256: 'aa',
        },
        {
          path: 'references/b.md',
          resourceKind: 'references',
          mediaType: 'text/markdown',
          byteSize: 1,
          sha256: 'bb',
        },
      ],
    }
    useSkillEditorStore.getState().loadPackage(pkg, draftWithResources)
    const state = useSkillEditorStore.getState()
    expect(state.resourcesHydrated).toBe(false)
    expect(state.resourcesHydrationStatus).toBe('pending')
    expect(state.workingCopy.resources.map((r) => r.contentBase64)).toEqual(['', ''])

    // Mutations blocked before hydrate.
    state.removeResource('references/a.md')
    state.upsertResource({ path: 'references/c.md', contentBase64: 'Yw==' })
    expect(useSkillEditorStore.getState().resourcesDirty).toBe(false)
    expect(useSkillEditorStore.getState().workingCopy.resources.map((r) => r.path)).toEqual([
      'references/a.md',
      'references/b.md',
    ])

    // Empty placeholder hydrate fails closed (does not invent readiness).
    useSkillEditorStore.getState().hydrateResources([
      { path: 'references/a.md', contentBase64: '' },
      { path: 'references/b.md', contentBase64: 'Yg==' },
    ])
    expect(useSkillEditorStore.getState().resourcesHydrationStatus).toBe('error')
    expect(useSkillEditorStore.getState().resourcesHydrated).toBe(false)

    // Even if dirty were forced, buildSaveBody must refuse empty seeds.
    useSkillEditorStore.setState({
      resourcesDirty: true,
      resourcesHydrated: false,
      resourcesHydrationStatus: 'pending',
    })
    expect(() => useSkillEditorStore.getState().buildSaveBody()).toThrow(
      /before hydrate completes|empty contentBase64/i,
    )

    // Successful hydrate unlocks mutations; remove serializes remaining bytes only.
    useSkillEditorStore.setState({
      resourcesDirty: false,
      resourcesHydrated: false,
      resourcesHydrationStatus: 'pending',
      resourcesHydrationError: null,
    })
    useSkillEditorStore.getState().hydrateResources([
      { path: 'references/a.md', contentBase64: 'YQ==' },
      { path: 'references/b.md', contentBase64: 'Yg==' },
    ])
    expect(useSkillEditorStore.getState().resourcesHydrated).toBe(true)
    useSkillEditorStore.getState().removeResource('references/a.md')
    const body = useSkillEditorStore.getState().buildSaveBody()
    expect(body.resources).toEqual([{ path: 'references/b.md', contentBase64: 'Yg==' }])
  })

  it('failed hydrate does not invent empty bytes as ready', () => {
    const draftWithResources: SkillVersionDetail = {
      ...draft,
      resources: [
        {
          path: 'references/a.md',
          resourceKind: 'references',
          mediaType: 'text/markdown',
          byteSize: 1,
          sha256: 'aa',
        },
      ],
    }
    useSkillEditorStore.getState().loadPackage(pkg, draftWithResources)
    useSkillEditorStore.getState().setResourcesHydrationError('fetch failed')
    expect(useSkillEditorStore.getState().resourcesHydrationStatus).toBe('error')
    expect(useSkillEditorStore.getState().canMutateResources()).toBe(false)
    expect(() => {
      useSkillEditorStore.setState({ resourcesDirty: true })
      useSkillEditorStore.getState().buildSaveBody()
    }).toThrow()
  })
})
