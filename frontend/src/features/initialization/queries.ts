import { useMutation, useQuery } from '@tanstack/react-query'
import {
  getInitializationDefaults,
  getInitializationStatus,
  initializeSystem,
  type InitializeSystemRequest,
} from './api/systemInitialization'
import {
  getPersistedInitializationCheckedAt,
  getPersistedInitializationStatus,
  setPersistedInitializationStatus,
} from './store'
import type { Locale } from '@/stores/app-store'

export const initializationKeys = {
  status: ['system-initialization-status'] as const,
  defaults: (locale: Locale) => ['system-initialization-defaults', locale] as const,
}

export async function fetchInitializationStatus() {
  const response = await getInitializationStatus()
  setPersistedInitializationStatus(response)
  return response
}

export function useInitializationStatusQuery() {
  return useQuery({
    queryKey: initializationKeys.status,
    queryFn: fetchInitializationStatus,
    initialData: getPersistedInitializationStatus(),
    initialDataUpdatedAt: getPersistedInitializationCheckedAt(),
    staleTime: 0,
  })
}

export function useInitializationDefaultsQuery(locale: Locale) {
  return useQuery({
    queryKey: initializationKeys.defaults(locale),
    queryFn: () => getInitializationDefaults(locale),
  })
}

export function useInitializeSystemMutation() {
  return useMutation({
    mutationFn: ({
      payload,
      setupToken,
    }: {
      payload: InitializeSystemRequest
      setupToken: string
    }) => initializeSystem(payload, setupToken),
  })
}
