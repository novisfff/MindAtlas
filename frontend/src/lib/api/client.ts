import { withMindAtlasLocale } from './locale'

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export type QueryParamValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryParamValue | QueryParamValue[]>

export const SESSION_EXPIRED_EVENT = 'mindatlas:session-expired'

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])
const CSRF_HEADER = 'X-MindAtlas-CSRF'
const CSRF_COOKIE = 'mindatlas_csrf'

export class ApiError extends Error {
  readonly status?: number
  readonly code?: number
  readonly url?: string
  readonly details?: unknown

  constructor(params: {
    message: string
    status?: number
    code?: number
    url?: string
    details?: unknown
  }) {
    super(params.message)
    this.name = 'ApiError'
    this.status = params.status
    this.code = params.code
    this.url = params.url
    this.details = params.details
    Object.setPrototypeOf(this, ApiError.prototype)
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

function isApiResponse(value: unknown): value is ApiResponse<unknown> {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.code === 'number' &&
    typeof v.message === 'string' &&
    Object.prototype.hasOwnProperty.call(v, 'data')
  )
}

function parseMaybeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function buildQueryString(query?: QueryParams): string {
  if (!query) return ''
  const params = new URLSearchParams()

  for (const [key, raw] of Object.entries(query)) {
    if (raw == null) continue

    const values = Array.isArray(raw) ? raw : [raw]
    for (const v of values) {
      if (v == null) continue
      params.append(key, String(v))
    }
  }

  const s = params.toString()
  return s ? `?${s}` : ''
}

function joinUrl(baseUrl: string, path: string): string {
  if (!baseUrl) return path
  if (/^https?:\/\//i.test(path)) return path

  const left = baseUrl.replace(/\/+$/, '')
  const right = path.startsWith('/') ? path : `/${path}`
  return `${left}${right}`
}

function isFormData(value: unknown): value is FormData {
  return typeof FormData !== 'undefined' && value instanceof FormData
}

function readCookie(name: string): string | undefined {
  if (typeof document === 'undefined') return undefined
  const prefix = name + '='
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length)
}

/** Decode a CSRF cookie value; return undefined on malformed percent-encoding. */
function safeDecodeCookieValue(value: string): string | undefined {
  try {
    return decodeURIComponent(value)
  } catch {
    // Malformed `%` sequences must not throw client-side; omit CSRF and fail closed server-side.
    return undefined
  }
}

function reportSessionExpired(path: string, status: number): void {
  if (
    status === 401 &&
    path !== '/api/operator-auth/login' &&
    path !== '/api/system-settings/initialize'
  ) {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    }
  }
}

/**
 * RequestInit for raw browser ``fetch`` (SSE / blob) that mirrors ApiClient:
 * same-origin credentials always, and CSRF header on unsafe methods.
 *
 * Prefer ``apiClient`` for JSON; use this only when the response body must be
 * consumed as a stream or blob outside ApiClient.
 */
export function browserFetchInit(init: RequestInit = {}): RequestInit {
  const method = String(init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)

  if (!SAFE_METHODS.has(method) && !headers.has(CSRF_HEADER)) {
    const csrf = readCookie(CSRF_COOKIE)
    if (csrf) {
      const decoded = safeDecodeCookieValue(csrf)
      if (decoded !== undefined) headers.set(CSRF_HEADER, decoded)
    }
  }

  return {
    ...init,
    headers,
    credentials: init.credentials ?? 'same-origin',
  }
}

/** Dispatch session-expired for raw-fetch 401s (mirrors ApiClient). */
export function reportBrowserSessionExpired(path: string, status: number): void {
  reportSessionExpired(path, status)
}

function throwApiError(path: string, error: ApiError): never {
  if (error.status != null) {
    reportSessionExpired(path, error.status)
  }
  throw error
}

export interface ApiClientConfig {
  baseUrl?: string
  fetcher?: typeof fetch
  defaultHeaders?: HeadersInit
}

export interface ApiCallOptions {
  query?: QueryParams
  headers?: HeadersInit
  signal?: AbortSignal
}

export interface ApiMutationOptions extends ApiCallOptions {
  body?: unknown
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly fetcher: typeof fetch
  private readonly defaultHeaders?: HeadersInit

  constructor(config: ApiClientConfig = {}) {
    this.baseUrl = config.baseUrl ?? ''
    // Bind fetch to window to prevent "Illegal invocation" error
    this.fetcher = config.fetcher ?? fetch.bind(globalThis)
    this.defaultHeaders = config.defaultHeaders
  }

  get<T>(path: string, options: ApiCallOptions = {}) {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(path: string, options: ApiMutationOptions = {}) {
    return this.request<T>(path, { ...options, method: 'POST' })
  }

  put<T>(path: string, options: ApiMutationOptions = {}) {
    return this.request<T>(path, { ...options, method: 'PUT' })
  }

  patch<T>(path: string, options: ApiMutationOptions = {}) {
    return this.request<T>(path, { ...options, method: 'PATCH' })
  }

  delete<T>(path: string, options: ApiCallOptions = {}) {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }

  private async request<T>(
    path: string,
    options: (ApiCallOptions | ApiMutationOptions) & { method: string }
  ): Promise<T> {
    const queryString = buildQueryString(options.query)
    const url = joinUrl(this.baseUrl, `${path}${queryString}`)

    const headers = withMindAtlasLocale(this.defaultHeaders)
    if (options.headers) {
      new Headers(options.headers).forEach((value, key) => headers.set(key, value))
    }
    if (!headers.has('accept')) headers.set('accept', 'application/json')

    if (!SAFE_METHODS.has(options.method.toUpperCase()) && !headers.has(CSRF_HEADER)) {
      const csrf = readCookie(CSRF_COOKIE)
      if (csrf) {
        const decoded = safeDecodeCookieValue(csrf)
        if (decoded !== undefined) headers.set(CSRF_HEADER, decoded)
      }
    }

    const init: RequestInit = {
      method: options.method,
      headers,
      signal: options.signal,
      credentials: 'same-origin',
    }

    const body = (options as ApiMutationOptions).body
    if (body !== undefined) {
      if (isFormData(body)) {
        init.body = body
      } else if (typeof body === 'string') {
        init.body = body
      } else {
        if (!headers.has('content-type')) headers.set('content-type', 'application/json')
        init.body = JSON.stringify(body)
      }
    }

    let response: Response
    try {
      response = await this.fetcher(url, init)
    } catch (error) {
      throw new ApiError({
        message: error instanceof Error ? error.message : 'Network error',
        url,
        details: error,
      })
    }

    const rawText = await response.text()
    const payload: unknown = rawText ? parseMaybeJson(rawText) : null

    if (response.ok) {
      if (isApiResponse(payload)) {
        if (payload.code === 0) return payload.data as T
        return throwApiError(
          path,
          new ApiError({
            message: payload.message || 'API error',
            status: response.status,
            code: payload.code,
            url,
            details: payload.data,
          }),
        )
      }
      return payload as T
    }

    if (isApiResponse(payload)) {
      return throwApiError(
        path,
        new ApiError({
          message: payload.message || response.statusText || 'Request failed',
          status: response.status,
          code: payload.code,
          url,
          details: payload.data,
        }),
      )
    }

    return throwApiError(
      path,
      new ApiError({
        message:
          (typeof payload === 'string' && payload.trim()) ||
          response.statusText ||
          'Request failed',
        status: response.status,
        url,
        details: payload,
      }),
    )
  }
}

export const apiClient = new ApiClient()
