import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/lib/api/client'
import { OperatorLoginPage } from './OperatorLoginPage'
import * as operatorAuthApi from '../api/operatorAuth'

vi.mock('../api/operatorAuth', () => ({
  loginOperator: vi.fn(),
  getOperatorSession: vi.fn(),
  logoutOperator: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'operatorAuth.login.title': 'Operator login',
        'operatorAuth.login.description': 'Enter the operator password to continue.',
        'operatorAuth.login.passwordLabel': 'Password',
        'operatorAuth.login.submit': 'Login',
        'operatorAuth.login.submitting': 'Signing in…',
        'operatorAuth.login.errors.invalid': 'Invalid credentials.',
        'operatorAuth.login.errors.locked': 'Login is temporarily locked. Try again later.',
        'operatorAuth.login.errors.unavailable': 'Authentication is temporarily unavailable.',
        'operatorAuth.login.errors.generic': 'Unable to sign in. Please try again.',
      }
      return map[key] ?? key
    },
  }),
}))

vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))

const loginOperator = vi.mocked(operatorAuthApi.loginOperator)

function renderLogin(initialPath = '/login') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<OperatorLoginPage />} />
          <Route path="/dashboard" element={<div>dashboard-page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('OperatorLoginPage', () => {
  beforeEach(() => {
    loginOperator.mockReset()
  })

  it('does not trim the operator password', async () => {
    loginOperator.mockResolvedValue({
      authenticated: true,
      role: 'operator',
    })

    renderLogin()

    const passwordInput = screen.getByLabelText(/password/i)
    fireEvent.change(passwordInput, { target: { value: '  exact password  ' } })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(loginOperator).toHaveBeenCalledWith('  exact password  ')
    })
  })

  it('navigates to dashboard after successful login', async () => {
    loginOperator.mockResolvedValue({
      authenticated: true,
      role: 'operator',
    })

    renderLogin()

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'long-enough-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    expect(await screen.findByText('dashboard-page')).toBeInTheDocument()
  })

  it('shows lockout copy for locked login responses', async () => {
    loginOperator.mockRejectedValue(
      new ApiError({ message: 'login_locked', status: 429, code: 42910 }),
    )

    renderLogin()

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'long-enough-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    expect(
      await screen.findByText(/login is temporarily locked/i),
    ).toBeInTheDocument()
  })

  it('shows generic invalid credentials without echoing the secret', async () => {
    loginOperator.mockRejectedValue(
      new ApiError({ message: 'invalid_credentials', status: 401, code: 40111 }),
    )

    renderLogin()

    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: 'wrong-password-value' },
    })
    fireEvent.click(screen.getByRole('button', { name: /login/i }))

    expect(await screen.findByText(/invalid credentials/i)).toBeInTheDocument()
    expect(screen.queryByText(/wrong-password-value/i)).not.toBeInTheDocument()
  })
})
