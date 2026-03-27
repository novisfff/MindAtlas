export const PLUGIN_ID = 'openclaw-mindatlas'
export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000
export const DEFAULT_CATALOG_REFRESH_TTL_SEC = 300

export interface PluginConfig {
  baseUrl: string
  integrationSecret: string
  requestTimeoutMs: number
  catalogRefreshTtlSec: number
}

function normalizeString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function normalizePositiveInteger(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
    return value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number.parseInt(value.trim(), 10)
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed
    }
  }
  return fallback
}

export function extractPluginEntryConfig(rawConfig: unknown, pluginId: string = PLUGIN_ID): Record<string, unknown> {
  if (!rawConfig || typeof rawConfig !== 'object') {
    return {}
  }

  const configRecord = rawConfig as Record<string, unknown>
  if (configRecord.baseUrl || configRecord.integrationSecret) {
    return configRecord
  }

  const plugins = configRecord.plugins
  if (!plugins || typeof plugins !== 'object') {
    return {}
  }
  const entries = (plugins as Record<string, unknown>).entries
  if (!entries || typeof entries !== 'object') {
    return {}
  }
  const pluginEntry = (entries as Record<string, unknown>)[pluginId]
  if (!pluginEntry || typeof pluginEntry !== 'object') {
    return {}
  }
  const nestedConfig = (pluginEntry as Record<string, unknown>).config
  if (!nestedConfig || typeof nestedConfig !== 'object') {
    return {}
  }
  return nestedConfig as Record<string, unknown>
}

export function normalizeBaseUrl(value: string): string {
  let normalized = value.trim().replace(/\/+$/, '')
  if (normalized.endsWith('/api')) {
    normalized = normalized.slice(0, -4)
  }
  return normalized
}

export function resolvePluginConfig(rawConfig: unknown): PluginConfig {
  const config = extractPluginEntryConfig(rawConfig)
  const baseUrl = normalizeBaseUrl(normalizeString(config.baseUrl))
  const integrationSecret = normalizeString(config.integrationSecret)

  if (!baseUrl) {
    throw new Error('MindAtlas baseUrl is required for openclaw-mindatlas.')
  }
  if (!integrationSecret) {
    throw new Error('MindAtlas integrationSecret is required for openclaw-mindatlas.')
  }

  return {
    baseUrl,
    integrationSecret,
    requestTimeoutMs: normalizePositiveInteger(config.requestTimeoutMs, DEFAULT_REQUEST_TIMEOUT_MS),
    catalogRefreshTtlSec: normalizePositiveInteger(config.catalogRefreshTtlSec, DEFAULT_CATALOG_REFRESH_TTL_SEC),
  }
}
