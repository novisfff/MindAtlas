import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiError, apiClient } from '@/lib/api/client'
import {
  activateAssistantRollout,
  getAssistantRolloutActivationReadiness,
  getPublicAssistantReadiness,
  prepareAssistantRollout,
  setAssistantNewRunsEnabled,
} from './runtime'

function ok<T>(data: T, status = 200): Response {
  return new Response(JSON.stringify({ code: 0, message: 'ok', data }), {
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

describe('status-aware public readiness', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns a typed public readiness body for HTTP 503', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      ok({ ready: false, reasonCodes: ['worker_unavailable'] }, 503),
    )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    vi.spyOn(apiClient, 'getAllowingStatuses').mockImplementation((path, statuses, options) =>
      client.getAllowingStatuses(path, statuses, options),
    )

    await expect(getPublicAssistantReadiness()).resolves.toEqual({
      ready: false,
      reasonCodes: ['worker_unavailable'],
    })
    expect(fetcher).toHaveBeenCalled()
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(String(fetcher.mock.calls[0][0])).toContain('/ready')
  })

  it('returns a typed public readiness body for HTTP 200', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      ok({ ready: true, reasonCodes: [] }, 200),
    )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    vi.spyOn(apiClient, 'getAllowingStatuses').mockImplementation((path, statuses, options) =>
      client.getAllowingStatuses(path, statuses, options),
    )

    await expect(getPublicAssistantReadiness()).resolves.toEqual({
      ready: true,
      reasonCodes: [],
    })
  })

  it('throws ApiError for non-allowlisted non-2xx statuses', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      ok({ ready: false, reasonCodes: ['worker_unavailable'] }, 502),
    )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })

    await expect(
      client.getAllowingStatuses('/ready', [503]),
    ).rejects.toBeInstanceOf(ApiError)
  })

  it('throws ApiError when accepted status has non-zero code', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 50300,
          message: 'not ready envelope',
          data: { ready: false, reasonCodes: ['worker_unavailable'] },
        }),
        { status: 503, headers: { 'content-type': 'application/json' } },
      ),
    )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })

    await expect(
      client.getAllowingStatuses('/ready', [503]),
    ).rejects.toMatchObject({ status: 503, code: 50300 })
  })
})

describe('activation credentialed client', () => {
  beforeEach(() => {
    clearCookies()
  })

  afterEach(() => {
    clearCookies()
    vi.restoreAllMocks()
  })

  it('sends activation through the cookie and csrf client', async () => {
    document.cookie = 'mindatlas_csrf=csrf-activate; Path=/'
    const resultBody = {
      activeRolloutRevisionId: 'revision-id',
      revisionLabel: 'prepared',
      revisionDigest: 'a'.repeat(64),
      controlRevision: 3,
      newRunsEnabled: true,
    }
    const fetcher = vi.fn().mockResolvedValue(ok(resultBody))
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    vi.spyOn(apiClient, 'post').mockImplementation((path, options) =>
      client.post(path, options),
    )

    const result = await activateAssistantRollout('revision-id', {
      expectedControlRevision: 2,
      requestId: '4f99cdf9-1952-4f2f-9558-cd56f89211af',
      reason: 'activate reviewed runtime',
    })

    expect(result.controlRevision).toBe(3)
    expect(result.activeRolloutRevisionId).toBe('revision-id')
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).get('X-MindAtlas-CSRF')).toBe('csrf-activate')
    expect(String(fetcher.mock.calls[0][0])).toContain(
      '/api/assistant-runtime/rollouts/revision-id/activate',
    )
    expect(JSON.parse(String(init.body))).toEqual({
      expectedControlRevision: 2,
      requestId: '4f99cdf9-1952-4f2f-9558-cd56f89211af',
      reason: 'activate reviewed runtime',
    })
  })

  it('fetches target-specific activation readiness without treating it as active state', async () => {
    const revisionId = 'prepared-revision-id'
    const resultBody = {
      rolloutRevisionId: revisionId,
      ready: true,
      reasonCodes: [],
      profileVersionId: 'profile-v',
      modelId: 'model-v',
      compatibleWorkerIds: ['worker-b'],
      buildRevision: 'build-b',
    }
    const fetcher = vi.fn().mockResolvedValue(ok(resultBody))
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    vi.spyOn(apiClient, 'get').mockImplementation((path, options) => client.get(path, options))

    await expect(getAssistantRolloutActivationReadiness(revisionId)).resolves.toEqual(resultBody)
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('same-origin')
    expect(String(fetcher.mock.calls[0][0])).toContain(
      `/api/assistant-runtime/rollouts/${revisionId}/activation-readiness`,
    )
  })

  it('sends prepare and new-runs through the credentialed client', async () => {
    document.cookie = 'mindatlas_csrf=csrf-mut; Path=/'
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        ok({
          rolloutRevisionId: 'prep-1',
          revisionLabel: 'r1',
          revisionDigest: 'b'.repeat(64),
          controlRevision: 0,
          activeRolloutRevisionId: null,
          newRunsEnabled: true,
        }),
      )
      .mockResolvedValueOnce(
        ok({
          activeRolloutRevisionId: 'prep-1',
          controlRevision: 1,
          newRunsEnabled: false,
        }),
      )
    const client = new ApiClient({ fetcher: fetcher as typeof fetch })
    vi.spyOn(apiClient, 'post').mockImplementation((path, options) =>
      client.post(path, options),
    )

    await prepareAssistantRollout({
      profileVersionId: 'profile-v',
      modelId: 'model-1',
      requestId: 'req-prep',
      reason: 'prepare',
    })
    await setAssistantNewRunsEnabled({
      enabled: false,
      expectedControlRevision: 0,
      requestId: 'req-switch',
      reason: 'pause',
    })

    expect(fetcher).toHaveBeenCalledTimes(2)
    for (const call of fetcher.mock.calls) {
      const init = call[1] as RequestInit
      expect(init.credentials).toBe('same-origin')
      expect(new Headers(init.headers).get('X-MindAtlas-CSRF')).toBe('csrf-mut')
    }
  })
})
