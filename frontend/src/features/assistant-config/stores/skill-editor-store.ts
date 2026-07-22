/**
 * Working-copy store for Universal Skill package editor (Plan 09 Task 6).
 * Keyed by packageId + draftVersionId. Save submits a complete snapshot.
 * UI never invents authority — conflicts surface as 409 without overwrite.
 */
import { create } from 'zustand'

import { sanitizeMindatlasYamlCapabilities } from '../components/SkillCapabilityEditor'
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

/** Resource byte hydrate lifecycle for CAS-safe working-copy mutations. */
export type ResourcesHydrationStatus = 'idle' | 'pending' | 'ready' | 'error'

interface SkillEditorState {
  packageId: string | null
  draftVersionId: string | null
  expectedAggregateRevision: number
  packageDetail: SkillPackageDetail | null
  draftDetail: SkillVersionDetail | null
  workingCopy: SkillWorkingCopy
  isDirty: boolean
  resourcesDirty: boolean
  /**
   * True only after every server resource path has non-empty bytes hydrated,
   * or when the draft has no resources (nothing to hydrate).
   * Resource mutations and resource CAS snapshots are blocked until ready.
   */
  resourcesHydrated: boolean
  resourcesHydrationStatus: ResourcesHydrationStatus
  resourcesHydrationError: string | null
  /**
   * Published Capability Registry identity keys.
   * `null` = registry not loaded yet (do not strip on save).
   * `string[]` (possibly empty) = registry loaded; save filters capabilities to this set.
   */
  capabilityRegistryKeys: string[] | null
  lastRequestId: string | null
  lastConflict: SkillEditorConflict | null
  validationDiagnostics: SkillValidationDiagnostic[]

