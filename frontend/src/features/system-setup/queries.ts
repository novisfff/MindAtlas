import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getRuntimeConfig,
  updateRuntimeConfig,
  validateRuntimeConfig,
  type RuntimeConfigGroupKey,
  type RuntimeConfigRequestByGroup,
} from './api/runtime-config'

export const runtimeConfigKeys = {
  all: ['system-runtime-config'] as const,
}

export function useRuntimeConfigQuery() {
  return useQuery({
    queryKey: runtimeConfigKeys.all,
    queryFn: getRuntimeConfig,
  })
}

export function useUpdateRuntimeConfigMutation<GroupKey extends RuntimeConfigGroupKey>(groupKey: GroupKey) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: RuntimeConfigRequestByGroup[GroupKey]) => updateRuntimeConfig(groupKey, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: runtimeConfigKeys.all })
    },
  })
}

export function useValidateRuntimeConfigMutation<
  GroupKey extends Extract<RuntimeConfigGroupKey, 'storage' | 'knowledge_graph'>
>(groupKey: GroupKey) {
  return useMutation({
    mutationFn: (payload: RuntimeConfigRequestByGroup[GroupKey]) => validateRuntimeConfig(groupKey, payload),
  })
}
