import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { InitializationGate } from './InitializationGate'
import { useInitializationStatusQuery } from '../queries'

vi.mock('../queries', () => ({
  useInitializationStatusQuery: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

const mockedUseInitializationStatusQuery = vi.mocked(useInitializationStatusQuery)

function renderGate(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/initialize"
          element={(
            <InitializationGate>
              <div>initialize-page</div>
            </InitializationGate>
          )}
        />
        <Route
          path="/dashboard"
          element={(
            <InitializationGate>
              <div>dashboard-page</div>
            </InitializationGate>
          )}
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('InitializationGate', () => {
  beforeEach(() => {
    mockedUseInitializationStatusQuery.mockReset()
  })

  it('redirects non-initialized users to the initialization route', () => {
    mockedUseInitializationStatusQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { initialized: false },
      refetch: vi.fn(),
    } as never)

    renderGate('/dashboard')

    expect(screen.getByText('initialize-page')).toBeInTheDocument()
  })

  it('redirects initialized users away from the initialization route', () => {
    mockedUseInitializationStatusQuery.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { initialized: true },
      refetch: vi.fn(),
    } as never)

    renderGate('/initialize')

    expect(screen.getByText('dashboard-page')).toBeInTheDocument()
  })

  it('renders the loading state while status is pending on app routes', () => {
    mockedUseInitializationStatusQuery.mockReturnValue({
      isLoading: true,
      isError: false,
      data: undefined,
      refetch: vi.fn(),
    } as never)

    renderGate('/dashboard')

    expect(screen.getByText('initialization.loadingTitle')).toBeInTheDocument()
    expect(screen.getByText('initialization.loadingDescription')).toBeInTheDocument()
  })

  it('renders a status failure state with retry on app routes', () => {
    const refetch = vi.fn()
    mockedUseInitializationStatusQuery.mockReturnValue({
      isLoading: false,
      isError: true,
      data: undefined,
      refetch,
    } as never)

    renderGate('/dashboard')

    expect(screen.getByText('initialization.statusErrorTitle')).toBeInTheDocument()
    expect(screen.getByText('initialization.statusErrorDescription')).toBeInTheDocument()
    expect(screen.getByText('initialization.retry')).toBeInTheDocument()
  })

  it('allows the initialization route while status is still loading', () => {
    mockedUseInitializationStatusQuery.mockReturnValue({
      isLoading: true,
      isError: false,
      data: undefined,
      refetch: vi.fn(),
    } as never)

    renderGate('/initialize')

    expect(screen.getByText('initialize-page')).toBeInTheDocument()
  })
})
