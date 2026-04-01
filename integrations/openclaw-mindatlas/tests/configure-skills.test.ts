import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

const {
  configureOpenClawSkills,
  ensureSkillsExtraDir,
  resolveInstalledPluginRoot,
  resolveOpenClawConfigPath,
  resolveOpenClawRoot,
} = await import('../scripts/configure-openclaw-skills.mjs')

test('resolveOpenClawConfigPath prefers OPENCLAW_CONFIG_PATH', () => {
  assert.equal(
    resolveOpenClawConfigPath({
      env: {
        OPENCLAW_CONFIG_PATH: '/tmp/openclaw-profile/openclaw.json',
        OPENCLAW_STATE_DIR: '/tmp/openclaw-state',
      },
      homeDir: '/tmp/openclaw-home',
    }),
    '/tmp/openclaw-profile/openclaw.json',
  )
})

test('resolveOpenClawRoot prefers OPENCLAW_CONFIG_PATH parent before OPENCLAW_STATE_DIR', () => {
  assert.equal(
    resolveOpenClawRoot({
      env: {
        OPENCLAW_CONFIG_PATH: '/tmp/openclaw-profile/openclaw.json',
        OPENCLAW_STATE_DIR: '/tmp/openclaw-state',
      },
      homeDir: '/tmp/openclaw-home',
    }),
    '/tmp/openclaw-profile',
  )
})

test('resolveInstalledPluginRoot prefers plugin installPath from config', () => {
  assert.equal(
    resolveInstalledPluginRoot(
      {
        plugins: {
          installs: {
            'openclaw-mindatlas': {
              installPath: '/srv/openclaw/extensions/openclaw-mindatlas',
            },
          },
        },
      },
      {
        env: {
          OPENCLAW_CONFIG_PATH: '/tmp/openclaw-profile/openclaw.json',
        },
      },
    ),
    '/srv/openclaw/extensions/openclaw-mindatlas',
  )
})

test('ensureSkillsExtraDir appends the MindAtlas skills directory only once', () => {
  const nextConfig = ensureSkillsExtraDir(
    {
      skills: {
        load: {
          extraDirs: ['/tmp/shared-skills'],
        },
      },
    },
    '/tmp/openclaw/extensions/openclaw-mindatlas/skills',
  )

  assert.deepEqual(nextConfig.skills.load.extraDirs, [
    '/tmp/shared-skills',
    '/tmp/openclaw/extensions/openclaw-mindatlas/skills',
  ])

  const dedupedConfig = ensureSkillsExtraDir(nextConfig, '/tmp/openclaw/extensions/openclaw-mindatlas/skills')
  assert.deepEqual(dedupedConfig.skills.load.extraDirs, [
    '/tmp/shared-skills',
    '/tmp/openclaw/extensions/openclaw-mindatlas/skills',
  ])
})

test('configureOpenClawSkills writes the installed plugin skills path into openclaw.json', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-configure-skills-'))
  const configPath = path.join(tempRoot, 'openclaw.json')
  const pluginRoot = path.join(tempRoot, 'extensions', 'openclaw-mindatlas')
  const skillsDir = path.join(pluginRoot, 'skills')

  try {
    fs.mkdirSync(skillsDir, { recursive: true })
    fs.writeFileSync(
      configPath,
      JSON.stringify(
        {
          plugins: {
            installs: {
              'openclaw-mindatlas': {
                installPath: pluginRoot,
              },
            },
          },
        },
        null,
        2,
      ),
      'utf8',
    )

    const result = configureOpenClawSkills({ configPath })
    const writtenConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'))

    assert.equal(result.skillsDir, skillsDir)
    assert.deepEqual(writtenConfig.skills.load.extraDirs, [skillsDir])
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})

test('configureOpenClawSkills falls back to the local plugin skills directory when no installed copy exists yet', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mindatlas-configure-skills-local-'))
  const configPath = path.join(tempRoot, 'openclaw.json')

  try {
    const result = configureOpenClawSkills({ configPath })
    const writtenConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'))

    assert.equal(path.basename(result.skillsDir), 'skills')
    assert.match(result.skillsDir, /openclaw-mindatlas[\\/]+skills$/)
    assert.deepEqual(writtenConfig.skills.load.extraDirs, [result.skillsDir])
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true })
  }
})
