import type { PluginConfig } from './config'
import { createCapabilityUnavailableMessage, createCatalogNotReadyMessage, createCatalogReloadRequiredMessage, mapMindAtlasErrorMessage } from './errors'
import { requestMindAtlas } from './http'
import type { CatalogSnapshot, MindAtlasRuntimeCapability } from './catalog'

export const MINDATLAS_LIST_CAPABILITIES_TOOL_NAME = 'mindatlas_list_capabilities'
export const MINDATLAS_RUN_CAPABILITY_TOOL_NAME = 'mindatlas_run_capability'

export interface ToolExecutionContextLike {
  channel?: string
  messageChannel?: string
  session?: string
  sessionId?: string
}

export interface ToolContentItem {
  type: 'text'
  text: string
}

export interface ToolResult {
  content: ToolContentItem[]
}

export interface ToolRegistration {
  name: string
  description: string
  parameters: Record<string, unknown>
  execute: (id: string, params: Record<string, unknown>, context?: ToolExecutionContextLike) => Promise<ToolResult>
}

export interface RuntimeToolState {
  config: PluginConfig
  getCatalogSnapshot: () => CatalogSnapshot
  getCapabilityByKey: (capabilityKey: string) => MindAtlasRuntimeCapability | undefined
  getCapabilityByToolName: (toolName: string) => MindAtlasRuntimeCapability | undefined
  isCapabilityToolRegistered: (toolName: string) => boolean
  isToolStale: (toolName: string) => boolean
  listRegisteredToolNames: () => string[]
  listStaleToolNames: () => string[]
  refreshCatalog: (reason?: 'manual') => Promise<{ ok: boolean; error?: string }>
  reloadRequired: () => boolean
  logHeaders: (toolName: string, context?: ToolExecutionContextLike) => Record<string, string>
}

function buildRoutingHint(capability: MindAtlasRuntimeCapability): string {
  switch (capability.capabilityKey) {
    case 'submit_context_capture':
      return 'MindAtlas route hint: prefer this for remember/save/record/store/archive requests and other durable-memory capture tasks. Submit one high-value context block and let OpenClaw request metadata carry source, channel, session, and tool context automatically.'
    case 'search_entries':
      return 'MindAtlas route hint: prefer this for search/look up/find previous records, "did I record this before", and recent or time-bounded searches.'
    case 'get_entry':
      return 'MindAtlas route hint: prefer this for exact record detail after search or when an entry ID is already known.'
    case 'create_relation':
      return 'MindAtlas route hint: prefer this for connect/link/associate two stored records.'
    case 'query_knowledge_graph':
      return 'MindAtlas route hint: prefer this for cross-record relations, patterns, why items are related, and synthesized knowledge questions.'
    case 'generate_periodic_review':
      return 'MindAtlas route hint: prefer this for time-bounded recap/review/digest/summary requests such as "what did I do last week", "show this month\'s tag distribution", or "summarize 2026-03-01 to 2026-03-31".'
    default:
      if (capability.sourceType === 'workflow') {
        return 'MindAtlas route hint: this is a published workflow path. Prefer it when the user asks to run a workflow or a specialized MindAtlas process.'
      }
      if (capability.sourceType === 'agent') {
        return 'MindAtlas route hint: this is a published agent path. Prefer it when the user asks MindAtlas to run a configured agent or workflow-like assistant.'
      }
      if (capability.implementationType === 'report') {
        return 'MindAtlas route hint: prefer this for time-bounded recap, review, digest, summary, or recent-activity tasks grounded in stored records.'
      }
      if (capability.implementationType === 'knowledge_graph') {
        return 'MindAtlas route hint: prefer this for graph, relation, or cross-record synthesis questions.'
      }
      if (capability.implementationType === 'relation') {
        return 'MindAtlas route hint: prefer this for explicit relation or linking tasks between records.'
      }
      return 'MindAtlas route hint: prefer this when the task belongs to MindAtlas durable memory, historical lookup, recap, or structured knowledge work.'
  }
}

export function buildToolDescription(capability: MindAtlasRuntimeCapability): string {
  const parts = [
    buildRoutingHint(capability),
    capability.title.trim(),
    capability.description.trim(),
    capability.inputSummary ? `Input: ${capability.inputSummary.trim()}` : '',
    capability.outputSummary ? `Output: ${capability.outputSummary.trim()}` : '',
  ].filter(Boolean)
  return parts.join('\n\n')
}

function formatResult(result: unknown): string {
  if (typeof result === 'string') {
    return result
  }
  return JSON.stringify(result, null, 2)
}

export function createTextResult(text: string): ToolResult {
  return {
    content: [{ type: 'text', text }],
  }
}

function normalizeInputObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('MindAtlas dispatcher requires `input` to be a JSON object.')
  }
  return value as Record<string, unknown>
}

function buildDispatcherCapabilityPayload(capability: MindAtlasRuntimeCapability, state: RuntimeToolState) {
  return {
    capabilityKey: capability.capabilityKey,
    toolName: capability.toolName,
    title: capability.title,
    description: capability.description,
    sourceType: capability.sourceType,
    implementationType: capability.implementationType,
    available: capability.available,
    availabilityReason: capability.availabilityReason ?? null,
    inputSummary: capability.inputSummary,
    outputSummary: capability.outputSummary,
    inputSchema: capability.inputSchema,
    outputSchema: capability.outputSchema,
    toolResponseMode: capability.toolResponseMode,
    dedicatedToolRegistered: state.isCapabilityToolRegistered(capability.toolName),
  }
}

