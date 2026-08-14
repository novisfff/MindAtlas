import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { listReconciliationCalls, reconcileCapabilityCall, type ReconcileInput } from './api/reconciliation'

export const reconciliationKeys = {
  all: ['reconciliation'] as const,
  list: () => [...reconciliationKeys.all, 'list'] as const,
}

export function useReconciliationQuery() {
  return useQuery({ queryKey: reconciliationKeys.list(), queryFn: listReconciliationCalls, refetchInterval: 15_000 })
}

export function useReconcileCapabilityCallMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ callId, input }: { callId: string; input: ReconcileInput }) => reconcileCapabilityCall(callId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: reconciliationKeys.list() })
      void queryClient.invalidateQueries({ queryKey: ['pre-ga-launch'] })
    },
  })
}
