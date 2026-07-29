import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  activateAssistantRollout,
  getAssistantReadinessDiagnostics,
  getPublicAssistantReadiness,
  listAssistantRollouts,
  prepareAssistantRollout,
  setAssistantNewRunsEnabled,
  type ActivateRolloutBody,
  type PrepareRolloutBody,
  type SetNewRunsBody,
} from './api/runtime'

export const assistantRuntimeKeys = {
  all: ['assistant-runtime'] as const,
  publicReadiness: () => [...assistantRuntimeKeys.all, 'public-readiness'] as const,
  diagnostics: () => [...assistantRuntimeKeys.all, 'diagnostics'] as const,
  rollouts: () => [...assistantRuntimeKeys.all, 'rollouts'] as const,
}

function invalidateRuntimeQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: assistantRuntimeKeys.publicReadiness() })
  void queryClient.invalidateQueries({ queryKey: assistantRuntimeKeys.diagnostics() })
  void queryClient.invalidateQueries({ queryKey: assistantRuntimeKeys.rollouts() })
}

export function usePublicAssistantReadinessQuery(enabled = true) {
  return useQuery({
    queryKey: assistantRuntimeKeys.publicReadiness(),
    queryFn: getPublicAssistantReadiness,
    enabled,
    refetchInterval: (query) => (query.state.data?.ready === false ? 2_000 : 15_000),
  })
}

export function useAssistantReadinessDiagnosticsQuery(enabled = true) {
  return useQuery({
    queryKey: assistantRuntimeKeys.diagnostics(),
    queryFn: getAssistantReadinessDiagnostics,
    enabled,
    refetchInterval: (query) => (query.state.data?.ready === false ? 2_000 : 15_000),
  })
}

export function useAssistantRolloutsQuery(enabled = true) {
  return useQuery({
    queryKey: assistantRuntimeKeys.rollouts(),
    queryFn: listAssistantRollouts,
    enabled,
  })
}

export function usePrepareAssistantRolloutMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: PrepareRolloutBody) => prepareAssistantRollout(body),
    onSuccess: () => invalidateRuntimeQueries(queryClient),
  })
}

export function useActivateAssistantRolloutMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      revisionId,
      body,
    }: {
      revisionId: string
      body: ActivateRolloutBody
    }) => activateAssistantRollout(revisionId, body),
    onSuccess: () => invalidateRuntimeQueries(queryClient),
  })
}

export function useSetAssistantNewRunsEnabledMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: SetNewRunsBody) => setAssistantNewRunsEnabled(body),
    onSuccess: () => invalidateRuntimeQueries(queryClient),
  })
}
