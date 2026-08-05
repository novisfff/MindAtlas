import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, apiClient } from '@/lib/api/client'
import { initializeSystem, type InitializeSystemRequest } from './systemInitialization'

function ok<T>(data: T, status = 200): Response {
  return new Response(JSON.stringify({ code: 0, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const validPayload: InitializeSystemRequest = {
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

describe('initializeSystem setup credentials', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('sends setup token only in Authorization and never persists it', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      ok({ initialized: true, locale: 'en' }),
    )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    const postSpy = vi.spyOn(apiClient, 'post').mockImplementation((path, options) =>
      client.post(path, options),
    )

    await initializeSystem(validPayload, 'one-time-setup-token-with-32-bytes')

    expect(postSpy).toHaveBeenCalled()
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBe(
      'Setup one-time-setup-token-with-32-bytes',
    )
    expect(String(init.body)).not.toContain('one-time-setup-token-with-32-bytes')
    expect(String(init.body)).toContain('operatorPassword')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
