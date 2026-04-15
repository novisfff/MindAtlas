import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PLUGIN_ID = 'openclaw-mindatlas'
const LOCAL_PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function normalizeEnvPath(value) {
  return typeof value === 'string' ? value.trim() : ''
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

export function resolveInstalledPluginRoot(config, options = {}) {
  const installPath = config?.plugins?.installs?.[PLUGIN_ID]?.installPath
  if (typeof installPath === 'string' && installPath.trim()) {
    return path.resolve(installPath)
  }

  return path.resolve(resolveOpenClawRoot(options), 'extensions', PLUGIN_ID)
}

export function ensureSkillsExtraDir(config, extraDir) {
  const nextConfig = config && typeof config === 'object' ? { ...config } : {}
  const skills = nextConfig.skills && typeof nextConfig.skills === 'object' ? { ...nextConfig.skills } : {}
  const load =
    skills.load && typeof skills.load === 'object'
      ? { ...skills.load }
      : {}

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

function isExecutedDirectly() {
  if (!process.argv[1]) {
    return false
  }

  return path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))
}

if (isExecutedDirectly()) {
  try {
    const result = configureOpenClawSkills()
    console.log(`Configured OpenClaw skills.load.extraDirs in ${result.configPath}`)
    console.log(`MindAtlas plugin root: ${result.pluginRoot}`)
    console.log(`MindAtlas skills dir: ${result.skillsDir}`)
    console.log(`Registered extra skill dirs: ${result.extraDirs.join(', ')}`)
    for (const warning of result.warnings) {
      console.warn(warning)
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to configure OpenClaw skills.'
    console.error(message)
    process.exitCode = 1
  }
}
