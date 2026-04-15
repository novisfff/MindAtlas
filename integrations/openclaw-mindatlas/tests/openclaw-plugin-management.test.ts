import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

const {
  LOCAL_PLUGIN_ROOT,
  backupConflictingSkills,
  cleanupLegacyMindAtlasConfig,
  runSetupOpenClawPlugin,
  runUpdateOpenClawPlugin,
} = await import('../../openclaw-mindatlas-cli/openclaw-plugin-management.mjs')

function createSilentLogger() {
  return {
    info() {},
    warn() {},
    error() {},
  }
}

function createPrompt(answers: string[]) {
  let index = 0
  let closeCount = 0

  return {
    prompt: {
      async ask() {
        const answer = answers[index]
        index += 1
        return answer ?? ''
      },
      async close() {
        closeCount += 1
      },
    },
    get askCount() {
      return index
    },
    get closeCount() {
      return closeCount
    },
  }
}

function createRunner(options: { failUninstall?: boolean } = {}) {
  const commands: string[] = []
  const runner = (command: string, args: string[]) => {
    const rendered = [command, ...args].join(' ')
    commands.push(rendered)

    if (rendered === 'openclaw --version') {
      return { status: 0, stdout: 'OpenClaw 2026.4.1\n', stderr: '' }
    }
    if (rendered === 'openclaw plugins uninstall openclaw-mindatlas --force' && options.failUninstall) {
      return { status: 1, stdout: '', stderr: 'plugin is not tracked\n' }
    }

    return { status: 0, stdout: '', stderr: '' }
  }

  return {
    commands,
    runner,
  }
}

test('cleanupLegacyMindAtlasConfig removes only MindAtlas-specific compatibility remnants', () => {
  const result = cleanupLegacyMindAtlasConfig({
    plugins: {
      allow: ['openclaw-mindatlas', 'feishu-plugin'],
      entries: {
        'openclaw-mindatlas': {
          enabled: true,
          config: {
            baseUrl: 'http://localhost:8000',
            integrationSecret: 'secret',
          },
        },
      },
    },
    tools: {
      allow: ['openclaw-mindatlas', 'mindatlas_search_entries', 'feishu_doc'],
      profile: 'full',
    },
  })

  assert.deepEqual(result.config.plugins.allow, ['openclaw-mindatlas', 'feishu-plugin'])
  assert.deepEqual(result.config.tools.allow, ['feishu_doc'])
  assert.equal(result.config.tools.profile, 'full')
  assert.equal(result.cleanupMessages.length, 1)
})

test('cleanupLegacyMindAtlasConfig removes tools.profile only when MindAtlas cleanup empties the tools block', () => {
  const result = cleanupLegacyMindAtlasConfig({
    tools: {
      allow: ['mindatlas_generate_weekly_report'],
      profile: 'full',
    },
  })

  assert.equal(result.config.tools, undefined)
  assert.equal(result.cleanupMessages.length, 2)
})

test('cleanupLegacyMindAtlasConfig restores the plugin allowlist entry when plugin allowlist mode is active', () => {
  const result = cleanupLegacyMindAtlasConfig({
    plugins: {
      allow: ['feishu-plugin'],
      entries: {
        'openclaw-mindatlas': {
          enabled: true,
          config: {
            baseUrl: 'http://localhost:8000',
            integrationSecret: 'secret',
          },
        },
      },
    },
  })

  assert.deepEqual(result.config.plugins.allow, ['feishu-plugin', 'openclaw-mindatlas'])
  assert.deepEqual(result.cleanupMessages, [
    'Restoring `plugins.allow` entry for `openclaw-mindatlas` so the plugin stays enabled when plugin allowlist mode is active.',
  ])
})

