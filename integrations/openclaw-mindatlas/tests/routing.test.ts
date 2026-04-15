import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type { MindAtlasRuntimeCapability } from '../src/catalog'
import { buildToolDescription } from '../src/tools'

function createCapability(overrides: Partial<MindAtlasRuntimeCapability>): MindAtlasRuntimeCapability {
  return {
    capabilityKey: 'search_entries',
    toolName: 'mindatlas_search_entries',
    title: 'Search Previous Records',
    description: 'Search previously stored MindAtlas records.',
    sourceType: 'tool',
    implementationType: 'entry',
    available: true,
    availabilityReason: null,
    inputSummary: 'keyword plus optional filters',
    outputSummary: 'matching records',
    inputSchema: {
      type: 'object',
      properties: {},
      required: [],
      additionalProperties: false,
    },
    outputSchema: {
      type: 'object',
      properties: {},
      required: [],
      additionalProperties: false,
    },
    toolResponseMode: 'json_schema',
    ...overrides,
  }
}

function readSkill(skillId: string): string {
  const skillPath = fileURLToPath(new URL(`../skills/${skillId}/SKILL.md`, import.meta.url))
  return fs.readFileSync(skillPath, 'utf8')
}

function readReadme(): string {
  const readmePath = fileURLToPath(new URL('../README.md', import.meta.url))
  return fs.readFileSync(readmePath, 'utf8')
}

test('buildToolDescription includes routing hints for search and periodic review capabilities', () => {
  const captureDescription = buildToolDescription(
    createCapability({
      capabilityKey: 'submit_context_capture',
      toolName: 'mindatlas_submit_context_capture',
      title: 'Smart Save To MindAtlas',
      description: 'Submit one high-value context block to MindAtlas.',
      inputSummary: 'context (string)',
      outputSummary: 'created/merged result',
    }),
  )
  const searchDescription = buildToolDescription(
    createCapability({
      capabilityKey: 'search_entries',
      toolName: 'mindatlas_search_entries',
    }),
  )
  const reviewDescription = buildToolDescription(
    createCapability({
      capabilityKey: 'generate_periodic_review',
      toolName: 'mindatlas_generate_periodic_review',
      title: 'Generate Periodic Review',
      description: 'Review MindAtlas records across any time range.',
      sourceType: 'workflow',
      implementationType: 'workflow',
    }),
  )

  assert.match(captureDescription, /high-value context block/i)
  assert.match(captureDescription, /source, channel, session, and tool context automatically/i)
  assert.match(captureDescription, /Input: context \(string\)/i)
  assert.match(searchDescription, /did I record this before/i)
  assert.match(searchDescription, /time-bounded searches/i)
  assert.match(reviewDescription, /what did I do last week/i)
  assert.match(reviewDescription, /show this month's tag distribution/i)
})

test('overview skill is the main router and requires session-visible mindatlas tools', () => {
  const content = readSkill('mindatlas-overview')

  assert.match(content, /Treat this as the main router skill for MindAtlas\./)
  assert.match(content, /Start from the current session's visible `mindatlas_\*` tools/)
  assert.match(content, /Do not assume MindAtlas only exposes a fixed built-in tool list/)
  assert.match(content, /mindatlas_list_capabilities/)
  assert.match(content, /mindatlas_run_capability/)
  assert.match(content, /re-run the plugin install path, re-run `configure:skills`, restart the OpenClaw Gateway, and open a brand-new session/i)
})

test('dispatcher, summary, retrieval, and auto-capture skills define narrower boundaries', () => {
  const dispatcher = readSkill('mindatlas-dispatcher')
  const summary = readSkill('mindatlas-summary')
  const retrieval = readSkill('mindatlas-retrieval')
  const autoCapture = readSkill('mindatlas-auto-capture')

  assert.match(dispatcher, /dynamic capability discovery/i)
  assert.match(dispatcher, /mindatlas_list_capabilities/)
  assert.match(dispatcher, /mindatlas_run_capability/)
  assert.match(dispatcher, /does not replace plugin or shipped-skill reinstall after upgrades/i)
  assert.match(summary, /This skill is only for summary, recap, report routing/)
  assert.match(summary, /mindatlas_generate_periodic_review/)
  assert.match(summary, /custom summary path/i)
  assert.match(summary, /Do not abandon MindAtlas just because a dedicated report tool is absent/)
  assert.match(summary, /restarting the Gateway and opening a new session/i)
  assert.match(retrieval, /It is not the broad default router for every history-related request\./)
  assert.match(retrieval, /custom exposed retrieval workflow or agent/i)
  assert.match(retrieval, /rerunning `configure:skills`/i)
  assert.match(autoCapture, /This skill is only for capture, create, store, and durable-memory submission/)
  assert.match(autoCapture, /Prefer `mindatlas_submit_context_capture` or another visible dedicated capture path/i)
  assert.match(autoCapture, /submit one high-value `context` block/i)
  assert.match(autoCapture, /OpenClaw provides that request metadata automatically/i)
  assert.match(autoCapture, /route back through overview\/dispatcher/i)
})

test('README documents reinstall and new-session guidance for plugin and shipped skill upgrades', () => {
  const readme = readReadme()

  assert.match(readme, /Bundles 5 shipped MindAtlas skills/i)
  assert.match(readme, /## Upgrade Or Reinstall/)
  assert.match(readme, /upgraded the MindAtlas repository or deployed a newer MindAtlas system version/i)
  assert.match(readme, /Prefer `openclaw plugins update openclaw-mindatlas`/i)
  assert.match(readme, /npm --prefix \.\/integrations\/openclaw-mindatlas run configure:skills/)
  assert.match(readme, /plugin already exists/i)
  assert.match(readme, /not plugin-managed/i)
  assert.match(readme, /tools\.allow/i)
  assert.match(readme, /Restart the OpenClaw Gateway/i)
  assert.match(readme, /Open a brand-new OpenClaw session/i)
})
