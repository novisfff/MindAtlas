/**
 * Assistant runtime control-plane + readiness clients (Plan 2 Task 11).
 * Cookie/CSRF principal only — no caller-selected identity/role headers.
 */
import { apiClient } from '@/lib/api/client'

export type AssistantReadinessReason =
  | 'system_not_initialized'
  | 'operator_missing'
  | 'operator_auth_unavailable'
  | 'system_seed_invalid'
  | 'profile_unpublished'
  | 'model_unbound'
  | 'rollout_inactive'
  | 'runtime_closure_drift'
  | 'worker_unavailable'
  | 'schema_incompatible'
  | 'new_runs_disabled'

export interface PublicAssistantReadiness {
  ready: boolean
  reasonCodes: AssistantReadinessReason[]
}

export interface AssistantReadinessDiagnostics extends PublicAssistantReadiness {
  activeRolloutRevisionId: string | null
  profileVersionId: string | null
  modelId: string | null
  compatibleWorkerIds: string[]
  buildRevision: string
}

export interface PreparedRolloutResult {
  rolloutRevisionId: string
  revisionLabel: string
  revisionDigest: string
  controlRevision: number
  activeRolloutRevisionId: string | null
  newRunsEnabled: boolean
}

export interface ActivatedRolloutResult {
  activeRolloutRevisionId: string
  revisionLabel: string
  revisionDigest: string
  controlRevision: number
  newRunsEnabled: boolean
}

export interface RuntimeControlResult {
  activeRolloutRevisionId: string | null
  controlRevision: number
  newRunsEnabled: boolean
}

export interface RolloutControlSummary {
  activeRolloutRevisionId: string | null
  controlRevision: number
  newRunsEnabled: boolean
}

export interface RolloutRevisionSummary {
  rolloutRevisionId: string
  revisionLabel: string
  revisionDigest: string
  profileVersionId: string
  modelId: string
  buildRevision: string
  preparedReason: string
  preparedByOperatorId: string | null
  createdAt: string | null
}

export interface AssistantRolloutsList {
  control: RolloutControlSummary
  revisions: RolloutRevisionSummary[]
}

export interface ActivateRolloutBody {
  expectedControlRevision: number
  requestId: string
  reason: string
}

export interface PrepareRolloutBody {
  profileVersionId: string
  modelId: string
  requestId: string
  reason: string
}

export interface SetNewRunsBody {
  enabled: boolean
  expectedControlRevision: number
  requestId: string
  reason: string
}

export async function getPublicAssistantReadiness(): Promise<PublicAssistantReadiness> {
  const response = await apiClient.getAllowingStatuses<PublicAssistantReadiness>(
    '/ready',
    [503],
  )
  return response.data
}

export function getAssistantReadinessDiagnostics() {
  return apiClient.get<AssistantReadinessDiagnostics>('/api/assistant-runtime/readiness')
}

export function listAssistantRollouts() {
  return apiClient.get<AssistantRolloutsList>('/api/assistant-runtime/rollouts')
}

export function prepareAssistantRollout(body: PrepareRolloutBody) {
  return apiClient.post<PreparedRolloutResult>('/api/assistant-runtime/rollouts/prepare', {
    body,
  })
}

export function activateAssistantRollout(revisionId: string, body: ActivateRolloutBody) {
  return apiClient.post<ActivatedRolloutResult>(
    `/api/assistant-runtime/rollouts/${revisionId}/activate`,
    { body },
  )
}

export function setAssistantNewRunsEnabled(body: SetNewRunsBody) {
  return apiClient.post<RuntimeControlResult>('/api/assistant-runtime/new-runs', { body })
}
