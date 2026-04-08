import type { PluginConfig } from './config'
import { MindAtlasApiError } from './errors'

interface ApiEnvelope<T> {
  success?: boolean
  code?: number
  message?: string
  data?: T
  details?: unknown
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  path: string
  config: PluginConfig
  body?: unknown
  headers?: Record<string, string>
}

function createTimeoutSignal(timeoutMs: number): AbortSignal {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(new Error('Request timed out')), timeoutMs)
  timeout.unref?.()
  controller.signal.addEventListener('abort', () => clearTimeout(timeout), { once: true })
  return controller.signal
}

async function parseEnvelope<T>(response: Response): Promise<ApiEnvelope<T> | null> {
  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    return null
  }
  return (await response.json()) as ApiEnvelope<T>
}

export async function requestMindAtlas<T>({ method = 'GET', path, config, body, headers = {} }: RequestOptions): Promise<T> {
  const requestHeaders: Record<string, string> = {
    Authorization: `Bearer ${config.integrationSecret}`,
    ...headers,
  }

  const init: RequestInit = {
    method,
    headers: requestHeaders,
    signal: createTimeoutSignal(config.requestTimeoutMs),
  }

  if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(`${config.baseUrl}${path}`, init)
  } catch (error) {
    if (error instanceof Error && error.message) {
      throw new Error(`MindAtlas request failed: ${error.message}`)
    }
    throw new Error('MindAtlas request failed.')
  }

  const envelope = await parseEnvelope<T>(response)
  if (!response.ok) {
    if (envelope) {
      throw new MindAtlasApiError(envelope.message || `MindAtlas request failed with status ${response.status}.`, {
        statusCode: response.status,
        code: envelope.code,
        details: envelope.details,
      })
    }
    throw new MindAtlasApiError(`MindAtlas request failed with status ${response.status}.`, {
      statusCode: response.status,
    })
  }

  if (envelope && envelope.success === false) {
    throw new MindAtlasApiError(envelope.message || 'MindAtlas request failed.', {
      statusCode: response.status,
      code: envelope.code,
      details: envelope.details,
    })
  }

  if (envelope) {
    return envelope.data as T
  }

  throw new Error('MindAtlas returned a non-JSON response.')
}
