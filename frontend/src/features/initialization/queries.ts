import { useMutation, useQuery } from '@tanstack/react-query'
import {
  getInitializationDefaults,
  getInitializationStatus,
  initializeSystem,
  type InitializeSystemRequest,
} from './api/systemInitialization'
import type { Locale } from '@/stores/app-store'

export const initializationKeys = {
  status: ['system-initialization-status'] as const,
  defaults: (locale: Locale) => ['system-initialization-defaults', locale] as const,
}

export function useInitializationStatusQuery() {
  return useQuery({
    queryKey: initializationKeys.status,
    queryFn: getInitializationStatus,
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
    mutationFn: (payload: InitializeSystemRequest) => initializeSystem(payload),
  })
}
