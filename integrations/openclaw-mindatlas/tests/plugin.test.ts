import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import pluginEntry, { OpenClawMindAtlasPluginRuntime, PLUGIN_ID, registerMindAtlasPlugin } from '../src/index'
import { describePluginConfigIssue, extractPluginEntryConfig, resolvePluginConfig, validatePluginConfig } from '../src/config'
import { BUNDLED_SKILL_IDS, MANAGED_SKILL_MARKER_FILE, resolveManagedSkillsRoot, syncBundledSkills } from '../src/skills'
import { MINDATLAS_LIST_CAPABILITIES_TOOL_NAME, MINDATLAS_RUN_CAPABILITY_TOOL_NAME } from '../src/tools'

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

function findTool(tools: MockTool[], toolName: string): MockTool {
  const tool = tools.find((item) => item.name === toolName)
  assert.ok(tool, `Expected tool ${toolName} to be registered.`)
  return tool
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
    requestTimeoutMs: 300000,
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
            capabilityKey: 'submit_context_capture',
            toolName: 'mindatlas_submit_context_capture',
            title: 'Smart Save To MindAtlas',
            description: 'Submit one high-value context block for intelligent persistence.',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'context (string)',
            outputSummary: 'created/merged result',
            inputSchema: {
              type: 'object',
              properties: { context: { type: 'string' } },
              required: ['context'],
              additionalProperties: false,
            },
            outputSchema: {
              type: 'object',
              properties: { status: { type: 'string' }, entryId: { type: 'string' } },
              required: ['status', 'entryId'],
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

  assert.equal(tools.length, 3)
  assert.equal(tools.some((tool) => tool.name === MINDATLAS_LIST_CAPABILITIES_TOOL_NAME), true)
  assert.equal(tools.some((tool) => tool.name === MINDATLAS_RUN_CAPABILITY_TOOL_NAME), true)
  assert.equal(tools.some((tool) => tool.name === 'mindatlas_submit_context_capture'), true)
  assert.equal(services.length, 1)
})

