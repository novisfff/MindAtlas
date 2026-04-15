import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import readline from 'node:readline/promises'
import { fileURLToPath } from 'node:url'

export const PLUGIN_ID = 'openclaw-mindatlas'
export const LOCAL_PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'openclaw-mindatlas')
export const DEFAULT_REQUEST_TIMEOUT_MS = 15000
export const DEFAULT_CATALOG_REFRESH_TTL_SEC = 300
export const MINDATLAS_SKILL_IDS = [
  'mindatlas-overview',
  'mindatlas-dispatcher',
  'mindatlas-auto-capture',
  'mindatlas-retrieval',
  'mindatlas-summary',
]

function cloneJson(value) {
  return value && typeof value === 'object' ? structuredClone(value) : {}
}

function normalizeEnvPath(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function normalizeString(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function sanitizePositiveInteger(value) {
  if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
    return value
  }

  const normalized = normalizeString(value)
  if (!normalized) {
    return null
  }

  const parsed = Number.parseInt(normalized, 10)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function readJsonFile(filePath, fallbackValue) {
  if (!fs.existsSync(filePath)) {
    return fallbackValue
  }

  const raw = fs.readFileSync(filePath, 'utf8').trim()
  if (!raw) {
    return fallbackValue
  }

  return JSON.parse(raw)
}

function writeJsonFile(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

function removeIfEmptyObject(parent, key) {
  const value = parent?.[key]
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return
  }

  if (Object.keys(value).length === 0) {
    delete parent[key]
  }
}

function createDefaultLogger(options = {}) {
  const output = options.output ?? process.stdout
  const errorOutput = options.errorOutput ?? process.stderr
  return {
    info(message = '') {
      output.write(`${message}\n`)
    },
    warn(message = '') {
      errorOutput.write(`${message}\n`)
    },
    error(message = '') {
      errorOutput.write(`${message}\n`)
    },
  }
}

function formatCommand(command, args) {
  return [command, ...args].join(' ')
}

function maskSecret(secret) {
  const normalized = normalizeString(secret)
  if (!normalized) {
    return '(missing)'
  }

  if (normalized.length <= 8) {
    return `${normalized.slice(0, 1)}***${normalized.slice(-1)}`
  }

  return `${normalized.slice(0, 4)}***${normalized.slice(-4)}`
}

function isValidHttpUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function formatTimestamp(date = new Date()) {
  const year = String(date.getFullYear())
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const seconds = String(date.getSeconds()).padStart(2, '0')
  return `${year}${month}${day}-${hours}${minutes}${seconds}`
}

export function resolveOpenClawConfigPath(options = {}) {
  const env = options.env ?? process.env
  const configPath = normalizeEnvPath(env.OPENCLAW_CONFIG_PATH)
  if (configPath) {
    return path.resolve(configPath)
  }

  return path.resolve(options.homeDir ?? os.homedir(), '.openclaw', 'openclaw.json')
}

export function resolveOpenClawRoot(options = {}) {
  const configPath = normalizeEnvPath((options.env ?? process.env).OPENCLAW_CONFIG_PATH)
  if (configPath) {
    return path.resolve(path.dirname(configPath))
  }

  const stateDir = normalizeEnvPath((options.env ?? process.env).OPENCLAW_STATE_DIR)
  if (stateDir) {
    return path.resolve(stateDir)
  }

  return path.resolve(options.homeDir ?? os.homedir(), '.openclaw')
}

export function resolveInstalledPluginRoot(config, options = {}) {
  const installPath = config?.plugins?.installs?.[PLUGIN_ID]?.installPath
  if (typeof installPath === 'string' && installPath.trim()) {
    return path.resolve(installPath)
  }

  return path.resolve(resolveOpenClawRoot(options), 'extensions', PLUGIN_ID)
}

export function ensureSkillsExtraDir(config, extraDir) {
  const nextConfig = config && typeof config === 'object' ? cloneJson(config) : {}
  const skills = nextConfig.skills && typeof nextConfig.skills === 'object' ? cloneJson(nextConfig.skills) : {}
  const load = skills.load && typeof skills.load === 'object' ? cloneJson(skills.load) : {}

  const currentExtraDirs = Array.isArray(load.extraDirs)
    ? load.extraDirs.filter((entry) => typeof entry === 'string' && entry.trim())
    : []

  const normalizedExtraDir = path.resolve(extraDir)
  if (!currentExtraDirs.some((entry) => path.resolve(entry) === normalizedExtraDir)) {
    currentExtraDirs.push(normalizedExtraDir)
  }

  load.extraDirs = currentExtraDirs
  skills.load = load
  nextConfig.skills = skills
  return nextConfig
}

export function detectLegacyMindAtlasToolPolicy(config) {
  const tools = config?.tools
  if (!tools || typeof tools !== 'object') {
    return []
  }

  const allow = Array.isArray(tools.allow)
    ? tools.allow.filter((entry) => typeof entry === 'string' && entry.trim())
    : []
  const profile = typeof tools.profile === 'string' ? tools.profile.trim() : ''
  const hasMindAtlasAllow = allow.some((entry) => entry === PLUGIN_ID || entry.startsWith('mindatlas_'))
  const removedPeriodicReviewTools = allow.filter(
    (entry) => entry === 'mindatlas_generate_weekly_report' || entry === 'mindatlas_generate_monthly_report',
  )

  if (!hasMindAtlasAllow && profile !== 'full') {
    return []
  }

  const warnings = []
  if (hasMindAtlasAllow) {
    warnings.push(
      'Detected legacy MindAtlas tools.allow entries in OpenClaw config. MindAtlas tool visibility now comes from official SDK registration, so these allowlist entries are deprecated and no longer required.',
    )
  }
  if (removedPeriodicReviewTools.length > 0) {
    warnings.push(
      'Detected removed MindAtlas report tool allowlist entries. `mindatlas_generate_weekly_report` and `mindatlas_generate_monthly_report` no longer exist; use `mindatlas_generate_periodic_review` instead.',
    )
  }
  if (profile === 'full' && hasMindAtlasAllow) {
    warnings.push(
      'Detected the old MindAtlas tools.profile compatibility setting in OpenClaw config. `configure:skills` no longer manages tools.profile because MindAtlas tools should now appear through required SDK registration instead.',
    )
  }
  return warnings
}

export function readPluginEntryState(config) {
  const entry = config?.plugins?.entries?.[PLUGIN_ID]
  const rawConfig = entry?.config && typeof entry.config === 'object' ? cloneJson(entry.config) : {}
  const baseUrl = normalizeString(rawConfig.baseUrl)
  const integrationSecret = normalizeString(rawConfig.integrationSecret)
  const requestTimeoutMs = sanitizePositiveInteger(rawConfig.requestTimeoutMs)
  const catalogRefreshTtlSec = sanitizePositiveInteger(rawConfig.catalogRefreshTtlSec)

  return {
    enabled: typeof entry?.enabled === 'boolean' ? entry.enabled : true,
    config: {
      baseUrl,
      integrationSecret,
      requestTimeoutMs,
      catalogRefreshTtlSec,
    },
  }
}

export function normalizePluginRuntimeConfig(rawConfig = {}) {
  return {
    baseUrl: normalizeString(rawConfig.baseUrl),
    integrationSecret: normalizeString(rawConfig.integrationSecret),
    requestTimeoutMs: sanitizePositiveInteger(rawConfig.requestTimeoutMs) ?? DEFAULT_REQUEST_TIMEOUT_MS,
    catalogRefreshTtlSec: sanitizePositiveInteger(rawConfig.catalogRefreshTtlSec) ?? DEFAULT_CATALOG_REFRESH_TTL_SEC,
  }
}

export function upsertPluginEntryConfig(config, options = {}) {
  const nextConfig = config && typeof config === 'object' ? cloneJson(config) : {}
  const plugins = nextConfig.plugins && typeof nextConfig.plugins === 'object' ? cloneJson(nextConfig.plugins) : {}
  const entries = plugins.entries && typeof plugins.entries === 'object' ? cloneJson(plugins.entries) : {}
  const currentEntry = entries[PLUGIN_ID] && typeof entries[PLUGIN_ID] === 'object' ? cloneJson(entries[PLUGIN_ID]) : {}

  entries[PLUGIN_ID] = {
    ...currentEntry,
    enabled: options.enabled ?? currentEntry.enabled ?? true,
    config: normalizePluginRuntimeConfig(options.pluginConfig ?? currentEntry.config ?? {}),
  }

  plugins.entries = entries
  nextConfig.plugins = plugins
  return nextConfig
}

export function cleanupLegacyMindAtlasConfig(config) {
  const nextConfig = config && typeof config === 'object' ? cloneJson(config) : {}
  const cleanupMessages = []

  const plugins = nextConfig.plugins && typeof nextConfig.plugins === 'object' ? cloneJson(nextConfig.plugins) : null
  if (plugins && Array.isArray(plugins.allow)) {
    const normalizedPluginAllow = plugins.allow.filter((entry) => typeof entry === 'string' && entry.trim())
    const pluginEntry = plugins.entries?.[PLUGIN_ID]
    const pluginShouldBeAllowlisted = pluginEntry && (typeof pluginEntry.enabled !== 'boolean' || pluginEntry.enabled)

    if (pluginShouldBeAllowlisted && !normalizedPluginAllow.includes(PLUGIN_ID)) {
      normalizedPluginAllow.push(PLUGIN_ID)
      cleanupMessages.push(
        'Restoring `plugins.allow` entry for `openclaw-mindatlas` so the plugin stays enabled when plugin allowlist mode is active.',
      )
    }

    if (normalizedPluginAllow.length > 0) {
      plugins.allow = [...new Set(normalizedPluginAllow)]
    } else {
      delete plugins.allow
    }

    nextConfig.plugins = plugins
    removeIfEmptyObject(nextConfig, 'plugins')
  }

  const tools = nextConfig.tools && typeof nextConfig.tools === 'object' ? cloneJson(nextConfig.tools) : null
  if (tools) {
    const originalAllow = Array.isArray(tools.allow)
      ? tools.allow.filter((entry) => typeof entry === 'string' && entry.trim())
      : []
    const retainedToolAllow = originalAllow.filter((entry) => entry !== PLUGIN_ID && !entry.startsWith('mindatlas_'))
    if (retainedToolAllow.length !== originalAllow.length) {
      cleanupMessages.push('Removing legacy MindAtlas entries from `tools.allow` so OpenClaw can rely on SDK tool registration.')
      if (retainedToolAllow.length > 0) {
        tools.allow = retainedToolAllow
      } else {
        delete tools.allow
      }
    }

    const remainingKeys = Object.keys(tools).filter((key) => key !== 'profile')
    if (remainingKeys.length === 0 && typeof tools.profile === 'string' && tools.profile.trim()) {
      cleanupMessages.push('Removing legacy `tools.profile` because the MindAtlas-specific compatibility allowlist has been cleared.')
      delete tools.profile
    }

    if (Object.keys(tools).length > 0) {
      nextConfig.tools = tools
    } else {
      delete nextConfig.tools
    }
  }

  return {
    config: nextConfig,
    cleanupMessages,
  }
}

function ensurePluginAllowlistForManagedConfig(config, options = {}) {
  const nextConfig = config && typeof config === 'object' ? cloneJson(config) : {}
  const plugins = nextConfig.plugins && typeof nextConfig.plugins === 'object' ? cloneJson(nextConfig.plugins) : null
  const currentAllow = Array.isArray(plugins?.allow)
    ? plugins.allow.filter((entry) => typeof entry === 'string' && entry.trim())
    : null

  if (!plugins || !currentAllow || currentAllow.length === 0) {
    return { config: nextConfig, changed: false }
  }

  const enabled = options.enabled ?? true
  if (!enabled || currentAllow.includes(PLUGIN_ID)) {
    return { config: nextConfig, changed: false }
  }

  plugins.allow = [...currentAllow, PLUGIN_ID]
  nextConfig.plugins = plugins
  return { config: nextConfig, changed: true }
}

function stripMindAtlasPluginState(config) {
  const nextConfig = config && typeof config === 'object' ? cloneJson(config) : {}
  const plugins = nextConfig.plugins && typeof nextConfig.plugins === 'object' ? cloneJson(nextConfig.plugins) : null
  if (!plugins) {
    return { config: nextConfig, changed: false }
  }

  let changed = false

  if (plugins.entries && typeof plugins.entries === 'object' && plugins.entries[PLUGIN_ID]) {
    const entries = cloneJson(plugins.entries)
    delete entries[PLUGIN_ID]
    plugins.entries = entries
    removeIfEmptyObject(plugins, 'entries')
    changed = true
  }

  if (plugins.installs && typeof plugins.installs === 'object' && plugins.installs[PLUGIN_ID]) {
    const installs = cloneJson(plugins.installs)
    delete installs[PLUGIN_ID]
    plugins.installs = installs
    removeIfEmptyObject(plugins, 'installs')
    changed = true
  }

  if (Array.isArray(plugins.allow)) {
    const allow = plugins.allow.filter((entry) => typeof entry === 'string' && entry.trim() && entry !== PLUGIN_ID)
    if (allow.length !== plugins.allow.length) {
      if (allow.length > 0) {
        plugins.allow = allow
      } else {
        delete plugins.allow
      }
      changed = true
    }
  }

  nextConfig.plugins = plugins
  removeIfEmptyObject(nextConfig, 'plugins')
  return { config: nextConfig, changed }
}

export function backupConflictingSkills(options = {}) {
  const env = options.env ?? process.env
  const homeDir = options.homeDir ?? os.homedir()
  const skillsRoot = path.resolve(options.skillsRoot ?? path.join(resolveOpenClawRoot({ env, homeDir }), 'skills'))
  const conflictingSkillIds = (options.skillIds ?? MINDATLAS_SKILL_IDS).filter((skillId) =>
    fs.existsSync(path.join(skillsRoot, skillId)),
  )

  if (conflictingSkillIds.length === 0) {
    return {
      skillsRoot,
      backupDir: null,
      movedSkillIds: [],
    }
  }

  const timestamp = typeof options.timestamp === 'string' && options.timestamp.trim()
    ? options.timestamp.trim()
    : formatTimestamp(options.now instanceof Date ? options.now : new Date())
  const backupDir = path.resolve(options.backupDir ?? path.join(path.dirname(skillsRoot), `skills-backup-${timestamp}`))
  fs.mkdirSync(backupDir, { recursive: true })

  for (const skillId of conflictingSkillIds) {
    fs.renameSync(path.join(skillsRoot, skillId), path.join(backupDir, skillId))
  }

  return {
    skillsRoot,
    backupDir,
    movedSkillIds: conflictingSkillIds,
  }
}

export function configureOpenClawSkills(options = {}) {
  const env = options.env ?? process.env
  const homeDir = options.homeDir ?? os.homedir()
  const configPath = path.resolve(options.configPath ?? resolveOpenClawConfigPath({ env, homeDir }))
  const configDir = path.dirname(configPath)
  const config = readJsonFile(configPath, {})
  const configuredPluginRoot = path.resolve(options.pluginRoot ?? resolveInstalledPluginRoot(config, { env, homeDir }))
  const configuredSkillsDir = path.resolve(options.skillsDir ?? path.join(configuredPluginRoot, 'skills'))
  const fallbackSkillsDir = path.join(LOCAL_PLUGIN_ROOT, 'skills')
  const pluginRoot = fs.existsSync(configuredSkillsDir) ? configuredPluginRoot : LOCAL_PLUGIN_ROOT
  const skillsDir = fs.existsSync(configuredSkillsDir) ? configuredSkillsDir : fallbackSkillsDir

  if (!fs.existsSync(skillsDir)) {
    throw new Error(`MindAtlas plugin skills directory was not found: ${skillsDir}`)
  }

  const warnings = detectLegacyMindAtlasToolPolicy(config)
  const nextConfig = ensureSkillsExtraDir(config, skillsDir)
  fs.mkdirSync(configDir, { recursive: true })
  fs.writeFileSync(configPath, `${JSON.stringify(nextConfig, null, 2)}\n`, 'utf8')

  return {
    configPath,
    pluginRoot,
    skillsDir,
    extraDirs: nextConfig.skills.load.extraDirs,
    warnings,
  }
}

function createCommandRunner() {
  return (command, args, options = {}) =>
    spawnSync(command, args, {
      encoding: 'utf8',
      stdio: 'pipe',
      ...options,
    })
}

export function runCommand(command, args, options = {}) {
  const logger = options.logger ?? createDefaultLogger()
  const runner = options.runner ?? createCommandRunner()
  logger.info(`$ ${formatCommand(command, args)}`)
  const result = runner(command, args, {
    cwd: options.cwd,
    env: options.env,
  })

  const stdout = typeof result.stdout === 'string' ? result.stdout.trimEnd() : ''
  const stderr = typeof result.stderr === 'string' ? result.stderr.trimEnd() : ''
  const status = typeof result.status === 'number' ? result.status : result.error ? 1 : 0

  if (stdout) {
    logger.info(stdout)
  }
  if (stderr) {
    if (status === 0) {
      logger.info(stderr)
    } else {
      logger.warn(stderr)
    }
  }

  return {
    status,
    stdout,
    stderr,
  }
}

function assertCommandSucceeded(result, errorMessage) {
  if (result.status === 0) {
    return
  }

  const detail = result.stderr || result.stdout
  throw new Error(detail ? `${errorMessage}\n${detail}` : errorMessage)
}

function createConsolePrompter(options = {}) {
  const rl = readline.createInterface({
    input: options.input ?? process.stdin,
    output: options.output ?? process.stdout,
  })

  return {
    async ask(question) {
      const answer = await rl.question(question)
      return answer.trim()
    },
    async close() {
      await rl.close()
    },
  }
}

async function promptRequiredStringField(options) {
  const prompt = options.prompt
  const logger = options.logger
  const label = options.label
  const existingValue = normalizeString(options.existingValue)
  const alwaysAsk = Boolean(options.alwaysAsk)
  const fallbackDisplay = options.fallbackDisplay ? ` [default: ${options.fallbackDisplay}]` : ''

  if (!alwaysAsk && existingValue) {
    return existingValue
  }

  while (true) {
    const currentDisplay =
      !alwaysAsk && existingValue
        ? ` [current: ${options.maskCurrent ? maskSecret(existingValue) : existingValue}]`
        : ''
    const answer = normalizeString(await prompt.ask(`${label}${currentDisplay}${fallbackDisplay}: `))
    if (answer) {
      if (options.validate && !options.validate(answer)) {
        logger.warn(options.invalidMessage)
        continue
      }
      return answer
    }

    if (!alwaysAsk && existingValue) {
      return existingValue
    }

    if (options.fallbackValue !== undefined) {
      return options.fallbackValue
    }

    logger.warn(options.emptyMessage)
  }
}

async function collectPluginRuntimeConfig(options = {}) {
  const logger = options.logger ?? createDefaultLogger()
  const prompt = options.prompt ?? createConsolePrompter(options)
  const existingState = readPluginEntryState({ plugins: { entries: { [PLUGIN_ID]: { config: options.existingConfig } } } }).config
  const mode = options.mode ?? 'setup'
  const alwaysAskAllFields = mode === 'setup'

  try {
    const baseUrl = await promptRequiredStringField({
      prompt,
      logger,
      label: 'MindAtlas base URL',
      existingValue: alwaysAskAllFields ? '' : existingState.baseUrl,
      alwaysAsk: alwaysAskAllFields,
      validate: isValidHttpUrl,
      invalidMessage: 'Please enter a valid http:// or https:// base URL.',
      emptyMessage: 'MindAtlas base URL is required.',
    })
    const integrationSecret = await promptRequiredStringField({
      prompt,
      logger,
      label: 'MindAtlas integration secret',
      existingValue: alwaysAskAllFields ? '' : existingState.integrationSecret,
      alwaysAsk: alwaysAskAllFields,
      maskCurrent: true,
      emptyMessage: 'MindAtlas integration secret is required.',
    })
    const requestTimeoutMsRaw = await promptRequiredStringField({
      prompt,
      logger,
      label: 'requestTimeoutMs',
      existingValue:
        !alwaysAskAllFields && existingState.requestTimeoutMs
          ? String(existingState.requestTimeoutMs)
          : '',
      alwaysAsk: alwaysAskAllFields,
      fallbackValue: String(DEFAULT_REQUEST_TIMEOUT_MS),
      fallbackDisplay: String(DEFAULT_REQUEST_TIMEOUT_MS),
      validate: (value) => sanitizePositiveInteger(value) !== null,
      invalidMessage: 'requestTimeoutMs must be a positive integer.',
      emptyMessage: 'requestTimeoutMs must be a positive integer.',
    })
    const catalogRefreshTtlSecRaw = await promptRequiredStringField({
      prompt,
      logger,
      label: 'catalogRefreshTtlSec',
      existingValue:
        !alwaysAskAllFields && existingState.catalogRefreshTtlSec
          ? String(existingState.catalogRefreshTtlSec)
          : '',
      alwaysAsk: alwaysAskAllFields,
      fallbackValue: String(DEFAULT_CATALOG_REFRESH_TTL_SEC),
      fallbackDisplay: String(DEFAULT_CATALOG_REFRESH_TTL_SEC),
      validate: (value) => sanitizePositiveInteger(value) !== null,
      invalidMessage: 'catalogRefreshTtlSec must be a positive integer.',
      emptyMessage: 'catalogRefreshTtlSec must be a positive integer.',
    })

    return normalizePluginRuntimeConfig({
      baseUrl,
      integrationSecret,
      requestTimeoutMs: requestTimeoutMsRaw,
      catalogRefreshTtlSec: catalogRefreshTtlSecRaw,
    })
  } finally {
    if (prompt.close) {
      await prompt.close()
    }
  }
}

function writeConfigWithCleanup(configPath, config, logger) {
  const { config: cleanedConfig, cleanupMessages } = cleanupLegacyMindAtlasConfig(config)
  for (const message of cleanupMessages) {
    logger.info(message)
  }
  writeJsonFile(configPath, cleanedConfig)
  return {
    config: cleanedConfig,
    cleanupMessages,
  }
}

function logBackupSummary(backupResult, logger) {
  if (backupResult.movedSkillIds.length === 0) {
    logger.info('No conflicting custom MindAtlas skill directories were found under the active OpenClaw skills root.')
    return
  }

  logger.info(
    `Backed up conflicting MindAtlas custom skills to ${backupResult.backupDir}: ${backupResult.movedSkillIds.join(', ')}`,
  )
}

function logConfigureSummary(result, logger) {
  logger.info(`Configured OpenClaw skills.load.extraDirs in ${result.configPath}`)
  logger.info(`MindAtlas plugin root: ${result.pluginRoot}`)
  logger.info(`MindAtlas skills dir: ${result.skillsDir}`)
  logger.info(`Registered extra skill dirs: ${result.extraDirs.join(', ')}`)
  for (const warning of result.warnings) {
    logger.warn(warning)
  }
}

export async function runSetupOpenClawPlugin(options = {}) {
  const logger = options.logger ?? createDefaultLogger(options)
  const env = options.env ?? process.env
  const homeDir = options.homeDir ?? os.homedir()
  const configPath = path.resolve(options.configPath ?? resolveOpenClawConfigPath({ env, homeDir }))

  assertCommandSucceeded(
    runCommand('openclaw', ['--version'], { env, logger, runner: options.runner }),
    'OpenClaw CLI was not found. Install the OpenClaw CLI on this host before running setup:openclaw.',
  )

  logger.info('Collecting OpenClaw MindAtlas plugin configuration...')
  const pluginConfig = await collectPluginRuntimeConfig({
    ...options,
    logger,
    mode: 'setup',
  })

  const installResult = runCommand('openclaw', ['plugins', 'install', LOCAL_PLUGIN_ROOT], {
    env,
    logger,
    runner: options.runner,
  })
  if (installResult.status !== 0 && /plugin already exists/i.test(`${installResult.stderr}\n${installResult.stdout}`)) {
    throw new Error(
      'OpenClaw reports that the MindAtlas plugin is already installed. Use `npm --prefix ./integrations/openclaw-mindatlas run update:openclaw` for upgrades or reinstallation.',
    )
  }
  assertCommandSucceeded(
    installResult,
    'Failed to install the local `openclaw-mindatlas` plugin package.',
  )

  const existingConfig = readJsonFile(configPath, {})
  const nextConfig = upsertPluginEntryConfig(existingConfig, {
    enabled: true,
    pluginConfig,
  })
  writeConfigWithCleanup(configPath, nextConfig, logger)

  const backupResult = backupConflictingSkills({ env, homeDir, ...options })
  logBackupSummary(backupResult, logger)

  const configureResult = configureOpenClawSkills({ configPath, env, homeDir })
  logConfigureSummary(configureResult, logger)

  assertCommandSucceeded(
    runCommand('openclaw', ['gateway', 'restart'], { env, logger, runner: options.runner }),
    'Failed to restart the OpenClaw Gateway.',
  )

  logger.info('')
  logger.info('OpenClaw MindAtlas setup completed.')
  logger.info(`Config path: ${configPath}`)
  logger.info(`Plugin root: ${LOCAL_PLUGIN_ROOT}`)
  logger.info('Open a brand-new OpenClaw session before validating the refreshed MindAtlas tool and skill surface.')

  return {
    configPath,
    pluginConfig,
    backupResult,
    configureResult,
  }
}

export async function runUpdateOpenClawPlugin(options = {}) {
  const logger = options.logger ?? createDefaultLogger(options)
  const env = options.env ?? process.env
  const homeDir = options.homeDir ?? os.homedir()
  const configPath = path.resolve(options.configPath ?? resolveOpenClawConfigPath({ env, homeDir }))
  const initialConfig = readJsonFile(configPath, {})
  const existingEntry = readPluginEntryState(initialConfig)
  const installPath = resolveInstalledPluginRoot(initialConfig, { env, homeDir })

  assertCommandSucceeded(
    runCommand('openclaw', ['--version'], { env, logger, runner: options.runner }),
    'OpenClaw CLI was not found. Install the OpenClaw CLI on this host before running update:openclaw.',
  )

  logger.info(
    `Reusing existing OpenClaw MindAtlas config when available: baseUrl=${existingEntry.config.baseUrl || '(missing)'}, integrationSecret=${maskSecret(existingEntry.config.integrationSecret)}, requestTimeoutMs=${existingEntry.config.requestTimeoutMs ?? '(missing)'}, catalogRefreshTtlSec=${existingEntry.config.catalogRefreshTtlSec ?? '(missing)'}`,
  )

  const pluginConfig = await collectPluginRuntimeConfig({
    ...options,
    logger,
    mode: 'update',
    existingConfig: existingEntry.config,
  })

  const preparedConfig = ensurePluginAllowlistForManagedConfig(initialConfig, {
    enabled: existingEntry.enabled,
  })
  if (preparedConfig.changed) {
    logger.info(
      'Temporarily restoring `plugins.allow` for `openclaw-mindatlas` before uninstall so OpenClaw can manage the plugin cleanly under plugin allowlist mode.',
    )
    writeJsonFile(configPath, preparedConfig.config)
  }

  const uninstallResult = runCommand('openclaw', ['plugins', 'uninstall', PLUGIN_ID, '--force'], {
    env,
    logger,
    runner: options.runner,
  })
  if (uninstallResult.status !== 0) {
    logger.warn('OpenClaw uninstall did not succeed cleanly; the script will continue with a manual install-path cleanup fallback.')
  }

  if (uninstallResult.status !== 0 || fs.existsSync(installPath)) {
    logger.info(`Removing lingering MindAtlas plugin install path: ${installPath}`)
    fs.rmSync(installPath, { recursive: true, force: true })
  }

  const postUninstallConfig = readJsonFile(configPath, {})
  const strippedConfig = stripMindAtlasPluginState(postUninstallConfig)
  if (strippedConfig.changed) {
    logger.info(
      'Removing stale MindAtlas plugin config remnants before reinstall so OpenClaw does not treat them as unknown during installation.',
    )
    writeJsonFile(configPath, strippedConfig.config)
  }

  assertCommandSucceeded(
    runCommand('openclaw', ['plugins', 'install', LOCAL_PLUGIN_ROOT], {
      env,
      logger,
      runner: options.runner,
    }),
    'Failed to reinstall the local `openclaw-mindatlas` plugin package.',
  )

  const postInstallConfig = readJsonFile(configPath, {})
  const restoredConfig = upsertPluginEntryConfig(postInstallConfig, {
    enabled: existingEntry.enabled,
    pluginConfig,
  })
  writeConfigWithCleanup(configPath, restoredConfig, logger)

  const backupResult = backupConflictingSkills({ env, homeDir, ...options })
  logBackupSummary(backupResult, logger)

  const configureResult = configureOpenClawSkills({ configPath, env, homeDir })
  logConfigureSummary(configureResult, logger)

  assertCommandSucceeded(
    runCommand('openclaw', ['gateway', 'restart'], { env, logger, runner: options.runner }),
    'Failed to restart the OpenClaw Gateway.',
  )

  logger.info('')
  logger.info('OpenClaw MindAtlas update completed.')
  logger.info(`Config path: ${configPath}`)
  logger.info(`Plugin root: ${LOCAL_PLUGIN_ROOT}`)
  logger.info('Open a brand-new OpenClaw session before validating the refreshed MindAtlas tool and skill surface.')

  return {
    configPath,
    pluginConfig,
    installPath,
    backupResult,
    configureResult,
  }
}
