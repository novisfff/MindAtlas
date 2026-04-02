import { definePluginEntry, type OpenClawPluginApi, type OpenClawPluginServiceContext, type PluginLogger } from 'openclaw/plugin-sdk/plugin-entry'

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
import { syncBundledSkills } from './skills'
import {
  buildToolDescription,
  createCapabilityToolRegistration,
  createTextResult,
  type ToolExecutionContextLike,
  type ToolResult,
} from './tools'

type LogMethod = 'info' | 'warn' | 'error' | 'debug'

function stableSerialize(value: unknown): string {
  try {
    return JSON.stringify(value)
  } catch {
    return '[unserializable]'
  }
}

function log(logger: PluginLogger | undefined, method: LogMethod, message: string, details?: Record<string, unknown>) {
  try {
    const target = logger?.[method] ?? logger?.info
    if (!target) {
      return
    }

    const suffix = details ? ` ${stableSerialize(details)}` : ''
    target(`[${PLUGIN_ID}] ${message}${suffix}`)
  } catch {
    // Logging should never break plugin execution.
  }
}

function extractRuntimeConfig(api: OpenClawPluginApi): unknown {
  return api.pluginConfig ?? api.config
}

function normalizeContextHeaders(toolName: string, context?: ToolExecutionContextLike): Record<string, string> {
  const headers: Record<string, string> = {
    'X-OpenClaw-Source': PLUGIN_ID,
    'X-OpenClaw-Tool': toolName,
  }
  const channel = context?.channel ?? context?.messageChannel
  const session = context?.session ?? context?.sessionId

  if (channel) {
    headers['X-OpenClaw-Channel'] = channel
  }
  if (session) {
    headers['X-OpenClaw-Session'] = session
  }
  return headers
}

interface RefreshOptions {
  initialRegistration?: boolean
  reason?: 'startup' | 'service-start' | 'ttl' | 'manual'
}

export class OpenClawMindAtlasPluginRuntime {
  private readonly api: OpenClawPluginApi
  private readonly logger: PluginLogger | undefined
  private readonly rawConfig: unknown
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
  private refreshServiceRegistered = false
  private lastReloadWarningKey = ''

  constructor(api: OpenClawPluginApi) {
    this.api = api
    this.logger = api.logger
    this.rawConfig = extractRuntimeConfig(api)
  }

  async register() {
    if (this.api.registrationMode !== 'full') {
      log(this.logger, 'debug', 'Skipping MindAtlas runtime registration because the plugin is not loading in full registration mode.', {
        registrationMode: this.api.registrationMode,
      })
      return
    }

    this.syncBundledSkillsIntoOpenClaw()

    const validationIssue = validatePluginConfig(this.rawConfig)
    if (validationIssue) {
      log(this.logger, 'warn', describePluginConfigIssue(validationIssue))
      this.config = null
      return
    }

    try {
      this.config = resolvePluginConfig(this.rawConfig)
    } catch (error) {
      log(this.logger, 'error', error instanceof Error ? error.message : 'Invalid plugin configuration.')
      return
    }
    if (!this.config) {
      log(this.logger, 'warn', 'openclaw-mindatlas did not receive a usable runtime config. No MindAtlas tools were registered.')
      return
    }

    await this.refreshCatalog({
      initialRegistration: true,
      reason: 'startup',
    })
    this.registerRefreshService()
  }

  async start(_context?: OpenClawPluginServiceContext) {
    if (!this.config || this.intervalHandle) {
      return
    }

    const refreshMs = (this.config.catalogRefreshTtlSec ?? DEFAULT_CATALOG_REFRESH_TTL_SEC) * 1000
    this.intervalHandle = setInterval(() => {
      void this.refreshCatalog({ reason: 'ttl' })
    }, refreshMs)
    this.intervalHandle.unref?.()
  }

