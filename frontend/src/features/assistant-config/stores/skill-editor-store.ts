/**
 * Working-copy store for Universal Skill package editor (Plan 09 Task 6).
 * Keyed by packageId + draftVersionId. Save submits a complete snapshot.
 * UI never invents authority — conflicts surface as 409 without overwrite.
 */
import { create } from 'zustand'

import type {
  SkillPackageDetail,
  SkillResourceInput,
  SkillVersionDetail,
} from '../api/skill-packages'

export interface SkillWorkingCopy {
  skillMd: string
  mindatlasYaml: string
  resources: SkillResourceInput[]
  versionName: string
  displayName: string
  description: string
}

export interface SkillEditorConflict {
  message: string
  serverRevision?: number
  details?: unknown
}

export interface SkillValidationDiagnostic {
  path: string
  message: string
  severity: 'error' | 'warning' | 'info'
}

interface SkillEditorState {
  packageId: string | null
  draftVersionId: string | null
  expectedAggregateRevision: number
  packageDetail: SkillPackageDetail | null
  draftDetail: SkillVersionDetail | null
  workingCopy: SkillWorkingCopy
  isDirty: boolean
  resourcesDirty: boolean
  lastRequestId: string | null
  lastConflict: SkillEditorConflict | null
  validationDiagnostics: SkillValidationDiagnostic[]

  loadPackage: (pkg: SkillPackageDetail, draft?: SkillVersionDetail | null) => void
  setSkillMd: (value: string) => void
  setMindatlasYaml: (value: string) => void
  setVersionName: (value: string) => void
  setDisplayName: (value: string) => void
  setDescription: (value: string) => void
  setResources: (resources: SkillResourceInput[]) => void
  upsertResource: (resource: SkillResourceInput) => void
  removeResource: (path: string) => void
  /** Seed resource bytes from server without marking dirty (pre-mutation hydrate). */
  hydrateResources: (resources: SkillResourceInput[]) => void
  setValidationDiagnostics: (items: SkillValidationDiagnostic[]) => void
  markSaved: (params: {
    packageDetail?: SkillPackageDetail | null
    draftVersionId?: string | null
    draftDetail?: SkillVersionDetail | null
    expectedAggregateRevision?: number
    requestId?: string | null
  }) => void
  setConflict: (conflict: SkillEditorConflict | null) => void
  setLastRequestId: (requestId: string | null) => void
  resetFromServer: () => void
  clear: () => void
  buildSaveBody: () => {
    skillMd: string
    mindatlasYaml: string | null
    resources?: SkillResourceInput[]
    versionName: string | null
    expectedAggregateRevision: number
    requestId: string
  }
}

const EMPTY_WORKING_COPY: SkillWorkingCopy = {
  skillMd: '',
  mindatlasYaml: '',
  resources: [],
  versionName: '',
  displayName: '',
  description: '',
}

/**
 * Hydrate working copy from server draft metadata.
 * Resource bytes are not inlined in version detail; paths seed the working-copy
 * list so explicit remove/replace can produce a complete CAS snapshot.
 * Content-only saves omit resources until the first mutation.
 */
function workingCopyFromServer(
  pkg: SkillPackageDetail,
  draft?: SkillVersionDetail | null,
): SkillWorkingCopy {
  const resourceSeeds: SkillResourceInput[] = (draft?.resources ?? []).map((r) => ({
    path: r.path,
    // Empty base64 marks "path known; bytes still on server until mutated".
    // buildSaveBody only sends resources after an explicit mutation.
    contentBase64: '',
  }))
  return {
    skillMd: draft?.skillMd ?? '',
    mindatlasYaml: draft?.mindatlasYaml ?? '',
    resources: resourceSeeds,
    versionName: draft?.versionName ?? '',
    displayName: pkg.displayName ?? '',
    description: pkg.description ?? '',
  }
}

