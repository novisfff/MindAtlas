import { runUpdateOpenClawPlugin } from './openclaw-plugin-management.mjs'

try {
  await runUpdateOpenClawPlugin()
} catch (error) {
  const message = error instanceof Error ? error.message : 'Failed to update the OpenClaw MindAtlas plugin.'
  console.error(message)
  process.exitCode = 1
}