  async stop(_context?: OpenClawPluginServiceContext) {
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

  async refreshCatalog(options: RefreshOptions = {}) {
    if (!this.config) {
      return
    }
    if (this.refreshing) {
      await this.refreshing
      return
    }

    this.refreshing = this.performRefresh({
      initialRegistration: options.initialRegistration ?? false,
      reason: options.reason ?? 'manual',
    })
    try {
      await this.refreshing
    } finally {
      this.refreshing = null
    }
  }

  private registerRefreshService() {
    if (!this.config || this.refreshServiceRegistered) {
      return
    }

    this.api.registerService({
      id: `${PLUGIN_ID}.catalog-refresh`,
      start: async (context) => {
        await this.start(context)
      },
      stop: async (context) => {
        await this.stop(context)
      },
    })
    this.refreshServiceRegistered = true
  }

  private syncBundledSkillsIntoOpenClaw() {
    try {
      const result = syncBundledSkills()
      for (const warning of result.warnings) {
        log(this.logger, 'warn', warning, {
          skillsRoot: result.managedRootDir,
        })
      }
      if (result.syncedSkillIds.length > 0) {
        log(this.logger, 'info', 'Synced MindAtlas shipped skills into the active OpenClaw custom skills directory.', {
          skillsRoot: result.managedRootDir,
          skillIds: result.syncedSkillIds,
          skippedSkillIds: result.skippedSkillIds,
        })
      }
    } catch (error) {
      log(
        this.logger,
        'warn',
        error instanceof Error
          ? `Failed to sync MindAtlas shipped skills into OpenClaw custom skills: ${error.message}`
          : 'Failed to sync MindAtlas shipped skills into OpenClaw custom skills.',
      )
    }
  }

  private async performRefresh(options: Required<RefreshOptions>) {
    if (!this.config) {
      return
    }

    try {
      const response = await fetchCapabilityCatalog(this.config, {
        'X-OpenClaw-Source': PLUGIN_ID,
      })
      const nextSnapshot = createCatalogSnapshot(response)
      const previousSnapshot = this.snapshot
      const discoveredCapabilities = [...nextSnapshot.itemsByToolName.values()]
      const availableCapabilities = discoveredCapabilities.filter((capability) => capability.available)
      const unavailableCapabilities = discoveredCapabilities.filter((capability) => !capability.available)
      const nameDelta = diffToolNames(previousSnapshot.toolNames, nextSnapshot.toolNames)
      const metadataDelta = diffRegisteredToolMetadata(previousSnapshot, nextSnapshot, this.registeredToolNames)
      const newlyAvailableUnregisteredToolNames = availableCapabilities
        .filter((capability) => !this.registeredToolNames.has(capability.toolName))
        .map((capability) => capability.toolName)
        .sort()

      this.snapshot = nextSnapshot

      if (options.initialRegistration) {
        for (const capability of availableCapabilities) {
          this.registerCapabilityTool(capability)
        }
      } else {
        this.handleRuntimeDrift({
          nameDelta,
          metadataDelta,
          newlyAvailableUnregisteredToolNames,
        })
      }

      const registeredToolNames = [...this.registeredToolNames].sort()
      const availableToolNames = availableCapabilities.map((capability) => capability.toolName).sort()
      log(this.logger, 'info', 'MindAtlas catalog refresh succeeded.', {
        reason: options.reason,
        integrationName: nextSnapshot.integrationName,
        totalCapabilities: discoveredCapabilities.length,
        availableCapabilities: availableCapabilities.length,
        unavailableCapabilities: unavailableCapabilities.length,
        availableToolNames,
        registeredToolNames,
        reloadRequired: this.reloadRequired,
      })

      if (registeredToolNames.length === 0) {
        if (discoveredCapabilities.length === 0) {
          log(this.logger, 'warn', 'MindAtlas catalog refresh succeeded but returned no capabilities. No MindAtlas tools are registered in this Gateway process.', {
            integrationName: nextSnapshot.integrationName,
            reloadRequired: this.reloadRequired,
          })
        } else if (availableCapabilities.length === 0) {
          log(this.logger, 'warn', 'MindAtlas catalog refresh succeeded but all discovered capabilities are currently unavailable. No MindAtlas tools are registered in this Gateway process.', {
            integrationName: nextSnapshot.integrationName,
            unavailableCapabilities: unavailableCapabilities.map((capability) => ({
              capabilityKey: capability.capabilityKey,
              toolName: capability.toolName,
              availabilityReason: capability.availabilityReason ?? null,
            })),
            reloadRequired: this.reloadRequired,
          })
        }
      }
    } catch (error) {
      const details = {
        reason: options.reason,
        error: error instanceof Error ? error.message : 'Failed to refresh MindAtlas catalog.',
      }

      if (options.initialRegistration) {
        log(
          this.logger,
          'warn',
          'MindAtlas startup catalog fetch failed. No MindAtlas tools were registered. Fix the plugin config or connectivity, then reload the OpenClaw Gateway so fresh sessions can see MindAtlas tools.',
          details,
        )
        return
      }

      log(this.logger, 'error', 'Failed to refresh MindAtlas catalog.', details)
    }
  }

  private handleRuntimeDrift({
    nameDelta,
    metadataDelta,
    newlyAvailableUnregisteredToolNames,
  }: {
    nameDelta: ReturnType<typeof diffToolNames>
    metadataDelta: ReturnType<typeof diffRegisteredToolMetadata>
    newlyAvailableUnregisteredToolNames: string[]
  }) {
    if (this.registeredToolNames.size > 0 && (nameDelta.changed || metadataDelta.changed)) {
      this.reloadRequired = true
      this.staleToolNames = new Set(
        [
          ...[...this.registeredToolNames].filter((toolName) => !this.snapshot.toolNames.has(toolName)),
          ...metadataDelta.changedToolNames,
        ].sort(),
      )

      const warningKey = JSON.stringify({
        type: 'registration-drift',
        addedToolNames: nameDelta.added,
        removedToolNames: nameDelta.removed,
        changedToolNames: metadataDelta.changedToolNames,
      })
      if (warningKey !== this.lastReloadWarningKey) {
        this.lastReloadWarningKey = warningKey
        log(
          this.logger,
          'warn',
          'MindAtlas catalog registration metadata changed after startup. OpenClaw will not hot-refresh registered MindAtlas tools. Reload the Gateway and start a new session to pick up the updated tool surface.',
          {
            addedToolNames: nameDelta.added,
            removedToolNames: nameDelta.removed,
            changedToolNames: metadataDelta.changedToolNames,
          },
        )
      }
    }

    if (newlyAvailableUnregisteredToolNames.length > 0) {
      this.reloadRequired = true

      const warningKey = JSON.stringify({
        type: 'late-available-tools',
        toolNames: newlyAvailableUnregisteredToolNames,
      })
      if (warningKey !== this.lastReloadWarningKey) {
        this.lastReloadWarningKey = warningKey
        log(
          this.logger,
          'warn',
          'MindAtlas now exposes available tools that were not registered during startup. This plugin does not late-register tools. Reload the OpenClaw Gateway and start a new session to expose them.',
          {
            toolNames: newlyAvailableUnregisteredToolNames,
          },
        )
      }
    }
  }

  private registerCapabilityTool(capability: MindAtlasRuntimeCapability) {
    if (!this.config || this.registeredToolNames.has(capability.toolName)) {
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

    this.api.registerTool(tool as Parameters<OpenClawPluginApi['registerTool']>[0])
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

export async function registerMindAtlasPlugin(api: OpenClawPluginApi) {
  const runtime = new OpenClawMindAtlasPluginRuntime(api)
  await runtime.register()
  return runtime
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: 'MindAtlas Capability Gateway',
  description: 'Expose MindAtlas capability catalog items as OpenClaw tools.',
  register: async (api) => {
    await registerMindAtlasPlugin(api)
  },
})

export { PLUGIN_ID, createTextResult }
export type { ToolExecutionContextLike, ToolResult }