export function createCapabilityToolRegistration(toolName: string, state: RuntimeToolState): ToolRegistration {
  return {
    name: toolName,
    description: '',
    parameters: {
      type: 'object',
      additionalProperties: true,
      properties: {},
      required: [],
    },
    async execute(_id: string, params: Record<string, unknown>, context?: ToolExecutionContextLike): Promise<ToolResult> {
      if (state.isToolStale(toolName)) {
        throw new Error(createCatalogReloadRequiredMessage(toolName))
      }

      const capability = state.getCapabilityByToolName(toolName)
      if (!capability) {
        throw new Error(createCatalogNotReadyMessage(toolName))
      }
      if (!capability.available) {
        throw new Error(createCapabilityUnavailableMessage(toolName, capability.availabilityReason))
      }

      try {
        const response = await requestMindAtlas<{
          capabilityKey: string
          toolName: string
          result: unknown
        }>({
          method: 'POST',
          path: `/api/integrations/openclaw/capabilities/${encodeURIComponent(capability.capabilityKey)}/execute`,
          config: state.config,
          body: params,
          headers: state.logHeaders(toolName, context),
        })
        return createTextResult(formatResult(response.result))
      } catch (error) {
        throw new Error(mapMindAtlasErrorMessage(error))
      }
    },
  }
}

export function createListCapabilitiesToolRegistration(state: RuntimeToolState): ToolRegistration {
  return {
    name: MINDATLAS_LIST_CAPABILITIES_TOOL_NAME,
    description: [
      'MindAtlas route hint: use this to refresh and inspect the latest exposed MindAtlas capability catalog, especially when a needed custom workflow, agent, or tool is not already visible as a dedicated `mindatlas_*` tool.',
      'Lists the latest available and unavailable MindAtlas capabilities and marks whether each capability already has a dedicated session-visible tool.',
      'Input: none.',
      'Output: JSON text with `availableCapabilities`, `unavailableCapabilities`, `registeredToolNames`, `staleToolNames`, and `reloadRequired`.',
    ].join('\n\n'),
    parameters: {
      type: 'object',
      properties: {},
      required: [],
      additionalProperties: false,
    },
    async execute(): Promise<ToolResult> {
      const refresh = await state.refreshCatalog('manual')
      const snapshot = state.getCatalogSnapshot()
      const availableCapabilities = snapshot.capabilities
        .filter((capability) => capability.available)
        .map((capability) => buildDispatcherCapabilityPayload(capability, state))
      const unavailableCapabilities = snapshot.capabilities
        .filter((capability) => !capability.available)
        .map((capability) => buildDispatcherCapabilityPayload(capability, state))

      return createTextResult(
        formatResult({
          integrationName: snapshot.integrationName,
          refreshedAt: new Date().toISOString(),
          reloadRequired: state.reloadRequired(),
          registeredToolNames: state.listRegisteredToolNames(),
          staleToolNames: state.listStaleToolNames(),
          availableCapabilities,
          unavailableCapabilities,
          ...(refresh.ok ? {} : { refreshError: refresh.error ?? 'Failed to refresh the MindAtlas capability catalog.' }),
        }),
      )
    },
  }
}

export function createRunCapabilityToolRegistration(state: RuntimeToolState): ToolRegistration {
  return {
    name: MINDATLAS_RUN_CAPABILITY_TOOL_NAME,
    description: [
      'MindAtlas route hint: use this when the user needs a newly exposed or custom MindAtlas capability that is not already available as a dedicated `mindatlas_*` tool in the current session.',
      'Refreshes the latest MindAtlas catalog, then executes the requested capability by `capabilityKey` with the provided structured `input` object.',
      'Input: `capabilityKey` (string) plus `input` (object).',
      'Output: the target capability result as JSON text.',
    ].join('\n\n'),
    parameters: {
      type: 'object',
      properties: {
        capabilityKey: {
          type: 'string',
          minLength: 1,
          description: 'The MindAtlas capability key to execute.',
        },
        input: {
          type: 'object',
          description: 'The structured input payload expected by the target capability.',
          additionalProperties: true,
        },
      },
      required: ['capabilityKey', 'input'],
      additionalProperties: false,
    },
    async execute(_id: string, params: Record<string, unknown>, context?: ToolExecutionContextLike): Promise<ToolResult> {
      const capabilityKey = typeof params.capabilityKey === 'string' ? params.capabilityKey.trim() : ''
      if (!capabilityKey) {
        throw new Error('MindAtlas dispatcher requires a non-empty capabilityKey.')
      }

      const input = normalizeInputObject(params.input)
      const refresh = await state.refreshCatalog('manual')
      const capability = state.getCapabilityByKey(capabilityKey)
      if (!capability) {
        const suffix = refresh.ok ? '' : ` Last refresh failed: ${refresh.error ?? 'unknown error'}.`
        throw new Error(`MindAtlas capability "${capabilityKey}" was not found in the latest catalog.${suffix}`)
      }
      if (!capability.available) {
        throw new Error(createCapabilityUnavailableMessage(capability.toolName || capabilityKey, capability.availabilityReason))
      }

      try {
        const response = await requestMindAtlas<{
          capabilityKey: string
          toolName: string
          result: unknown
        }>({
          method: 'POST',
          path: `/api/integrations/openclaw/capabilities/${encodeURIComponent(capability.capabilityKey)}/execute`,
          config: state.config,
          body: input,
          headers: state.logHeaders(MINDATLAS_RUN_CAPABILITY_TOOL_NAME, context),
        })
        return createTextResult(formatResult(response.result))
      } catch (error) {
        throw new Error(mapMindAtlasErrorMessage(error))
      }
    },
  }
}
