import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createAiProvider,
  deleteAiProvider,
  fetchModels as fetchModelsLegacy,
  fetchModelsById,
  listAiProviders,
  testAiProviderConnection,
  updateAiProvider,
  type AiProviderCreateRequest,
  type AiProviderUpdateRequest,
  type FetchModelsRequest,
} from './api/aiProviders'
import {
  createCredential,
  deleteCredential,
  discoverModelsByCredential,
  discoverModelsByKey,
  fetchCredentials,
  testCredentialConnection,
  updateCredential,
  type AiCredentialCreateRequest,
  type AiCredentialUpdateRequest,
} from './api/credentials'
import {
  createModel,
  deleteModel,
  fetchModels,
  updateModel,
  type AiModelCreateRequest,
  type AiModelType,
  type AiModelUpdateRequest,
} from './api/models'
import {
  fetchModelBindings,
  updateModelBindings,
  type UpdateModelBindingsRequest,
} from './api/bindings'

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

export function useTestAiProviderMutation() {
  return useMutation({
    mutationFn: (id: string) => testAiProviderConnection(id),
  })
}

export function useFetchModelsMutation() {
  return useMutation({
    mutationFn: (data: FetchModelsRequest) => fetchModelsLegacy(data),
  })
}

export function useFetchModelsByIdMutation() {
  return useMutation({
    mutationFn: (id: string) => fetchModelsById(id),
  })
}

// ==================== New AI Registry Hooks ====================

export const credentialsKeys = {
  all: ['ai-credentials'] as const,
  list: () => [...credentialsKeys.all, 'list'] as const,
}

export const modelsKeys = {
  all: ['ai-models'] as const,
  list: (params?: { credentialId?: string; modelType?: AiModelType }) =>
    [...modelsKeys.all, 'list', params] as const,
}

export const bindingsKeys = {
  all: ['model-bindings'] as const,
}

// Credentials
export function useCredentialsQuery() {
  return useQuery({
    queryKey: credentialsKeys.list(),
    queryFn: fetchCredentials,
  })
}

export function useCreateCredentialMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: AiCredentialCreateRequest) => createCredential(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: credentialsKeys.all }),
  })
}

export function useUpdateCredentialMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: AiCredentialUpdateRequest }) =>
      updateCredential(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: credentialsKeys.all }),
  })
}

export function useDeleteCredentialMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteCredential(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: credentialsKeys.all }),
  })
}

export function useTestCredentialMutation() {
  return useMutation({
    mutationFn: (id: string) => testCredentialConnection(id),
  })
}

export function useDiscoverModelsByCredentialMutation() {
  return useMutation({
    mutationFn: (id: string) => discoverModelsByCredential(id),
  })
}

export function useDiscoverModelsByKeyMutation() {
  return useMutation({
    mutationFn: ({ baseUrl, apiKey }: { baseUrl: string; apiKey: string }) =>
      discoverModelsByKey(baseUrl, apiKey),
  })
}

// Models
export function useModelsQuery(params?: { credentialId?: string; modelType?: AiModelType }) {
  return useQuery({
    queryKey: modelsKeys.list(params),
    queryFn: () => fetchModels(params),
  })
}

export function useCreateModelMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: AiModelCreateRequest) => createModel(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: modelsKeys.all }),
  })
}

export function useUpdateModelMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: AiModelUpdateRequest }) =>
      updateModel(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: modelsKeys.all }),
  })
}

export function useDeleteModelMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteModel(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: modelsKeys.all }),
  })
}

// Bindings
export function useModelBindingsQuery() {
  return useQuery({
    queryKey: bindingsKeys.all,
    queryFn: fetchModelBindings,
  })
}

export function useUpdateBindingsMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: UpdateModelBindingsRequest) => updateModelBindings(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: bindingsKeys.all }),
  })
}
