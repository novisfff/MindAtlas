import type { PluginConfig } from './config'
import { createCapabilityUnavailableMessage, createCatalogNotReadyMessage, createCatalogReloadRequiredMessage, mapMindAtlasErrorMessage } from './errors'
import { requestMindAtlas } from './http'
import type { MindAtlasRuntimeCapability } from './catalog'

export interface ToolExecutionContextLike {
  channel?: string
  session?: string
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

export function buildToolDescription(capability: MindAtlasRuntimeCapability): string {
  const parts = [
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