export const useSkillEditorStore = create<SkillEditorState>()((set, get) => ({
  packageId: null,
  draftVersionId: null,
  expectedAggregateRevision: 0,
  packageDetail: null,
  draftDetail: null,
  workingCopy: { ...EMPTY_WORKING_COPY },
  isDirty: false,
      resourcesDirty: false,
  lastRequestId: null,
  lastConflict: null,
  validationDiagnostics: [],

  loadPackage: (pkg, draft = null) =>
    set({
      packageId: pkg.id,
      draftVersionId: draft?.id ?? pkg.draftVersion?.id ?? null,
      expectedAggregateRevision: pkg.aggregateRevision ?? 0,
      packageDetail: pkg,
      draftDetail: draft,
      workingCopy: workingCopyFromServer(pkg, draft),
      isDirty: false,
      resourcesDirty: false,
      lastConflict: null,
      validationDiagnostics: [],
    }),

  setSkillMd: (value) =>
    set((state) => ({
      workingCopy: { ...state.workingCopy, skillMd: value },
      isDirty: true,
      lastConflict: null,
    })),

  setMindatlasYaml: (value) =>
    set((state) => ({
      workingCopy: { ...state.workingCopy, mindatlasYaml: value },
      isDirty: true,
      lastConflict: null,
    })),

  setVersionName: (value) =>
    set((state) => ({
      workingCopy: { ...state.workingCopy, versionName: value },
      isDirty: true,
    })),

  setDisplayName: (value) =>
    set((state) => ({
      workingCopy: { ...state.workingCopy, displayName: value },
      isDirty: true,
    })),

  setDescription: (value) =>
    set((state) => ({
      workingCopy: { ...state.workingCopy, description: value },
      isDirty: true,
    })),

  setResources: (resources) =>
    set((state) => ({
      workingCopy: { ...state.workingCopy, resources: [...resources] },
      isDirty: true,
      resourcesDirty: true,
    })),

  upsertResource: (resource) =>
    set((state) => {
      const without = state.workingCopy.resources.filter((r) => r.path !== resource.path)
      return {
        workingCopy: { ...state.workingCopy, resources: [...without, resource] },
        isDirty: true,
        resourcesDirty: true,
      }
    }),

  removeResource: (path) =>
    set((state) => ({
      workingCopy: {
        ...state.workingCopy,
        resources: state.workingCopy.resources.filter((r) => r.path !== path),
      },
      isDirty: true,
      resourcesDirty: true,
    })),

  hydrateResources: (resources) =>
    set((state) => {
      // Never overwrite an in-progress working-copy mutation.
      if (state.resourcesDirty) return state
      return {
        workingCopy: { ...state.workingCopy, resources: [...resources] },
      }
    }),

  setValidationDiagnostics: (items) => set({ validationDiagnostics: items }),

  markSaved: ({ packageDetail, draftVersionId, draftDetail, expectedAggregateRevision, requestId }) =>
    set((state) => ({
      packageDetail: packageDetail ?? state.packageDetail,
      draftVersionId: draftVersionId ?? state.draftVersionId,
      draftDetail: draftDetail ?? state.draftDetail,
      expectedAggregateRevision:
        expectedAggregateRevision ?? state.expectedAggregateRevision,
      lastRequestId: requestId ?? state.lastRequestId,
      isDirty: false,
      resourcesDirty: false,
      lastConflict: null,
    })),

  setConflict: (conflict) => set({ lastConflict: conflict }),

  setLastRequestId: (requestId) => set({ lastRequestId: requestId }),

  resetFromServer: () => {
    const { packageDetail, draftDetail } = get()
    if (!packageDetail) return
    set({
      workingCopy: workingCopyFromServer(packageDetail, draftDetail),
      isDirty: false,
      resourcesDirty: false,
      lastConflict: null,
      expectedAggregateRevision: packageDetail.aggregateRevision ?? 0,
      draftVersionId: draftDetail?.id ?? packageDetail.draftVersion?.id ?? null,
    })
  },

  clear: () =>
    set({
      packageId: null,
      draftVersionId: null,
      expectedAggregateRevision: 0,
      packageDetail: null,
      draftDetail: null,
      workingCopy: { ...EMPTY_WORKING_COPY },
      isDirty: false,
      resourcesDirty: false,
      lastRequestId: null,
      lastConflict: null,
      validationDiagnostics: [],
    }),

  buildSaveBody: () => {
    const { workingCopy, resourcesDirty, expectedAggregateRevision, lastRequestId } = get()
    // Always include requestId + expected revision (mandatory CAS).
    // When resources were mutated, send the complete intended snapshot.
    // Content-only edits omit resources so the server preserves prior bytes.
    const body: {
      skillMd: string
      mindatlasYaml: string | null
      resources?: { path: string; contentBase64: string }[]
      versionName: string | null
      expectedAggregateRevision: number
      requestId: string
    } = {
      skillMd: workingCopy.skillMd,
      mindatlasYaml: workingCopy.mindatlasYaml.trim() ? workingCopy.mindatlasYaml : null,
      versionName: workingCopy.versionName.trim() ? workingCopy.versionName : null,
      expectedAggregateRevision,
      requestId:
        lastRequestId ||
        (typeof crypto !== 'undefined' && crypto.randomUUID
          ? `save-${crypto.randomUUID()}`
          : `save-${Date.now()}`),
    }
    if (resourcesDirty) {
      // Explicit complete replacement snapshot (add/replace/remove).
      body.resources = workingCopy.resources.map((r) => ({
        path: r.path,
        contentBase64: r.contentBase64,
      }))
    }
    return body
  },
}))
