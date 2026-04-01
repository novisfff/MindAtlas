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

test('buildToolDescription includes routing hints for search and weekly report capabilities', () => {
  const searchDescription = buildToolDescription(
    createCapability({
      capabilityKey: 'search_entries',
      toolName: 'mindatlas_search_entries',
    }),
  )
  const weeklyDescription = buildToolDescription(
    createCapability({
      capabilityKey: 'generate_weekly_report',
      toolName: 'mindatlas_generate_weekly_report',
      title: 'Generate Weekly Report',
      description: 'Generate or return the weekly report.',
      implementationType: 'report',
    }),
  )

  assert.match(searchDescription, /did I record this before/i)
  assert.match(searchDescription, /time-bounded searches/i)
  assert.match(weeklyDescription, /what did I do this week/i)
  assert.match(weeklyDescription, /recently/i)
})

test('overview skill is the main router and requires session-visible mindatlas tools', () => {
  const content = readSkill('mindatlas-overview')

  assert.match(content, /Treat this as the main router skill for MindAtlas\./)
  assert.match(content, /Start from the current session's visible `mindatlas_\*` tools/)
  assert.match(content, /MindAtlas capabilities are not exposed in this session/)
})

test('summary, retrieval, and auto-capture skills define narrower boundaries', () => {
  const summary = readSkill('mindatlas-summary')
  const retrieval = readSkill('mindatlas-retrieval')
  const autoCapture = readSkill('mindatlas-auto-capture')

  assert.match(summary, /This skill is only for summary, recap, report routing/)
  assert.match(summary, /Do not abandon MindAtlas just because a dedicated report tool is absent/)
  assert.match(retrieval, /It is not the broad default router for every history-related request\./)
  assert.match(retrieval, /If the request is primarily a recap, review, digest/)
  assert.match(autoCapture, /This skill is only for capture, create, store, and durable-memory submission/)
  assert.match(autoCapture, /Prefer `mindatlas_submit_context_capture` when it is visible/)
})
