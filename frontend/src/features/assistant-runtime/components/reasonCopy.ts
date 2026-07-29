import type { AssistantReadinessReason } from '../api/runtime'

export const ASSISTANT_READINESS_REASONS: readonly AssistantReadinessReason[] = [
  'system_not_initialized',
  'operator_missing',
  'operator_auth_unavailable',
  'system_seed_invalid',
  'profile_unpublished',
  'model_unbound',
  'rollout_inactive',
  'runtime_closure_drift',
  'worker_unavailable',
  'schema_incompatible',
  'new_runs_disabled',
] as const

/** Integrity failures that must never offer a bypass. */
export const INTEGRITY_STOP_REASONS: ReadonlySet<AssistantReadinessReason> = new Set([
  'system_seed_invalid',
  'runtime_closure_drift',
  'schema_incompatible',
])

export function reasonTranslationKey(code: string): string {
  return `assistantRuntime.reasons.${code}`
}

export function isIntegrityStopReason(code: string): boolean {
  return INTEGRITY_STOP_REASONS.has(code as AssistantReadinessReason)
}
