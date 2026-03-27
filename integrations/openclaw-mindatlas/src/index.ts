import {
  DEFAULT_CATALOG_REFRESH_TTL_SEC,
  PLUGIN_ID,
  describePluginConfigIssue,
  resolvePluginConfig,
  validatePluginConfig,
  type PluginConfig,
} from './config'
import {
  createCatalogSnapshot,
  diffRegisteredToolMetadata,
  diffToolNames,
  fetchCapabilityCatalog,
  type CatalogSnapshot,
  type MindAtlasRuntimeCapability,
} from './catalog'
import { createCapabilityToolRegistration, buildToolDescription, createTextResult, type ToolExecutionContextLike, type ToolRegistration, type ToolResult } from './tools'

type LogMethod = 'info' | 'warn' | 'error' | 'debug'

interface LoggerLike {
  info?: (message: string, ...args: unknown[]) => void
  warn?: (message: string, ...args: unknown[]) => void
  error?: (message: string, ...args: unknown[]) => void
  debug?: (message: string, ...args: unknown[]) => void
}

interface PluginService {
  id: string
  start?: () => void | Promise<void>
  stop?: () => void | Promise<void>
}

export interface PluginApiLike {
  config?: unknown
  logger?: LoggerLike
  registerTool: (tool: ToolRegistration) => void
  registerService: (service: PluginService) => void
}

function log(logger: LoggerLike | undefined, method: LogMethod, message: string, details?: Record<string, unknown>) {
  try {
    const target = logger?.[method] ?? logger?.info
    if (target) {
      if (details) {
        target(`[${PLUGIN_ID}] ${message}`, details)
      } else {
        target(`[${PLUGIN_ID}] ${message}`)
      }
    }
  } catch {
    // Logging should never break plugin execution.
  }
}

function normalizeContextHeaders(toolName: string, context?: ToolExecutionContextLike): Record<string, string> {
  const headers: Record<string, string> = {
    'X-OpenClaw-Source': PLUGIN_ID,
    'X-OpenClaw-Tool': toolName,
  }
  if (context?.channel) {
    headers['X-OpenClaw-Channel'] = context.channel
  }
  if (context?.session) {
    headers['X-OpenClaw-Session'] = context.session
  }
  return headers
}

export class OpenClawMindAtlasPluginRuntime {
  private readonly api: PluginApiLike
  private readonly logger: LoggerLike | undefined
  private config: PluginConfig | null = null
  private snapshot: CatalogSnapshot = {
    integrationName: 'MindAtlas',
    itemsByToolName: new Map(),
    toolNames: new Set(),
  }
  private registeredToolNames = new Set<string>()
  private staleToolNames = new Set<string>()
  private intervalHandle: NodeJS.Timeout | null = null
  private refreshing: Promise<void> | null = null
  private reloadRequired = false
  private lastStructureWarningKey = ''

  constructor(api: PluginApiLike) {
    this.api = api
    this.logger = api.logger
  }

  register() {
    this.api.registerService({
      id: `${PLUGIN_ID}.catalog-refresh`,
      start: async () => {
        await this.start()
      },
      stop: async () => {
        await this.stop()
      },
    })
  }

  async start() {
    const validationIssue = validatePluginConfig(this.api.config)
    if (validationIssue) {
      log(this.logger, 'warn', describePluginConfigIssue(validationIssue))
      this.config = null
      return
    }

    try {
      this.config = resolvePluginConfig(this.api.config)
    } catch (error) {
      log(this.logger, 'error', error instanceof Error ? error.message : 'Invalid plugin configuration.')
      return
    }
    if (!this.config) {
      log(this.logger, 'warn', 'openclaw-mindatlas did not receive a usable runtime config. No MindAtlas tools were registered.')
      return
    }

    await this.refreshCatalog()

    const refreshMs = (this.config?.catalogRefreshTtlSec ?? DEFAULT_CATALOG_REFRESH_TTL_SEC) * 1000
    this.intervalHandle = setInterval(() => {
      void this.refreshCatalog()
    }, refreshMs)
    this.intervalHandle.unref?.()
  }

