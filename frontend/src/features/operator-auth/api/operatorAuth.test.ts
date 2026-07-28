import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiClient,
  SESSION_EXPIRED_EVENT,
  apiClient,
} from '@/lib/api/client'
import {
  getOperatorSession,
  loginOperator,
  logoutOperator,
  type OperatorSession,
} from './operatorAuth'

function ok<T>(data: T, status = 200): Response {
  return new Response(JSON.stringify({ code: 0, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function failed(status: number, message = 'Request failed'): Response {
  return new Response(JSON.stringify({ code: status, message, data: null }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function clearCookies(): void {
  for (const part of document.cookie.split(';')) {
    const name = part.split('=')[0]?.trim()
    if (name) {
      document.cookie = `${name}=; Path=/; Max-Age=0`
    }
  }
}

describe('ApiClient session + CSRF', () => {
  beforeEach(() => {
    clearCookies()
  })

  afterEach(() => {
    clearCookies()
  })

  it('sends cookies and csrf on unsafe requests', async () => {
    document.cookie = 'mindatlas_csrf=csrf-value; Path=/'
    const fetcher = vi.fn().mockResolvedValue(ok({ changed: true }))
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    await client.put('/api/system-settings/locale', { body: { locale: 'en' } })
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).get('X-MindAtlas-CSRF')).toBe('csrf-value')
  })

  it('sends credentials on safe methods without inventing CSRF', async () => {
    const fetcher = vi.fn().mockResolvedValue(ok({ items: [] }))
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    await client.get('/api/entries')
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).has('X-MindAtlas-CSRF')).toBe(false)
  })

  it('does not attach CSRF when the cookie is absent on login', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      ok({ authenticated: true, role: 'operator' } satisfies OperatorSession),
    )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    await client.post('/api/operator-auth/login', { body: { password: 'x'.repeat(12) } })
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).has('X-MindAtlas-CSRF')).toBe(false)
  })

  it('emits one session-expired event for protected 401', async () => {
    const listener = vi.fn()
    window.addEventListener(SESSION_EXPIRED_EVENT, listener)
    const client = new ApiClient({ fetcher: vi.fn().mockResolvedValue(failed(401)) })
    await expect(client.get('/api/entries')).rejects.toMatchObject({ status: 401 })
    expect(listener).toHaveBeenCalledTimes(1)
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener)
  })

  it('does not emit session-expired for login 401', async () => {
    const listener = vi.fn()
    window.addEventListener(SESSION_EXPIRED_EVENT, listener)
    const client = new ApiClient({ fetcher: vi.fn().mockResolvedValue(failed(401)) })
    await expect(
      client.post('/api/operator-auth/login', { body: { password: 'x'.repeat(12) } }),
    ).rejects.toMatchObject({ status: 401 })
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener)
  })

  it('does not emit session-expired for initialize 401', async () => {
    const listener = vi.fn()
    window.addEventListener(SESSION_EXPIRED_EVENT, listener)
    const client = new ApiClient({ fetcher: vi.fn().mockResolvedValue(failed(401)) })
    await expect(
      client.post('/api/system-settings/initialize', { body: {} }),
    ).rejects.toMatchObject({ status: 401 })
    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener(SESSION_EXPIRED_EVENT, listener)
  })
})

describe('operatorAuth typed helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('getOperatorSession calls the session probe', async () => {
    const session: OperatorSession = { authenticated: false }
    const spy = vi.spyOn(apiClient, 'get').mockResolvedValue(session)
    await expect(getOperatorSession()).resolves.toEqual(session)
    expect(spy).toHaveBeenCalledWith('/api/operator-auth/session')
  })

  it('loginOperator posts password body only', async () => {
    const session: OperatorSession = {
      authenticated: true,
      role: 'operator',
      idleExpiresAt: '2030-01-01T00:00:00Z',
      absoluteExpiresAt: '2030-01-08T00:00:00Z',
    }
    const spy = vi.spyOn(apiClient, 'post').mockResolvedValue(session)
    await expect(loginOperator('super-secret-pw')).resolves.toEqual(session)
    expect(spy).toHaveBeenCalledWith('/api/operator-auth/login', {
      body: { password: 'super-secret-pw' },
    })
  })

  it('logoutOperator posts empty body', async () => {
    const spy = vi.spyOn(apiClient, 'post').mockResolvedValue({ loggedOut: true as const })
    await expect(logoutOperator()).resolves.toEqual({ loggedOut: true })
    expect(spy).toHaveBeenCalledWith('/api/operator-auth/logout', { body: {} })
  })
})
