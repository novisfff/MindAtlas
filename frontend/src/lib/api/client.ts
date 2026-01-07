export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export type QueryParamValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryParamValue | QueryParamValue[]>

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

    const headers = new Headers(this.defaultHeaders)
    if (options.headers) {
      new Headers(options.headers).forEach((value, key) => headers.set(key, value))
    }
    if (!headers.has('accept')) headers.set('accept', 'application/json')

    const init: RequestInit = {
      method: options.method,
      headers,
      signal: options.signal,
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
        throw new ApiError({
          message: payload.message || 'API error',
          status: response.status,
          code: payload.code,
          url,
          details: payload.data,
        })
      }
      return payload as T
    }

    if (isApiResponse(payload)) {
      throw new ApiError({
        message: payload.message || response.statusText || 'Request failed',
        status: response.status,
        code: payload.code,
        url,
        details: payload.data,
      })
    }

    throw new ApiError({
      message:
        (typeof payload === 'string' && payload.trim()) ||
        response.statusText ||
        'Request failed',
      status: response.status,
      url,
      details: payload,
    })
  }
}

export const apiClient = new ApiClient()