  async stop() {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle)
      this.intervalHandle = null
    }
  }

  getCapabilityByToolName(toolName: string): MindAtlasRuntimeCapability | undefined {
    return this.snapshot.itemsByToolName.get(toolName)
  }

  isToolStale(toolName: string): boolean {
    return this.staleToolNames.has(toolName)
  }

  async refreshCatalog() {
    if (!this.config) {
      return
    }
    if (this.refreshing) {
      await this.refreshing
      return
    }

    this.refreshing = this.performRefresh()
    try {
      await this.refreshing
    } finally {
      this.refreshing = null
    }
  }

  private async performRefresh() {
    if (!this.config) {
      return
    }

    try {
      const response = await fetchCapabilityCatalog(this.config, {
        'X-OpenClaw-Source': PLUGIN_ID,
      })
      const nextSnapshot = createCatalogSnapshot(response)
      const previousSnapshot = this.snapshot
      const nameDelta = diffToolNames(previousSnapshot.toolNames, nextSnapshot.toolNames)
      const metadataDelta = diffRegisteredToolMetadata(previousSnapshot, nextSnapshot, this.registeredToolNames)
      this.snapshot = nextSnapshot

      if (previousSnapshot.toolNames.size > 0 && (nameDelta.changed || metadataDelta.changed)) {
        this.reloadRequired = true
        this.staleToolNames = new Set(
          [
            ...[...this.registeredToolNames].filter((toolName) => !nextSnapshot.toolNames.has(toolName)),
            ...metadataDelta.changedToolNames,
          ],
        )
        const warningKey = JSON.stringify({
          addedToolNames: nameDelta.added,
          removedToolNames: nameDelta.removed,
          changedToolNames: metadataDelta.changedToolNames,
        })
        if (warningKey !== this.lastStructureWarningKey) {
          this.lastStructureWarningKey = warningKey
          log(this.logger, 'warn', 'MindAtlas catalog registration metadata changed. Reload OpenClaw to refresh tool registration.', {
            addedToolNames: nameDelta.added,
            removedToolNames: nameDelta.removed,
            changedToolNames: metadataDelta.changedToolNames,
          })
        }
      }

      const newlyAvailable = [...nextSnapshot.itemsByToolName.values()].filter(
        (capability) => capability.available && !this.registeredToolNames.has(capability.toolName)
      )

      if (!this.reloadRequired && newlyAvailable.length > 0) {
        for (const capability of newlyAvailable) {
          this.registerCapabilityTool(capability)
        }
      }
    } catch (error) {
      log(this.logger, 'error', error instanceof Error ? error.message : 'Failed to refresh MindAtlas catalog.')
    }
  }

  private registerCapabilityTool(capability: MindAtlasRuntimeCapability) {
    if (!this.config) {
      return
    }

    const tool = createCapabilityToolRegistration(capability.toolName, {
      config: this.config,
      getCapabilityByToolName: (toolName) => this.getCapabilityByToolName(toolName),
      isToolStale: (toolName) => this.isToolStale(toolName),
      logHeaders: (toolName, context) => normalizeContextHeaders(toolName, context),
    })
    tool.description = buildToolDescription(capability)
    tool.parameters = capability.inputSchema

    this.api.registerTool(tool)
    this.registeredToolNames.add(capability.toolName)

    log(this.logger, 'info', 'Registered MindAtlas capability tool.', {
      toolName: capability.toolName,
      capabilityKey: capability.capabilityKey,
      sourceType: capability.sourceType,
    })
  }

  getState() {
    return {
      reloadRequired: this.reloadRequired,
      registeredToolNames: new Set(this.registeredToolNames),
      staleToolNames: new Set(this.staleToolNames),
      knownToolNames: new Set(this.snapshot.toolNames),
    }
  }
}

export function createPlugin(api: PluginApiLike) {
  const runtime = new OpenClawMindAtlasPluginRuntime(api)
  return {
    id: PLUGIN_ID,
    name: 'MindAtlas Capability Gateway',
    description: 'Expose MindAtlas capability catalog items as OpenClaw tools.',
    register() {
      runtime.register()
    },
    runtime,
  }
}

export default function register(api: PluginApiLike) {
  const runtime = new OpenClawMindAtlasPluginRuntime(api)
  runtime.register()
}
export { PLUGIN_ID, createTextResult }
export type { ToolExecutionContextLike, ToolResult }
