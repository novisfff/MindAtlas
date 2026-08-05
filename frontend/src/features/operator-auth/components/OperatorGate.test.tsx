import { act, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SESSION_EXPIRED_EVENT } from '@/lib/api/client'
import { OperatorGate } from './OperatorGate'
import * as operatorAuthApi from '../api/operatorAuth'
import { useInitializationStatusQuery } from '@/features/initialization/queries'

vi.mock('@/features/initialization/queries', () => ({
  useInitializationStatusQuery: vi.fn(),
  initializationKeys: {
    status: ['system-initialization-status'],
  },
}))

vi.mock('../api/operatorAuth', () => ({
  getOperatorSession: vi.fn(),
  loginOperator: vi.fn(),
  logoutOperator: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'operatorAuth.gate.loadingTitle': 'Checking operator session',
        'operatorAuth.gate.loadingDescription': 'Confirming your signed-in state.',
        'operatorAuth.gate.sessionExpired': 'Your session expired. Please sign in again.',
        'operatorAuth.login.title': 'Operator login',
      }
      return map[key] ?? key
    },
  }),
}))

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
  },
}))

const mockedInitStatus = vi.mocked(useInitializationStatusQuery)
const getOperatorSession = vi.mocked(operatorAuthApi.getOperatorSession)

function renderApp(options: {
  initialized: boolean
  authenticated: boolean
  path: string
  sessionPending?: boolean
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

  mockedInitStatus.mockReturnValue({
    isLoading: false,
    isError: false,
    data: { initialized: options.initialized, locale: 'en' },
    refetch: vi.fn(),
  } as never)

  if (options.sessionPending) {
    getOperatorSession.mockImplementation(() => new Promise(() => {}))
  } else {
    getOperatorSession.mockResolvedValue({
      authenticated: options.authenticated,
      role: options.authenticated ? 'operator' : undefined,
    })
  }

  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[options.path]}>
          <OperatorGate>
            <Routes>
              <Route path="/login" element={<h1>Operator login</h1>} />
              <Route path="/dashboard" element={<div>dashboard-page</div>} />
              <Route path="/initialize" element={<div>initialize-page</div>} />
              <Route path="/entries" element={<div>entries-page</div>} />
            </Routes>
          </OperatorGate>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  }
}

describe('OperatorGate', () => {
  beforeEach(() => {
    mockedInitStatus.mockReset()
    getOperatorSession.mockReset()
  })

  it('redirects initialized unauthenticated users to login', async () => {
    renderApp({ initialized: true, authenticated: false, path: '/dashboard' })
    expect(await screen.findByRole('heading', { name: /operator login/i })).toBeVisible()
  })

  it('allows authenticated users onto protected routes', async () => {
    renderApp({ initialized: true, authenticated: true, path: '/dashboard' })
    expect(await screen.findByText('dashboard-page')).toBeInTheDocument()
  })

  it('redirects authenticated users away from login', async () => {
    renderApp({ initialized: true, authenticated: true, path: '/login' })
    expect(await screen.findByText('dashboard-page')).toBeInTheDocument()
  })

  it('bypasses auth while the system is uninitialized', async () => {
    renderApp({ initialized: false, authenticated: false, path: '/initialize' })
    expect(await screen.findByText('initialize-page')).toBeInTheDocument()
  })

  it('bypasses auth on the initialization route even when initialized flag races', async () => {
    renderApp({ initialized: true, authenticated: false, path: '/initialize' })
    expect(await screen.findByText('initialize-page')).toBeInTheDocument()
  })

  it('shows loading state while the session probe is pending', async () => {
    renderApp({
      initialized: true,
      authenticated: false,
      path: '/dashboard',
      sessionPending: true,
    })
    expect(await screen.findByText('Checking operator session')).toBeInTheDocument()
  })

  it('handles session expiry on a protected page without a redirect loop', async () => {
    const { queryClient } = renderApp({
      initialized: true,
      authenticated: true,
      path: '/entries',
    })

    expect(await screen.findByText('entries-page')).toBeInTheDocument()

    queryClient.setQueryData(['entries'], [{ id: '1' }])
    const removeSpy = vi.spyOn(queryClient, 'removeQueries')
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    // After expiry the session probe must report unauthenticated to avoid a login↔dashboard loop.
    getOperatorSession.mockResolvedValue({ authenticated: false })

    await act(async () => {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    })

    expect(await screen.findByRole('heading', { name: /operator login/i })).toBeVisible()
    expect(removeSpy).toHaveBeenCalled()
    expect(invalidateSpy).toHaveBeenCalled()

    await act(async () => {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    })

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /operator login/i })).toBeVisible()
    })
    expect(screen.queryByText('entries-page')).not.toBeInTheDocument()
    expect(screen.queryByText('dashboard-page')).not.toBeInTheDocument()
  })
})