test('backupConflictingSkills moves same-named MindAtlas skill directories into a timestamped backup folder', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-skill-backup-'))
  const skillsRoot = path.join(tempRoot, 'skills')

  try {
    fs.mkdirSync(path.join(skillsRoot, 'mindatlas-overview'), { recursive: true })
    fs.mkdirSync(path.join(skillsRoot, 'mindatlas-summary'), { recursive: true })
    fs.writeFileSync(path.join(skillsRoot, 'mindatlas-overview', 'SKILL.md'), 'overview\n', 'utf8')
    fs.writeFileSync(path.join(skillsRoot, 'mindatlas-summary', 'SKILL.md'), 'summary\n', 'utf8')

    const result = backupConflictingSkills({
      skillsRoot,
      timestamp: '20260415-160000',
    })

    assert.equal(result.backupDir, path.join(tempRoot, 'skills-backup-20260415-160000'))
    assert.deepEqual(result.movedSkillIds.sort(), ['mindatlas-overview', 'mindatlas-summary'])
    assert.equal(fs.existsSync(path.join(skillsRoot, 'mindatlas-overview')), false)
    assert.equal(fs.existsSync(path.join(result.backupDir, 'mindatlas-overview', 'SKILL.md')), true)
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('runSetupOpenClawPlugin prompts for four config fields and writes the plugin config', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-setup-openclaw-'))
  const configPath = path.join(tempRoot, 'openclaw.json')
  const promptState = createPrompt([
    'http://127.0.0.1:8000',
    'setup-secret',
    '18000',
    '600',
  ])
  const runnerState = createRunner()

  try {
    await runSetupOpenClawPlugin({
      configPath,
      homeDir: tempRoot,
      prompt: promptState.prompt,
      runner: runnerState.runner,
      logger: createSilentLogger(),
    })

    const writtenConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    assert.equal(promptState.askCount, 4)
    assert.equal(promptState.closeCount, 1)
    assert.deepEqual(writtenConfig.plugins.entries['openclaw-mindatlas'], {
      enabled: true,
      config: {
        baseUrl: 'http://127.0.0.1:8000',
        integrationSecret: 'setup-secret',
        requestTimeoutMs: 18000,
        catalogRefreshTtlSec: 600,
      },
    })
    assert.match(writtenConfig.skills.load.extraDirs[0], /openclaw-mindatlas[\\/]+skills$/)
    assert.deepEqual(runnerState.commands, [
      'openclaw --version',
      `openclaw plugins install --link ${LOCAL_PLUGIN_ROOT}`,
      'openclaw gateway restart',
    ])
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('runUpdateOpenClawPlugin reuses the existing plugin config without reprompting complete fields', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-update-openclaw-'))
  const configPath = path.join(tempRoot, 'openclaw.json')
  const installPath = path.join(tempRoot, 'extensions', 'openclaw-mindatlas')
  const promptState = createPrompt([])
  const runnerState = createRunner()

  try {
    fs.mkdirSync(installPath, { recursive: true })
    fs.writeFileSync(
      configPath,
      JSON.stringify(
        {
          plugins: {
            allow: ['feishu-plugin'],
            installs: {
              'openclaw-mindatlas': {
                installPath,
              },
            },
            entries: {
              'openclaw-mindatlas': {
                enabled: false,
                config: {
                  baseUrl: 'http://mindatlas.internal',
                  integrationSecret: 'existing-secret',
                  requestTimeoutMs: 25000,
                  catalogRefreshTtlSec: 900,
                },
              },
            },
          },
        },
        null,
        2,
      ),
      'utf8',
    )

    await runUpdateOpenClawPlugin({
      configPath,
      homeDir: tempRoot,
      prompt: promptState.prompt,
      runner: runnerState.runner,
      logger: createSilentLogger(),
    })

    const writtenConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    assert.equal(promptState.askCount, 0)
    assert.equal(promptState.closeCount, 1)
    assert.equal(fs.existsSync(installPath), false)
    assert.deepEqual(writtenConfig.plugins.entries['openclaw-mindatlas'], {
      enabled: false,
      config: {
        baseUrl: 'http://mindatlas.internal',
        integrationSecret: 'existing-secret',
        requestTimeoutMs: 25000,
        catalogRefreshTtlSec: 900,
      },
    })
    assert.deepEqual(writtenConfig.plugins.allow, ['feishu-plugin'])
    assert.deepEqual(runnerState.commands, [
      'openclaw --version',
      'openclaw plugins uninstall openclaw-mindatlas --force',
      `openclaw plugins install --link ${LOCAL_PLUGIN_ROOT}`,
      'openclaw gateway restart',
    ])
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('runUpdateOpenClawPlugin only prompts for missing fields and manually removes the install path when uninstall fails', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-update-openclaw-fallback-'))
  const configPath = path.join(tempRoot, 'openclaw.json')
  const installPath = path.join(tempRoot, 'extensions', 'openclaw-mindatlas')
  const promptState = createPrompt(['12000', '450'])
  const runnerState = createRunner({ failUninstall: true })

  try {
    fs.mkdirSync(installPath, { recursive: true })
    fs.writeFileSync(
      configPath,
      JSON.stringify(
        {
          plugins: {
            allow: ['feishu-plugin'],
            installs: {
              'openclaw-mindatlas': {
                installPath,
              },
            },
            entries: {
              'openclaw-mindatlas': {
                enabled: true,
                config: {
                  baseUrl: 'http://mindatlas.internal',
                  integrationSecret: 'existing-secret',
                },
              },
            },
          },
          tools: {
            allow: ['openclaw-mindatlas', 'mindatlas_search_entries'],
            profile: 'full',
          },
        },
        null,
        2,
      ),
      'utf8',
    )

    await runUpdateOpenClawPlugin({
      configPath,
      homeDir: tempRoot,
      prompt: promptState.prompt,
      runner: runnerState.runner,
      logger: createSilentLogger(),
    })

    const writtenConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    assert.equal(promptState.askCount, 2)
    assert.equal(fs.existsSync(installPath), false)
    assert.deepEqual(writtenConfig.plugins.entries['openclaw-mindatlas'].config, {
      baseUrl: 'http://mindatlas.internal',
      integrationSecret: 'existing-secret',
      requestTimeoutMs: 12000,
      catalogRefreshTtlSec: 450,
    })
    assert.deepEqual(writtenConfig.plugins.allow, ['feishu-plugin', 'openclaw-mindatlas'])
    assert.equal(writtenConfig.tools, undefined)
    assert.deepEqual(runnerState.commands, [
      'openclaw --version',
      'openclaw plugins uninstall openclaw-mindatlas --force',
      `openclaw plugins install --link ${LOCAL_PLUGIN_ROOT}`,
      'openclaw gateway restart',
    ])
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('runUpdateOpenClawPlugin does not delete a linked local plugin source path when uninstall fails', async () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-update-openclaw-linked-'))
  const configPath = path.join(tempRoot, 'openclaw.json')
  const linkedSourcePath = path.join(tempRoot, 'linked-openclaw-mindatlas')
  const promptState = createPrompt([])
  const runnerState = createRunner({ failUninstall: true })

  try {
    fs.mkdirSync(path.join(linkedSourcePath, 'skills'), { recursive: true })
    fs.writeFileSync(
      configPath,
      JSON.stringify(
        {
          plugins: {
            allow: ['feishu-plugin', 'openclaw-mindatlas'],
            installs: {
              'openclaw-mindatlas': {
                source: 'path',
                sourcePath: linkedSourcePath,
                installPath: linkedSourcePath,
              },
            },
            entries: {
              'openclaw-mindatlas': {
                enabled: true,
                config: {
                  baseUrl: 'http://mindatlas.internal',
                  integrationSecret: 'existing-secret',
                  requestTimeoutMs: 15000,
                  catalogRefreshTtlSec: 300,
                },
              },
            },
          },
        },
        null,
        2,
      ),
      'utf8',
    )

    await runUpdateOpenClawPlugin({
      configPath,
      homeDir: tempRoot,
      prompt: promptState.prompt,
      runner: runnerState.runner,
      logger: createSilentLogger(),
    })

    assert.equal(fs.existsSync(linkedSourcePath), true)
    assert.deepEqual(runnerState.commands, [
      'openclaw --version',
      'openclaw plugins uninstall openclaw-mindatlas --force',
      `openclaw plugins install --link ${LOCAL_PLUGIN_ROOT}`,
      'openclaw gateway restart',
    ])
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})
