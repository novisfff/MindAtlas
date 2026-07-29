import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  getOperatorSession,
  loginOperator,
  logoutOperator,
  type OperatorSession,
} from './api/operatorAuth'

export const operatorSessionKeys = {
  session: ['operator-session'] as const,
}

export function useOperatorSessionQuery(enabled = true) {
  return useQuery({
    queryKey: operatorSessionKeys.session,
    queryFn: getOperatorSession,
    enabled,
    retry: false,
    staleTime: 30_000,
  })
}

export function useOperatorLoginMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (password: string) => loginOperator(password),
    // Do not retain password-bearing mutation entries in the RQ cache after settle.
    gcTime: 0,
    onSuccess: (session: OperatorSession) => {
      queryClient.setQueryData(operatorSessionKeys.session, session)
    },
  })
}

export function useOperatorLogoutMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => logoutOperator(),
    onSuccess: () => {
      queryClient.setQueryData(operatorSessionKeys.session, { authenticated: false } satisfies OperatorSession)
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== operatorSessionKeys.session[0]
          && query.queryKey[0] !== 'system-initialization-status',
      })
    },
  })
}
