import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  activateAiProvider,
  createAiProvider,
  deleteAiProvider,
  fetchModels,
  fetchModelsById,
  listAiProviders,
  testAiProviderConnection,
  updateAiProvider,
  type AiProviderCreateRequest,
  type AiProviderUpdateRequest,
  type FetchModelsRequest,
} from './api/aiProviders'

export const aiProvidersKeys = {
  all: ['ai-providers'] as const,
  list: () => [...aiProvidersKeys.all, 'list'] as const,
}

export function useAiProvidersQuery() {
  return useQuery({
    queryKey: aiProvidersKeys.list(),
    queryFn: listAiProviders,
  })
}

export function useCreateAiProviderMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: AiProviderCreateRequest) => createAiProvider(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiProvidersKeys.all }),
  })
}

export function useUpdateAiProviderMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: AiProviderUpdateRequest }) =>
      updateAiProvider(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiProvidersKeys.all }),
  })
}

export function useDeleteAiProviderMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteAiProvider(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiProvidersKeys.all }),
  })
}

export function useActivateAiProviderMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => activateAiProvider(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiProvidersKeys.all }),
  })
}

export function useTestAiProviderMutation() {
  return useMutation({
    mutationFn: (id: string) => testAiProviderConnection(id),
  })
}

export function useFetchModelsMutation() {
  return useMutation({
    mutationFn: (data: FetchModelsRequest) => fetchModels(data),
  })
}

export function useFetchModelsByIdMutation() {
  return useMutation({
    mutationFn: (id: string) => fetchModelsById(id),
  })
}
