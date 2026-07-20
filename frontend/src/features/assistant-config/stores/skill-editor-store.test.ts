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

  it('marks dirty on edits and builds a complete save body', () => {
    useSkillEditorStore.getState().loadPackage(pkg, draft)
    useSkillEditorStore.getState().setSkillMd('# changed')
    useSkillEditorStore.getState().setMindatlasYaml('capabilities: []\n')
    const state = useSkillEditorStore.getState()
    expect(state.isDirty).toBe(true)
    expect(state.buildSaveBody()).toEqual({
      skillMd: '# changed',
      mindatlasYaml: 'capabilities: []\n',
      resources: [],
      versionName: 'v1',
    })
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
})
