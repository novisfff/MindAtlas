import type { PluginConfig } from './config'
import { requestMindAtlas } from './http'

export interface MindAtlasRuntimeCapability {
  capabilityKey: string
  toolName: string
  title: string
  description: string
  sourceType: 'system_adapter' | 'tool' | 'workflow' | 'agent'
  implementationType: string
  available: boolean
  availabilityReason?: string | null
  inputSummary: string
  outputSummary: string
  inputSchema: Record<string, unknown>
  outputSchema: Record<string, unknown>
  toolResponseMode: 'json_schema' | 'text_field'
}

export interface CatalogResponse {
  integrationName: string
  capabilities: MindAtlasRuntimeCapability[]
}

export interface CatalogSnapshot {
  integrationName: string
  itemsByToolName: Map<string, MindAtlasRuntimeCapability>
  toolNames: Set<string>
}

export interface ToolNameDelta {
  added: string[]
  removed: string[]
  changed: boolean
}

export interface ToolRegistrationDelta {
  changedToolNames: string[]
  changed: boolean
}

function stableSerialize(value: unknown): string {
  if (value === null || value === undefined) {
    return 'null'
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableSerialize(item)).join(',')}]`
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right))
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${stableSerialize(item)}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function buildRegistrationSignature(capability: MindAtlasRuntimeCapability): string {
  return stableSerialize({
    capabilityKey: capability.capabilityKey,
    sourceType: capability.sourceType,
    implementationType: capability.implementationType,
    title: capability.title,
    description: capability.description,
    inputSummary: capability.inputSummary,
    outputSummary: capability.outputSummary,
    inputSchema: capability.inputSchema,
  })
}

export async function fetchCapabilityCatalog(
  config: PluginConfig,
  headers: Record<string, string>,
): Promise<CatalogResponse> {
  return requestMindAtlas<CatalogResponse>({
    method: 'GET',
    path: '/api/integrations/openclaw/capabilities',
    config,
    headers,
  })
}

export function createCatalogSnapshot(response: CatalogResponse): CatalogSnapshot {
  const itemsByToolName = new Map<string, MindAtlasRuntimeCapability>()
  for (const capability of response.capabilities) {
    if (!capability.toolName) {
      continue
    }
    itemsByToolName.set(capability.toolName, capability)
  }
  return {
    integrationName: response.integrationName,
    itemsByToolName,
    toolNames: new Set(itemsByToolName.keys()),
  }
}

export function diffToolNames(previous: Set<string>, next: Set<string>): ToolNameDelta {
  const added = [...next].filter((name) => !previous.has(name)).sort()
  const removed = [...previous].filter((name) => !next.has(name)).sort()
  return {
    added,
    removed,
    changed: added.length > 0 || removed.length > 0,
  }
}

export function diffRegisteredToolMetadata(
  previous: CatalogSnapshot,
  next: CatalogSnapshot,
  registeredToolNames: Iterable<string>,
): ToolRegistrationDelta {
  const changedToolNames = [...registeredToolNames]
    .filter((toolName) => previous.itemsByToolName.has(toolName) && next.itemsByToolName.has(toolName))
    .filter((toolName) => {
      const previousItem = previous.itemsByToolName.get(toolName)
      const nextItem = next.itemsByToolName.get(toolName)
      if (!previousItem || !nextItem) {
        return false
      }
      return buildRegistrationSignature(previousItem) !== buildRegistrationSignature(nextItem)
    })
    .sort()

  return {
    changedToolNames,
    changed: changedToolNames.length > 0,
  }
}