test('startup catalog failure still registers dispatcher tools and tells operators to reload after fixing connectivity', async () => {
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

  assert.equal(tools.length, 2)
  assert.equal(tools.some((tool) => tool.name === MINDATLAS_LIST_CAPABILITIES_TOOL_NAME), true)
  assert.equal(tools.some((tool) => tool.name === MINDATLAS_RUN_CAPABILITY_TOOL_NAME), true)
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
              capabilityKey: 'submit_context_capture',
              toolName: 'mindatlas_submit_context_capture',
              title: 'Smart Save To MindAtlas',
              description: 'Submit one high-value context block for intelligent persistence.',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'context (string)',
              outputSummary: 'created/merged result',
              inputSchema: {
                type: 'object',
                properties: { context: { type: 'string' } },
                required: ['context'],
                additionalProperties: false,
              },
              outputSchema: {
                type: 'object',
                properties: { status: { type: 'string' }, entryId: { type: 'string' } },
                required: ['status', 'entryId'],
                additionalProperties: false,
              },
              toolResponseMode: 'json_schema',
            },
          ],
        },
      })
    }

    assert.equal(String(input), 'http://127.0.0.1:8000/api/integrations/openclaw/capabilities/submit_context_capture/execute')
    assert.equal((init?.headers as Record<string, string>).Authorization, 'Bearer secret-value')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Tool'], 'mindatlas_submit_context_capture')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Channel'], 'discord')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Session'], 'session-42')
    assert.deepEqual(JSON.parse(String(init?.body)), { context: 'hello' })

    return createResponse({
      success: true,
      code: 0,
      message: 'OK',
      data: {
        capabilityKey: 'submit_context_capture',
        toolName: 'mindatlas_submit_context_capture',
        result: {
          status: 'created',
          entryId: 'entry-1',
          entryTitle: 'hello',
        },
      },
    })
  }

  try {
    await runtime.register()
    await services[0].start?.(createServiceContext())
    const captureTool = findTool(tools, 'mindatlas_submit_context_capture')
    const result = await captureTool.execute('tool-call-1', { context: 'hello' }, { channel: 'discord', session: 'session-42' })
    assert.equal(result.content[0]?.type, 'text')
    assert.match(result.content[0]?.text ?? '', /"entryId": "entry-1"/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('mindatlas_list_capabilities refreshes catalog and marks whether a capability already has a dedicated tool', async () => {
  const { api, tools } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api as never, {
    syncBundledSkills: createBundledSkillSyncStub(),
  })

  let phase: 'initial' | 'list' = 'initial'
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input) => {
    assert.equal(String(input), 'http://127.0.0.1:8000/api/integrations/openclaw/capabilities')
    if (phase === 'initial') {
      return createResponse({
        success: true,
        code: 0,
        message: 'OK',
        data: {
          integrationName: 'MindAtlas',
          capabilities: [
            {
              capabilityKey: 'submit_context_capture',
              toolName: 'mindatlas_submit_context_capture',
              title: 'Smart Save To MindAtlas',
              description: 'Submit one high-value context block for intelligent persistence.',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'context (string)',
              outputSummary: 'created/merged result',
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
            capabilityKey: 'submit_context_capture',
            toolName: 'mindatlas_submit_context_capture',
            title: 'Smart Save To MindAtlas',
            description: 'Submit one high-value context block for intelligent persistence.',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'context (string)',
            outputSummary: 'created/merged result',
            inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
          {
            capabilityKey: 'custom_project_review',
            toolName: 'mindatlas_custom_project_review',
            title: 'Project Review Workflow',
            description: 'Run the administrator-exposed project review workflow.',
            sourceType: 'workflow',
            implementationType: 'workflow',
            available: true,
            availabilityReason: null,
            inputSummary: 'projectName and date range',
            outputSummary: 'review content',
            inputSchema: {
              type: 'object',
              properties: {
                projectName: { type: 'string' },
              },
              required: ['projectName'],
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
    phase = 'list'
    const result = await findTool(tools, MINDATLAS_LIST_CAPABILITIES_TOOL_NAME).execute('tool-call-2', {})
    const payload = JSON.parse(result.content[0]?.text ?? '{}')

    assert.equal(payload.reloadRequired, true)
    assert.equal(Array.isArray(payload.availableCapabilities), true)
    assert.equal(payload.availableCapabilities.some((item: { capabilityKey: string; dedicatedToolRegistered: boolean }) => item.capabilityKey === 'custom_project_review' && item.dedicatedToolRegistered === false), true)
    assert.equal(payload.registeredToolNames.includes(MINDATLAS_LIST_CAPABILITIES_TOOL_NAME), true)
    assert.equal(payload.registeredToolNames.includes(MINDATLAS_RUN_CAPABILITY_TOOL_NAME), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('mindatlas_run_capability executes a newly exposed capability without a dedicated session-visible tool', async () => {
  const { api, tools } = createMockApi()
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
              capabilityKey: 'submit_context_capture',
              toolName: 'mindatlas_submit_context_capture',
              title: 'Smart Save To MindAtlas',
              description: 'Submit one high-value context block for intelligent persistence.',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'context (string)',
              outputSummary: 'created/merged result',
              inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
          ],
        },
      })
    }

    if (calls === 2) {
      assert.equal(String(input), 'http://127.0.0.1:8000/api/integrations/openclaw/capabilities')
      return createResponse({
        success: true,
        code: 0,
        message: 'OK',
        data: {
          integrationName: 'MindAtlas',
          capabilities: [
            {
              capabilityKey: 'submit_context_capture',
              toolName: 'mindatlas_submit_context_capture',
              title: 'Smart Save To MindAtlas',
              description: 'Submit one high-value context block for intelligent persistence.',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'context (string)',
              outputSummary: 'created/merged result',
              inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
            {
              capabilityKey: 'custom_project_review',
              toolName: 'mindatlas_custom_project_review',
              title: 'Project Review Workflow',
              description: 'Run the administrator-exposed project review workflow.',
              sourceType: 'workflow',
              implementationType: 'workflow',
              available: true,
              availabilityReason: null,
              inputSummary: 'projectName and date range',
              outputSummary: 'review content',
              inputSchema: {
                type: 'object',
                properties: {
                  projectName: { type: 'string' },
                },
                required: ['projectName'],
                additionalProperties: false,
              },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
          ],
        },
      })
    }

    assert.equal(String(input), 'http://127.0.0.1:8000/api/integrations/openclaw/capabilities/custom_project_review/execute')
    assert.equal((init?.headers as Record<string, string>)['X-OpenClaw-Tool'], MINDATLAS_RUN_CAPABILITY_TOOL_NAME)
    assert.deepEqual(JSON.parse(String(init?.body)), { projectName: 'MindAtlas' })
    return createResponse({
      success: true,
      code: 0,
      message: 'OK',
      data: {
        capabilityKey: 'custom_project_review',
        toolName: 'mindatlas_custom_project_review',
        result: {
          content: 'Project review complete',
        },
      },
    })
  }

  try {
    await runtime.register()
    const result = await findTool(tools, MINDATLAS_RUN_CAPABILITY_TOOL_NAME).execute('tool-call-4', {
      capabilityKey: 'custom_project_review',
      input: {
        projectName: 'MindAtlas',
      },
    })

    assert.match(result.content[0]?.text ?? '', /Project review complete/)
    assert.equal(runtime.getState().registeredToolNames.has('mindatlas_custom_project_review'), false)
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
              capabilityKey: 'submit_context_capture',
              toolName: 'mindatlas_submit_context_capture',
              title: 'Smart Save To MindAtlas',
              description: 'Submit one high-value context block for intelligent persistence.',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'context (string)',
              outputSummary: 'created/merged result',
              inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
              toolResponseMode: 'json_schema',
            },
            {
              capabilityKey: 'generate_periodic_review',
              toolName: 'mindatlas_generate_periodic_review',
              title: 'Periodic Review',
              description: 'Generate a time-bounded recap',
              sourceType: 'workflow',
              implementationType: 'workflow',
              available: false,
              availabilityReason: 'Warm-up pending',
              inputSummary: 'focus/period/startDate/endDate',
              outputSummary: 'content (string)',
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
            capabilityKey: 'submit_context_capture',
            toolName: 'mindatlas_submit_context_capture',
            title: 'Smart Save To MindAtlas',
            description: 'Submit one high-value context block for intelligent persistence.',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'context (string)',
            outputSummary: 'created/merged result',
            inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
          {
            capabilityKey: 'generate_periodic_review',
            toolName: 'mindatlas_generate_periodic_review',
            title: 'Periodic Review',
            description: 'Generate a time-bounded recap',
            sourceType: 'workflow',
            implementationType: 'workflow',
            available: true,
            availabilityReason: null,
            inputSummary: 'focus/period/startDate/endDate',
            outputSummary: 'content (string)',
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
    assert.equal(tools.length, 3)

    phase = 'refresh'
    await runtime.refreshCatalog()

    assert.equal(tools.length, 3)
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
              capabilityKey: 'submit_context_capture',
              toolName: 'mindatlas_submit_context_capture',
              title: 'Smart Save To MindAtlas',
              description: 'Submit one high-value context block for intelligent persistence.',
              sourceType: 'tool',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'context (string)',
              outputSummary: 'created/merged result',
              inputSchema: {
                type: 'object',
                properties: { context: { type: 'string' } },
                required: ['context'],
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
            capabilityKey: 'submit_context_capture',
            toolName: 'mindatlas_submit_context_capture',
            title: 'Smart Save To MindAtlas Updated',
            description: 'Submit one high-value context block and let MindAtlas merge it automatically.',
            sourceType: 'tool',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'context (string) plus OpenClaw metadata headers',
            outputSummary: 'created/merged result',
            inputSchema: {
              type: 'object',
              properties: {
                context: { type: 'string' },
              },
              required: ['context'],
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
    assert.equal(tools.length, 3)

    phase = 'drift'
    await runtime.refreshCatalog()

    await assert.rejects(
      () => findTool(tools, 'mindatlas_submit_context_capture').execute('tool-call-3', { context: 'hello' }),
      /Start a new session or reload the OpenClaw plugin or Gateway/,
    )

    const state = runtime.getState()
    assert.equal(state.reloadRequired, true)
    assert.equal(state.staleToolNames.has('mindatlas_submit_context_capture'), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})
