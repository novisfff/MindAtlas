import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { PLUGIN_ID } from './config'

export const BUNDLED_SKILL_IDS = [
  'mindatlas-overview',
  'mindatlas-auto-capture',
  'mindatlas-retrieval',
  'mindatlas-summary',
] as const

export const MANAGED_SKILL_MARKER_FILE = '.openclaw-mindatlas-managed.json'

interface ManagedSkillMarker {
  pluginId: string
  markerVersion: number
}

export interface SkillSyncOptions {
  env?: NodeJS.ProcessEnv
  homeDir?: string
  sourceRootDir?: string
  managedRootDir?: string
}

export interface SkillSyncResult {
  sourceRootDir: string
  managedRootDir: string
  syncedSkillIds: string[]
  skippedSkillIds: string[]
  warnings: string[]
}

function normalizeEnvPath(value: string | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function resolveManagedSkillsRoot(options: Pick<SkillSyncOptions, 'env' | 'homeDir'> = {}): string {
  const env = options.env ?? process.env
  const configPath = normalizeEnvPath(env.OPENCLAW_CONFIG_PATH)
  if (configPath) {
    return path.resolve(path.dirname(configPath), 'skills')
  }

  const stateDir = normalizeEnvPath(env.OPENCLAW_STATE_DIR)
  if (stateDir) {
    return path.resolve(stateDir, 'skills')
  }

  return path.resolve(options.homeDir ?? os.homedir(), '.openclaw', 'skills')
}

export function resolveBundledSkillsRoot(): string {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'skills')
}

function readManagedSkillMarker(skillDir: string): ManagedSkillMarker | null {
  const markerPath = path.join(skillDir, MANAGED_SKILL_MARKER_FILE)
  if (!fs.existsSync(markerPath)) {
    return null
  }

  try {
    const parsed = JSON.parse(fs.readFileSync(markerPath, 'utf8')) as ManagedSkillMarker
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function clearManagedSkillDir(skillDir: string) {
  for (const entry of fs.readdirSync(skillDir)) {
    if (entry === MANAGED_SKILL_MARKER_FILE) {
      continue
    }
    fs.rmSync(path.join(skillDir, entry), { recursive: true, force: true })
  }
}

function copyDirectory(sourceDir: string, targetDir: string) {
  fs.mkdirSync(targetDir, { recursive: true })

  for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
    const sourcePath = path.join(sourceDir, entry.name)
    const targetPath = path.join(targetDir, entry.name)

    if (entry.isDirectory()) {
      copyDirectory(sourcePath, targetPath)
      continue
    }

    if (entry.isFile()) {
      fs.copyFileSync(sourcePath, targetPath)
    }
  }
}

function writeManagedSkillMarker(skillDir: string) {
  fs.writeFileSync(
    path.join(skillDir, MANAGED_SKILL_MARKER_FILE),
    `${JSON.stringify({ pluginId: PLUGIN_ID, markerVersion: 1 }, null, 2)}\n`,
    'utf8',
  )
}

export function syncBundledSkills(options: SkillSyncOptions = {}): SkillSyncResult {
  const sourceRootDir = path.resolve(options.sourceRootDir ?? resolveBundledSkillsRoot())
  const managedRootDir = path.resolve(options.managedRootDir ?? resolveManagedSkillsRoot(options))
  const result: SkillSyncResult = {
    sourceRootDir,
    managedRootDir,
    syncedSkillIds: [],
    skippedSkillIds: [],
    warnings: [],
  }

  if (!fs.existsSync(sourceRootDir)) {
    result.warnings.push(`Bundled MindAtlas skills directory was not found: ${sourceRootDir}`)
    return result
  }

  fs.mkdirSync(managedRootDir, { recursive: true })

  for (const skillId of BUNDLED_SKILL_IDS) {
    const sourceDir = path.join(sourceRootDir, skillId)
    const sourceSkillFile = path.join(sourceDir, 'SKILL.md')
    if (!fs.existsSync(sourceSkillFile)) {
      result.skippedSkillIds.push(skillId)
      result.warnings.push(`Bundled MindAtlas skill is missing its SKILL.md asset: ${sourceSkillFile}`)
      continue
    }

    const targetDir = path.join(managedRootDir, skillId)
    if (fs.existsSync(targetDir)) {
      const stat = fs.statSync(targetDir)
      if (!stat.isDirectory()) {
        result.skippedSkillIds.push(skillId)
        result.warnings.push(`Skipping shipped MindAtlas skill because the target path is not a directory: ${targetDir}`)
        continue
      }

      const marker = readManagedSkillMarker(targetDir)
      if (!marker || marker.pluginId !== PLUGIN_ID) {
        result.skippedSkillIds.push(skillId)
        result.warnings.push(`Skipping shipped MindAtlas skill because an existing custom skill with the same id is not plugin-managed: ${targetDir}`)
        continue
      }

      clearManagedSkillDir(targetDir)
    } else {
      fs.mkdirSync(targetDir, { recursive: true })
    }

    copyDirectory(sourceDir, targetDir)
    writeManagedSkillMarker(targetDir)
    result.syncedSkillIds.push(skillId)
  }

  return result
}
