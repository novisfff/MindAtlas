import type { PropsWithChildren } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useOperatorLoginMutation } from './queries'
import * as operatorAuthApi from './api/operatorAuth'

vi.mock('./api/operatorAuth', () => ({
  loginOperator: vi.fn(),
  getOperatorSession: vi.fn(),
  logoutOperator: vi.fn(),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function mutationVariablesStillHold(queryClient: QueryClient, secret: string): boolean {
  return queryClient.getMutationCache().getAll().some((entry) => {
    const variables = entry.state.variables
    return variables === secret || (typeof variables === 'string' && variables.includes(secret))
  })
}

describe('useOperatorLoginMutation secret cleanup', () => {
  beforeEach(() => {
    vi.mocked(operatorAuthApi.loginOperator).mockReset()
  })

  it('does not leave the password in mutation cache after success + reset', async () => {
    vi.mocked(operatorAuthApi.loginOperator).mockResolvedValue({
      authenticated: true,
      role: 'operator',
    })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { result } = renderHook(() => useOperatorLoginMutation(), {
      wrapper: createWrapper(queryClient),
    })

    const secret = 'login-success-secret-value'

    await act(async () => {
      await result.current.mutateAsync(secret)
    })

    act(() => {
      result.current.reset()
    })

    await waitFor(() => {
      expect(result.current.variables).toBeUndefined()
      expect(result.current.status).toBe('idle')
      expect(mutationVariablesStillHold(queryClient, secret)).toBe(false)
    })
  })

  it('does not leave the password in mutation cache after failure + reset', async () => {
    vi.mocked(operatorAuthApi.loginOperator).mockRejectedValue(new Error('login_failed'))

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const { result } = renderHook(() => useOperatorLoginMutation(), {
      wrapper: createWrapper(queryClient),
    })

    const secret = 'login-failure-secret-value'

    await act(async () => {
      await expect(result.current.mutateAsync(secret)).rejects.toThrow()
    })

    act(() => {
      result.current.reset()
    })

    await waitFor(() => {
      expect(result.current.variables).toBeUndefined()
      expect(result.current.error).toBeNull()
      expect(result.current.status).toBe('idle')
      expect(mutationVariablesStillHold(queryClient, secret)).toBe(false)
    })
  })
})
