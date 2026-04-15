import { fileURLToPath } from 'node:url'
import path from 'node:path'

export {
  configureOpenClawSkills,
  detectLegacyMindAtlasToolPolicy,
  ensureSkillsExtraDir,
  resolveInstalledPluginRoot,
  resolveOpenClawConfigPath,
  resolveOpenClawRoot,
} from './openclaw-plugin-management.mjs'

import { configureOpenClawSkills } from './openclaw-plugin-management.mjs'

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
