import type { PropsWithChildren } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useInitializeSystemMutation } from './queries'
import * as systemInitializationApi from './api/systemInitialization'
import type { InitializeSystemRequest } from './api/systemInitialization'

vi.mock('./api/systemInitialization', () => ({
  initializeSystem: vi.fn(),
  getInitializationStatus: vi.fn(),
  getInitializationDefaults: vi.fn(),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

const payload: InitializeSystemRequest = {
  locale: 'en',
  operatorPassword: 'exact-operator-password',
  aiCredential: {
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    apiKey: 'sk-test',
  },
  llmModel: {
    name: 'gpt-4.1-mini',
  },
  entryTypes: [
    {
      name: 'Note',
      graphEnabled: true,
      aiEnabled: true,
      enabled: true,
      origin: 'custom',
    },
  ],
}

function mutationHoldsSecrets(
  queryClient: QueryClient,
  setupToken: string,
  operatorPassword: string,
): boolean {
  return queryClient.getMutationCache().getAll().some((entry) => {
    const variables = entry.state.variables as
      | { setupToken?: string; payload?: { operatorPassword?: string } }
      | undefined
    if (!variables || typeof variables !== 'object') return false
    return (
      variables.setupToken === setupToken ||
      variables.payload?.operatorPassword === operatorPassword
    )
  })
}

describe('useInitializeSystemMutation secret cleanup', () => {
  beforeEach(() => {
    vi.mocked(systemInitializationApi.initializeSystem).mockReset()
  })

  it('does not leave setup token or operator password in mutation cache after success + reset', async () => {
    vi.mocked(systemInitializationApi.initializeSystem).mockResolvedValue({
      initialized: true,
      locale: 'en',
    })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { result } = renderHook(() => useInitializeSystemMutation(), {
      wrapper: createWrapper(queryClient),
    })

    const setupToken = 'one-time-setup-token-with-32-bytes!!'
    const secretVariables = { payload, setupToken }

    await act(async () => {
      await result.current.mutateAsync(secretVariables)
    })

    act(() => {
      result.current.reset()
    })

    await waitFor(() => {
      expect(result.current.variables).toBeUndefined()
      expect(result.current.status).toBe('idle')
      expect(mutationHoldsSecrets(queryClient, setupToken, payload.operatorPassword)).toBe(false)
    })
  })

  it('does not leave setup token or operator password in mutation cache after failure + reset', async () => {
    vi.mocked(systemInitializationApi.initializeSystem).mockRejectedValue(
      new Error('initialization_failed'),
    )

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { result } = renderHook(() => useInitializeSystemMutation(), {
      wrapper: createWrapper(queryClient),
    })

    const setupToken = 'one-time-setup-token-with-32-bytes!!'
    const secretVariables = { payload, setupToken }

    await act(async () => {
      await expect(result.current.mutateAsync(secretVariables)).rejects.toThrow()
    })

    act(() => {
      result.current.reset()
    })

    await waitFor(() => {
      expect(result.current.variables).toBeUndefined()
      expect(result.current.error).toBeNull()
      expect(result.current.status).toBe('idle')
      expect(mutationHoldsSecrets(queryClient, setupToken, payload.operatorPassword)).toBe(false)
    })
  })
})
