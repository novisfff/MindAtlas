import test from 'node:test'
import assert from 'node:assert/strict'

import { createPlugin, OpenClawMindAtlasPluginRuntime, PLUGIN_ID } from '../src/index'
import { describePluginConfigIssue, extractPluginEntryConfig, resolvePluginConfig, validatePluginConfig } from '../src/config'

interface MockTool {
  name: string
  description: string
  parameters: Record<string, unknown>
  execute: (id: string, params: Record<string, unknown>, context?: { channel?: string; session?: string }) => Promise<{
    content: Array<{ type: 'text'; text: string }>
  }>
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
  const services: Array<{ id: string; start?: () => void | Promise<void>; stop?: () => void | Promise<void> }> = []
  const logs: Array<{ level: string; message: string; details?: unknown }> = []

  return {
    api: {
      config,
      logger: {
        info(message: string, details?: unknown) {
          logs.push({ level: 'info', message, details })
        },
        warn(message: string, details?: unknown) {
          logs.push({ level: 'warn', message, details })
        },
        error(message: string, details?: unknown) {
          logs.push({ level: 'error', message, details })
        },
      },
      registerTool(tool: MockTool) {
        tools.push(tool)
      },
      registerService(service: { id: string; start?: () => void | Promise<void>; stop?: () => void | Promise<void> }) {
        services.push(service)
      },
    },
    tools,
    services,
    logs,
  }
}

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

test('plugin start skips registration when install-time config is still missing', async () => {
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
  const plugin = createPlugin(api)
  plugin.register()

  await services[0].start?.()

  assert.equal(tools.length, 0)
  assert.equal(logs.some((entry) => entry.level === 'warn' && /installed but not configured yet/.test(entry.message)), true)
})

test('plugin registers only available tools when the catalog loads', async () => {
  const { api, services, tools } = createMockApi()
  const plugin = createPlugin(api)
  plugin.register()

  assert.equal(services.length, 1)

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
            sourceType: 'system_adapter',
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
            sourceType: 'system_adapter',
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
    await services[0].start?.()
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(tools.length, 1)
  assert.equal(tools[0].name, 'mindatlas_capture_entry')
  assert.equal(tools[0].parameters.type, 'object')
})

test('tool execution forwards params and returns textified result', async () => {
  const { api, services, tools } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api)
  runtime.register()

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
              sourceType: 'system_adapter',
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
    await services[0].start?.()
    const result = await tools[0].execute('tool-call-1', { title: 'hello' }, { channel: 'discord', session: 'session-42' })
    assert.equal(result.content[0]?.type, 'text')
    assert.match(result.content[0]?.text ?? '', /"id": "entry-1"/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('structure drift marks removed tools as stale and asks for reload', async () => {
  const { api, services, tools } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api)
  runtime.register()

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
              sourceType: 'system_adapter',
              implementationType: 'entry',
              available: true,
              availabilityReason: null,
              inputSummary: 'title (string)',
              outputSummary: 'id (string)',
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
            capabilityKey: 'renamed_capture_entry',
            toolName: 'mindatlas_capture_entry_v2',
            title: 'Capture Entry v2',
            description: 'Save a new entry',
            sourceType: 'system_adapter',
            implementationType: 'entry',
            available: true,
            availabilityReason: null,
            inputSummary: 'title (string)',
            outputSummary: 'id (string)',
            inputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            outputSchema: { type: 'object', properties: {}, required: [], additionalProperties: false },
            toolResponseMode: 'json_schema',
          },
        ],
      },
    })
  }

  try {
    await services[0].start?.()
    assert.equal(tools.length, 1)

    phase = 'drift'
    await runtime.refreshCatalog()

    await assert.rejects(
      () => tools[0].execute('tool-call-2', {}),
      /Reload the OpenClaw plugin or Gateway/,
    )

    const state = runtime.getState()
    assert.equal(state.reloadRequired, true)
    assert.equal(state.staleToolNames.has('mindatlas_capture_entry'), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('metadata drift marks existing tools as stale and asks for reload', async () => {
  const { api, services, tools } = createMockApi()
  const runtime = new OpenClawMindAtlasPluginRuntime(api)
  runtime.register()

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
              sourceType: 'system_adapter',
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
            sourceType: 'system_adapter',
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
    await services[0].start?.()
    assert.equal(tools.length, 1)

    phase = 'drift'
    await runtime.refreshCatalog()

    await assert.rejects(
      () => tools[0].execute('tool-call-3', { title: 'hello' }),
      /Reload the OpenClaw plugin or Gateway/,
    )

    const state = runtime.getState()
    assert.equal(state.reloadRequired, true)
    assert.equal(state.staleToolNames.has('mindatlas_capture_entry'), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})
