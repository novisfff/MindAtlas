import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import pluginEntry, { OpenClawMindAtlasPluginRuntime, PLUGIN_ID, registerMindAtlasPlugin } from '../src/index'
import { describePluginConfigIssue, extractPluginEntryConfig, resolvePluginConfig, validatePluginConfig } from '../src/config'
import { BUNDLED_SKILL_IDS, MANAGED_SKILL_MARKER_FILE, resolveManagedSkillsRoot, syncBundledSkills } from '../src/skills'

const TEST_OPENCLAW_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'openclaw-mindatlas-tests-'))

test.after(() => {
  fs.rmSync(TEST_OPENCLAW_ROOT, { recursive: true, force: true })
})

interface MockTool {
  name: string
  description: string
  parameters: Record<string, unknown>
  execute: (id: string, params: Record<string, unknown>, context?: Record<string, unknown>) => Promise<{
    content: Array<{ type: 'text'; text: string }>
  }>
}

interface MockService {
  id: string
  start?: (context?: unknown) => void | Promise<void>
  stop?: (context?: unknown) => void | Promise<void>
}

function createResponse(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
    ...init,
  })
}

function createPluginConfig() {
  return {
    plugins: {
      entries: {
        [PLUGIN_ID]: {
          config: {
            baseUrl: 'http://127.0.0.1:8000',
            integrationSecret: 'secret-value',
            requestTimeoutMs: 3000,
            catalogRefreshTtlSec: 300,
          },
        },
      },
    },
  }
}

function createMockApi(config: unknown = createPluginConfig()) {
  const tools: MockTool[] = []
  const services: MockService[] = []
  const logs: Array<{ level: string; message: string }> = []

  const api = {
    id: PLUGIN_ID,
    name: 'MindAtlas Capability Gateway',
    version: '0.1.0',
    source: 'tests',
    registrationMode: 'full',
    config,
    pluginConfig: extractPluginEntryConfig(config),
    runtime: {},
    logger: {
      debug(message: string) {
        logs.push({ level: 'debug', message })
      },
      info(message: string) {
        logs.push({ level: 'info', message })
      },
      warn(message: string) {
        logs.push({ level: 'warn', message })
      },
      error(message: string) {
        logs.push({ level: 'error', message })
      },
    },
    registerTool(tool: MockTool) {
      tools.push(tool)
    },
    registerService(service: MockService) {
      services.push(service)
    },
    registerHook() {},
    registerHttpRoute() {},
    registerChannel() {},
    registerGatewayMethod() {},
    registerCli() {},
    registerCliBackend() {},
    registerProvider() {},
    registerSpeechProvider() {},
    registerMediaUnderstandingProvider() {},
    registerImageGenerationProvider() {},
    registerWebSearchProvider() {},
    registerInteractiveHandler() {},
    onConversationBindingResolved() {},
    registerCommand() {},
    registerContextEngine() {},
    registerMemoryPromptSection() {},
    registerMemoryFlushPlan() {},
    registerMemoryRuntime() {},
    registerMemoryEmbeddingProvider() {},
    resolvePath(input: string) {
      return input
    },
    on() {},
  }

  return {
    api,
    tools,
    services,
    logs,
  }
}

function createServiceContext() {
  return {
    config: createPluginConfig(),
    stateDir: TEST_OPENCLAW_ROOT,
    logger: {
      info() {},
      warn() {},
      error() {},
    },
  }
}

function createBundledSkillSyncStub() {
  return () => ({
    sourceRootDir: path.join(TEST_OPENCLAW_ROOT, 'bundled-skills'),
    managedRootDir: path.join(TEST_OPENCLAW_ROOT, 'managed-skills'),
    syncedSkillIds: [],
    skippedSkillIds: [],
    warnings: [],
  })
}

test('plugin entry metadata matches the plugin manifest id', () => {
  assert.equal(pluginEntry.id, PLUGIN_ID)
  assert.equal(pluginEntry.name, 'MindAtlas Capability Gateway')
})

