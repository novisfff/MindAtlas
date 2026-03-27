export class MindAtlasApiError extends Error {
  statusCode: number
  code?: number
  details?: unknown

  constructor(message: string, options?: { statusCode?: number; code?: number; details?: unknown }) {
    super(message)
    this.name = 'MindAtlasApiError'
    this.statusCode = options?.statusCode ?? 500
    this.code = options?.code
    this.details = options?.details
  }
}

export function createCatalogReloadRequiredMessage(toolName: string): string {
  return `MindAtlas capability catalog structure changed for "${toolName}". Reload the OpenClaw plugin or Gateway to refresh registered tools.`
}

export function createCatalogNotReadyMessage(toolName: string): string {
  return `MindAtlas capability "${toolName}" is not ready yet. Check the plugin configuration or wait for the next catalog refresh.`
}

export function createCapabilityUnavailableMessage(toolName: string, reason?: string | null): string {
  if (reason && reason.trim()) {
    return `MindAtlas capability "${toolName}" is currently unavailable: ${reason.trim()}`
  }
  return `MindAtlas capability "${toolName}" is currently unavailable.`
}

export function mapMindAtlasErrorMessage(error: unknown): string {
  if (!(error instanceof MindAtlasApiError)) {
    if (error instanceof Error && error.message.trim()) {
      return error.message
    }
    return 'MindAtlas request failed.'
  }

  switch (error.code) {
    case 40161:
      return 'MindAtlas integration secret is missing or invalid.'
    case 40361:
      return 'MindAtlas OpenClaw integration is disabled.'
    case 40362:
      return 'This MindAtlas capability is disabled or not exposed.'
    case 40461:
      return 'The requested MindAtlas capability was not found. Refresh the catalog or reload the plugin.'
    default:
      return error.message || 'MindAtlas request failed.'
  }
}
