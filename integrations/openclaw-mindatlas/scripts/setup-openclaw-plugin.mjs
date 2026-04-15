import { runSetupOpenClawPlugin } from './openclaw-plugin-management.mjs'

try {
  await runSetupOpenClawPlugin()
} catch (error) {
  const message = error instanceof Error ? error.message : 'Failed to set up the OpenClaw MindAtlas plugin.'
  console.error(message)
  process.exitCode = 1
}