test('resolveManagedSkillsRoot prefers the active OpenClaw config directory', () => {
  assert.equal(
    resolveManagedSkillsRoot({
      env: {
        OPENCLAW_CONFIG_PATH: '/tmp/openclaw-profile/openclaw.json',
        OPENCLAW_STATE_DIR: '/tmp/openclaw-state',
      },
      homeDir: '/tmp/openclaw-home',
    }),
    '/tmp/openclaw-profile/skills',
  )
})

test('syncBundledSkills copies the shipped MindAtlas skills into the managed custom skill root', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-skill-sync-'))
  const sourceRootDir = fileURLToPath(new URL('../skills', import.meta.url))

  try {
    const result = syncBundledSkills({
      sourceRootDir,
      managedRootDir: path.join(tempRoot, 'skills'),
    })

    assert.deepEqual(result.syncedSkillIds, [...BUNDLED_SKILL_IDS])
    assert.deepEqual(result.skippedSkillIds, [])
    assert.deepEqual(result.warnings, [])

    for (const skillId of BUNDLED_SKILL_IDS) {
      const skillDir = path.join(result.managedRootDir, skillId)
      assert.equal(fs.existsSync(path.join(skillDir, 'SKILL.md')), true)
      assert.equal(fs.existsSync(path.join(skillDir, MANAGED_SKILL_MARKER_FILE)), true)
    }
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('syncBundledSkills does not overwrite a same-named user-owned custom skill', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-skill-conflict-'))
  const sourceRootDir = fileURLToPath(new URL('../skills', import.meta.url))
  const targetSkillDir = path.join(tempRoot, 'skills', 'mindatlas-overview')

  try {
    fs.mkdirSync(targetSkillDir, { recursive: true })
    fs.writeFileSync(path.join(targetSkillDir, 'SKILL.md'), 'user-owned skill\n', 'utf8')

    const result = syncBundledSkills({
      sourceRootDir,
      managedRootDir: path.join(tempRoot, 'skills'),
    })

    assert.equal(result.skippedSkillIds.includes('mindatlas-overview'), true)
    assert.match(result.warnings.join('\n'), /not plugin-managed/)
    assert.equal(fs.readFileSync(path.join(targetSkillDir, 'SKILL.md'), 'utf8'), 'user-owned skill\n')
    assert.equal(result.syncedSkillIds.includes('mindatlas-summary'), true)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('resolvePluginConfig reads nested plugin config and normalizes /api suffix', () => {
  const raw = {
    plugins: {
      entries: {
        [PLUGIN_ID]: {
          config: {
            baseUrl: 'https://mindatlas.example.com/api/',
            integrationSecret: 'top-secret',
          },
        },
      },
    },
  }

  assert.deepEqual(extractPluginEntryConfig(raw), {
    baseUrl: 'https://mindatlas.example.com/api/',
    integrationSecret: 'top-secret',
  })

  assert.deepEqual(resolvePluginConfig(raw), {
    baseUrl: 'https://mindatlas.example.com',
    integrationSecret: 'top-secret',
    requestTimeoutMs: 15000,
    catalogRefreshTtlSec: 300,
  })
})

test('plugin config can be absent during install and yields a friendly setup warning', () => {
  const raw = {
    plugins: {
      entries: {
        [PLUGIN_ID]: {
          enabled: true,
          config: {},
        },
      },
    },
  }

  assert.deepEqual(validatePluginConfig(raw), {
    missingFields: ['baseUrl', 'integrationSecret'],
  })
  assert.equal(resolvePluginConfig(raw), null)
  assert.match(
    describePluginConfigIssue({
      missingFields: ['baseUrl', 'integrationSecret'],
    }),
    /installed but not configured yet/,
  )
})

test('registerMindAtlasPlugin skips registration when install-time config is still missing', async () => {
  const { api, services, tools, logs } = createMockApi({
    plugins: {
      entries: {
        [PLUGIN_ID]: {
          enabled: true,
          config: {},
        },
      },
    },
  })

  await registerMindAtlasPlugin(api as never, {
    syncBundledSkills: createBundledSkillSyncStub(),
  })

  assert.equal(tools.length, 0)
  assert.equal(services.length, 0)
  assert.equal(logs.some((entry) => entry.level === 'warn' && /installed but not configured yet/.test(entry.message)), true)
})

test('plugin registers only available tools during the official register(api) phase', async () => {
  const { api, services, tools } = createMockApi()

  const originalFetch = globalThis.fetch
  globalThis.fetch = async () =>
    createResponse({
      success: true,
      code: 0,
      message: 'OK',
      data: {
        integrationName: 'MindAtlas',
        capabilities: [
          {
            capabilityKey: 'capture_entry',
            toolName: 'mindatlas_capture_entry',
            title: 'Capture Entry',
            description: 'Save a new entry',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'title (string)',
            outputSummary: 'id (string)',
            inputSchema: {
              type: 'object',
              properties: { title: { type: 'string' } },
              required: ['title'],
              additionalProperties: false,
            },
            outputSchema: {
              type: 'object',
              properties: { id: { type: 'string' } },
              required: ['id'],
              additionalProperties: false,
            },
            toolResponseMode: 'json_schema',
          },
          {
            capabilityKey: 'query_knowledge_graph',
            toolName: 'mindatlas_query_knowledge_graph',
            title: 'Knowledge Graph',
            description: 'Ask LightRAG',
            sourceType: 'tool',
            implementationType: 'knowledge_graph',
            available: false,
            availabilityReason: 'LightRAG is disabled',
            inputSummary: 'query (string)',
            outputSummary: 'answer (string)',
            inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
        ],
      },
    })

  try {
    await registerMindAtlasPlugin(api as never, {
      syncBundledSkills: createBundledSkillSyncStub(),
    })
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(tools.length, 1)
  assert.equal(tools[0].name, 'mindatlas_capture_entry')
  assert.equal(services.length, 1)
})

test('startup catalog failure does not register tools and tells operators to reload after fixing connectivity', async () => {
  const { api, services, tools, logs } = createMockApi()

  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => {
    throw new Error('connect ECONNREFUSED 127.0.0.1:8000')
  }

  try {
    await registerMindAtlasPlugin(api as never, {
      syncBundledSkills: createBundledSkillSyncStub(),
    })
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(tools.length, 0)
  assert.equal(services.length, 1)
  assert.equal(logs.some((entry) => entry.level === 'warn' && /reload the OpenClaw Gateway/i.test(entry.message)), true)
})

test('tool execution forwards params and returns textified result', async () => {
  const { api, services, tools } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api as never, {
    syncBundledSkills: createBundledSkillSyncStub(),
  })

  let calls = 0
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input, init) => {
    calls += 1
    if (calls === 1) {
      return createResponse({
        success: true,
        code: 0,
        message: 'OK',
        data: {
          integrationName: 'MindAtlas',
          capabilities: [
            {
              capabilityKey: 'capture_entry',
              toolName: 'mindatlas_capture_entry',
              title: 'Capture Entry',
              description: 'Save a new entry',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'title (string)',
              outputSummary: 'id (string)',
              inputSchema: {
                type: 'object',
                properties: { title: { type: 'string' } },
                required: ['title'],
                additionalProperties: false,
              },
              outputSchema: {
                type: 'object',
                properties: { id: { type: 'string' } },
                required: ['id'],
                additionalProperties: false,
              },
              toolResponseMode: 'json_schema',
            },
          ],
        },
      })
    }

    assert.equal(String(input), 'http://127.0.0.1:8000/api/integrations/openclaw/capabilities/capture_entry/execute')
    assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer secret-value')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Tool'], 'mindatlas_capture_entry')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Channel'], 'discord')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Session'], 'session-42')
    assert.deepEqual(JSON.parse(String(init?.body)), { title: 'hello' })

    return createResponse({
      success: true,
      code: 0,
      message: 'OK',
      data: {
        capabilityKey: 'capture_entry',
        toolName: 'mindatlas_capture_entry',
        result: {
          id: 'entry-1',
          title: 'hello',
        },
      },
    })
  }

  try {
    await runtime.register()
    await services[0].start?.(createServiceContext())
    const result = await tools[0].execute('tool-call-1', { title: 'hello' }, { channel: 'discord', session: 'session-42' })
    assert.equal(result.content[0]?.type, 'text')
    assert.match(result.content[0]?.text ?? '', /"id": "entry-1"/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('ttl refresh marks newly available tools as reload-required instead of late-registering them', async () => {
  const { api, tools, logs } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api as never, {
    syncBundledSkills: createBundledSkillSyncStub(),
  })

  let phase: 'initial' | 'refresh' = 'initial'
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => {
    if (phase === 'initial') {
      return createResponse({
        success: true,
        code: 0,
        message: 'OK',
        data: {
          integrationName: 'MindAtlas',
          capabilities: [
            {
              capabilityKey: 'capture_entry',
              toolName: 'mindatlas_capture_entry',
              title: 'Capture Entry',
              description: 'Save a new entry',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'title (string)',
              outputSummary: 'id (string)',
              inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
            {
              capabilityKey: 'generate_weekly_report',
              toolName: 'mindatlas_generate_weekly_report',
              title: 'Weekly Report',
              description: 'Generate a weekly recap',
              sourceType: 'tool',
              implementationType: 'report',
              available: false,
              availabilityReason: 'Warm-up pending',
              inputSummary: 'startDate (string)',
              outputSummary: 'summary (string)',
              inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
          ],
        },
      })
    }

    return createResponse({
      success: true,
      code: 0,
      message: 'OK',
      data: {
        integrationName: 'MindAtlas',
        capabilities: [
          {
            capabilityKey: 'capture_entry',
            toolName: 'mindatlas_capture_entry',
            title: 'Capture Entry',
            description: 'Save a new entry',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'title (string)',
            outputSummary: 'id (string)',
            inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
          {
            capabilityKey: 'generate_weekly_report',
            toolName: 'mindatlas_generate_weekly_report',
            title: 'Weekly Report',
            description: 'Generate a weekly recap',
            sourceType: 'tool',
            implementationType: 'report',
            available: true,
            availabilityReason: null,
            inputSummary: 'startDate (string)',
            outputSummary: 'summary (string)',
            inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
        ],
      },
    })
  }

  try {
    await runtime.register()
    assert.equal(tools.length, 1)

    phase = 'refresh'
    await runtime.refreshCatalog()

    assert.equal(tools.length, 1)
    assert.equal(runtime.getState().reloadRequired, true)
    assert.equal(logs.some((entry) => /does not late-register tools/i.test(entry.message)), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('metadata drift marks existing tools as stale and asks for reload', async () => {
  const { api, tools } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api as never, {
    syncBundledSkills: createBundledSkillSyncStub(),
  })

  let phase: 'initial' | 'drift' = 'initial'
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => {
    if (phase === 'initial') {
      return createResponse({
        success: true,
        code: 0,
        message: 'OK',
        data: {
          integrationName: 'MindAtlas',
          capabilities: [
            {
              capabilityKey: 'capture_entry',
              toolName: 'mindatlas_capture_entry',
              title: 'Capture Entry',
              description: 'Save a new entry',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'title (string)',
              outputSummary: 'id (string)',
              inputSchema: {
                type: 'object',
                properties: { title: { type: 'string' } },
                required: ['title'],
                additionalProperties: false,
              },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
          ],
        },
      })
    }

    return createResponse({
      success: true,
      code: 0,
      message: 'OK',
      data: {
        integrationName: 'MindAtlas',
        capabilities: [
          {
            capabilityKey: 'capture_entry',
            toolName: 'mindatlas_capture_entry',
            title: 'Capture Entry Updated',
            description: 'Save a new entry with tags',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'title (string), tags (array[string])',
            outputSummary: 'id (string)',
            inputSchema: {
              type: 'object',
              properties: {
                title: { type: 'string' },
                tags: { type: 'array', items: { type: 'string' } },
              },
              required: ['title'],
              additionalProperties: false,
            },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
        ],
      },
    })
  }

  try {
    await runtime.register()
    assert.equal(tools.length, 1)

    phase = 'drift'
    await runtime.refreshCatalog()

    await assert.rejects(
      () => tools[0].execute('tool-call-3', { title: 'hello' }),
      /Start a new session or reload the OpenClaw plugin or Gateway/,
    )

    const state = runtime.getState()
    assert.equal(state.reloadRequired, true)
    assert.equal(state.staleToolNames.has('mindatlas_capture_entry'), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})
