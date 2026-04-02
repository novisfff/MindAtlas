import type { PluginConfig } from './config'
import { createCapabilityUnavailableMessage, createCatalogNotReadyMessage, createCatalogReloadRequiredMessage, mapMindAtlasErrorMessage } from './errors'
import { requestMindAtlas } from './http'
import type { MindAtlasRuntimeCapability } from './catalog'

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
  getCapabilityByToolName: (toolName: string) => MindAtlasRuntimeCapability | undefined
  isToolStale: (toolName: string) => boolean
  logHeaders: (toolName: string, context?: ToolExecutionContextLike) => Record<string, string>
}

function buildRoutingHint(capability: MindAtlasRuntimeCapability): string {
  switch (capability.capabilityKey) {
    case 'submit_context_capture':
      return 'MindAtlas route hint: prefer this for remember/save/record/store/archive requests and other durable-memory capture tasks.'
    case 'search_entries':
      return 'MindAtlas route hint: prefer this for search/look up/find previous records, "did I record this before", and recent or time-bounded searches.'
    case 'get_entry':
      return 'MindAtlas route hint: prefer this for exact record detail after search or when an entry ID is already known.'
    case 'create_relation':
      return 'MindAtlas route hint: prefer this for connect/link/associate two stored records.'
    case 'query_knowledge_graph':
      return 'MindAtlas route hint: prefer this for cross-record relations, patterns, why items are related, and synthesized knowledge questions.'
    case 'generate_weekly_report':
      return 'MindAtlas route hint: prefer this for weekly recap/review/digest questions such as "what did I do this week", "last week", or "recently".'
    case 'generate_monthly_report':
      return 'MindAtlas route hint: prefer this for monthly recap/review/digest questions such as "what did I do this month" or "this month".'
    default:
      if (capability.sourceType === 'workflow') {
        return 'MindAtlas route hint: this is a published workflow path. Prefer it when the user asks to run a workflow or a specialized MindAtlas process.'
      }
      if (capability.sourceType === 'agent') {
        return 'MindAtlas route hint: this is a published agent path. Prefer it when the user asks MindAtlas to run a configured agent or workflow-like assistant.'
      }
      if (capability.implementationType === 'report') {
        return 'MindAtlas route hint: prefer this for recap, review, digest, summary, or recent-activity tasks grounded in stored records.'
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