  loadPackage: (pkg: SkillPackageDetail, draft?: SkillVersionDetail | null) => void
  setSkillMd: (value: string) => void
  setMindatlasYaml: (value: string) => void
  /**
   * Publish Registry identity keys used to sanitize capabilities on save.
   * Pass an array (including empty) after a load attempt; null resets to not-loaded.
   */
  setCapabilityRegistryKeys: (keys: string[] | null) => void
  setVersionName: (value: string) => void
  setDisplayName: (value: string) => void
  setDescription: (value: string) => void
  setResources: (resources: SkillResourceInput[]) => void
  upsertResource: (resource: SkillResourceInput) => void
  removeResource: (path: string) => void
  /**
   * Seed resource bytes from server without marking dirty (pre-mutation hydrate).
   * Rejects incomplete snapshots (missing paths or empty contentBase64).
   */
  hydrateResources: (resources: SkillResourceInput[]) => void
  /** Mark hydrate failed; blocks resource mutations/saves until reload. */
  setResourcesHydrationError: (message: string) => void
  /** True when resource working-copy mutations are allowed. */
  canMutateResources: () => boolean
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
 * list as placeholders until hydrateResources fills real base64.
 * Placeholders must never be sent as a CAS replacement snapshot.
 */
function workingCopyFromServer(
  pkg: SkillPackageDetail,
  draft?: SkillVersionDetail | null,
): SkillWorkingCopy {
  const resourceSeeds: SkillResourceInput[] = (draft?.resources ?? []).map((r) => ({
    path: r.path,
    // Empty base64 marks "path known; bytes still on server until hydrated".
    // buildSaveBody only sends resources after an explicit mutation AND hydrate.
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

function initialHydrationState(draft?: SkillVersionDetail | null): {
  resourcesHydrated: boolean
  resourcesHydrationStatus: ResourcesHydrationStatus
  resourcesHydrationError: string | null
} {
  const hasServerResources = (draft?.resources?.length ?? 0) > 0
  if (!hasServerResources) {
    return {
      resourcesHydrated: true,
      resourcesHydrationStatus: 'ready',
      resourcesHydrationError: null,
    }
  }
  return {
    resourcesHydrated: false,
    resourcesHydrationStatus: 'pending',
    resourcesHydrationError: null,
  }
}

function hasEmptyResourceBytes(resources: SkillResourceInput[]): boolean {
  return resources.some((r) => !r.contentBase64 || r.contentBase64.length === 0)
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
  resourcesHydrated: true,
  resourcesHydrationStatus: 'idle',
  resourcesHydrationError: null,
  capabilityRegistryKeys: null,
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
      ...initialHydrationState(draft),
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

  setCapabilityRegistryKeys: (keys) =>
    set({
      capabilityRegistryKeys: keys === null ? null : keys.map((k) => k.trim()).filter(Boolean),
    }),

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

  canMutateResources: () => {
    const state = get()
    return state.resourcesHydrated && state.resourcesHydrationStatus === 'ready'
  },

  setResources: (resources) =>
    set((state) => {
      if (!state.resourcesHydrated || state.resourcesHydrationStatus !== 'ready') {
        return state
      }
      if (hasEmptyResourceBytes(resources)) {
        return state
      }
      return {
        workingCopy: { ...state.workingCopy, resources: [...resources] },
        isDirty: true,
        resourcesDirty: true,
      }
    }),

  upsertResource: (resource) =>
    set((state) => {
      if (!state.resourcesHydrated || state.resourcesHydrationStatus !== 'ready') {
        return state
      }
      if (!resource.contentBase64 || resource.contentBase64.length === 0) {
        return state
      }
      const without = state.workingCopy.resources.filter((r) => r.path !== resource.path)
      return {
        workingCopy: { ...state.workingCopy, resources: [...without, resource] },
        isDirty: true,
        resourcesDirty: true,
      }
    }),

  removeResource: (path) =>
    set((state) => {
      if (!state.resourcesHydrated || state.resourcesHydrationStatus !== 'ready') {
        return state
      }
      return {
        workingCopy: {
          ...state.workingCopy,
          resources: state.workingCopy.resources.filter((r) => r.path !== path),
        },
        isDirty: true,
        resourcesDirty: true,
      }
    }),

  hydrateResources: (resources) =>
    set((state) => {
      // Never overwrite an in-progress working-copy mutation.
      if (state.resourcesDirty) return state
      // Incomplete hydrate (empty placeholder base64) must fail closed.
      if (hasEmptyResourceBytes(resources)) {
        return {
          resourcesHydrated: false,
          resourcesHydrationStatus: 'error',
          resourcesHydrationError:
            'Resource hydrate incomplete: empty contentBase64 is not allowed',
        }
      }
      // If server paths exist, require every path to be present in the hydrate payload.
      const serverPaths = (state.draftDetail?.resources ?? []).map((r) => r.path)
      if (serverPaths.length > 0) {
        const hydratedPaths = new Set(resources.map((r) => r.path))
        const missing = serverPaths.filter((p) => !hydratedPaths.has(p))
        if (missing.length > 0) {
          return {
            resourcesHydrated: false,
            resourcesHydrationStatus: 'error',
            resourcesHydrationError: `Resource hydrate incomplete: missing ${missing.join(', ')}`,
          }
        }
      }
      return {
        workingCopy: { ...state.workingCopy, resources: [...resources] },
        resourcesHydrated: true,
        resourcesHydrationStatus: 'ready',
        resourcesHydrationError: null,
      }
    }),

  setResourcesHydrationError: (message) =>
    set({
      resourcesHydrated: false,
      resourcesHydrationStatus: 'error',
      resourcesHydrationError: message,
    }),

  setValidationDiagnostics: (items) => set({ validationDiagnostics: items }),

  markSaved: ({ packageDetail, draftVersionId, draftDetail, expectedAggregateRevision, requestId }) =>
    set((state) => {
      const nextDraft = draftDetail ?? state.draftDetail
      return {
        packageDetail: packageDetail ?? state.packageDetail,
        draftVersionId: draftVersionId ?? state.draftVersionId,
        draftDetail: nextDraft,
        expectedAggregateRevision:
          expectedAggregateRevision ?? state.expectedAggregateRevision,
        lastRequestId: requestId ?? state.lastRequestId,
        isDirty: false,
        resourcesDirty: false,
        // After a resource-mutating save the server is authoritative again;
        // re-hydrate if the next draft still lists resources.
        ...initialHydrationState(nextDraft),
        lastConflict: null,
      }
    }),

  setConflict: (conflict) => set({ lastConflict: conflict }),

  setLastRequestId: (requestId) => set({ lastRequestId: requestId }),

  resetFromServer: () => {
    const { packageDetail, draftDetail } = get()
    if (!packageDetail) return
    set({
      workingCopy: workingCopyFromServer(packageDetail, draftDetail),
      isDirty: false,
      resourcesDirty: false,
      ...initialHydrationState(draftDetail),
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
      resourcesHydrated: true,
      resourcesHydrationStatus: 'idle',
      resourcesHydrationError: null,
      capabilityRegistryKeys: null,
      lastRequestId: null,
      lastConflict: null,
      validationDiagnostics: [],
    }),

  buildSaveBody: () => {
    const {
      workingCopy,
      resourcesDirty,
      resourcesHydrated,
      resourcesHydrationStatus,
      capabilityRegistryKeys,
      expectedAggregateRevision,
      lastRequestId,
    } = get()
    // Always include requestId + expected revision (mandatory CAS).
    // When resources were mutated, send the complete intended snapshot.
    // Content-only edits omit resources so the server preserves prior bytes.
    // Registry-only: once Registry is loaded, drop free-text capability keys.
    // While null (not loaded), leave YAML as-is — UI freezes capabilities on free-text edits.
    const rawYaml = workingCopy.mindatlasYaml
    const sanitizedYaml =
      rawYaml.trim() && capabilityRegistryKeys !== null
        ? sanitizeMindatlasYamlCapabilities(rawYaml, capabilityRegistryKeys)
        : rawYaml
    const body: {
      skillMd: string
      mindatlasYaml: string | null
      resources?: { path: string; contentBase64: string }[]
      versionName: string | null
      expectedAggregateRevision: number
      requestId: string
    } = {
      skillMd: workingCopy.skillMd,
      mindatlasYaml: sanitizedYaml.trim() ? sanitizedYaml : null,
      versionName: workingCopy.versionName.trim() ? workingCopy.versionName : null,
      expectedAggregateRevision,
      requestId:
        lastRequestId ||
        (typeof crypto !== 'undefined' && crypto.randomUUID
          ? `save-${crypto.randomUUID()}`
          : `save-${Date.now()}`),
    }
    if (resourcesDirty) {
      // Never ship empty placeholder base64 — that would wipe sibling bytes.
      if (!resourcesHydrated || resourcesHydrationStatus !== 'ready') {
        throw new Error(
          'Cannot save resource snapshot before hydrate completes for all existing paths',
        )
      }
      if (hasEmptyResourceBytes(workingCopy.resources)) {
        throw new Error(
          'Cannot save resource snapshot containing empty contentBase64 placeholders',
        )
      }
      // Explicit complete replacement snapshot (add/replace/remove).
      body.resources = workingCopy.resources.map((r) => ({
        path: r.path,
        contentBase64: r.contentBase64,
      }))
    }
    return body
  },
}))
