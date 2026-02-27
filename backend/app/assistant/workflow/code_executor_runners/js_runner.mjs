import vm from 'node:vm'
import { createRequire } from 'node:module'

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = ''
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', (chunk) => {
      data += chunk
    })
    process.stdin.on('end', () => resolve(data))
    process.stdin.on('error', reject)
  })
}

function emit(payload) {
  process.stdout.write(JSON.stringify(payload))
}

function makeLimitedLogger(maxChars) {
  let text = ''
  const append = (line) => {
    text += line
    if (text.length > maxChars) {
      text = text.slice(0, maxChars) + '...(truncated)'
    }
  }
  return {
    logger: {
      log: (...args) => append(`${args.map((item) => String(item)).join(' ')}\n`),
      info: (...args) => append(`${args.map((item) => String(item)).join(' ')}\n`),
      warn: (...args) => append(`${args.map((item) => String(item)).join(' ')}\n`),
      error: (...args) => append(`${args.map((item) => String(item)).join(' ')}\n`),
    },
    getValue: () => text,
  }
}

function rootModuleName(name) {
  if (!name) return ''
  if (name.startsWith('node:')) {
    return name.slice(5).split('/')[0]
  }
  return name.split('/')[0]
}

function parseSignatureParams(code, entrypoint) {
  const escaped = String(entrypoint || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  if (!escaped) return null
  const patterns = [
    new RegExp(`(?:^|\\n)\\s*(?:async\\s+)?function\\s+${escaped}\\s*\\(([^)]*)\\)`, 'm'),
    new RegExp(`(?:^|\\n)\\s*(?:const|let|var)\\s+${escaped}\\s*=\\s*(?:async\\s*)?\\(([^)]*)\\)\\s*=>`, 'm'),
    new RegExp(`(?:^|\\n)\\s*(?:const|let|var)\\s+${escaped}\\s*=\\s*(?:async\\s+)?function\\s*\\(([^)]*)\\)`, 'm'),
    new RegExp(`(?:^|\\n)\\s*(?:module\\.exports|exports)\\.${escaped}\\s*=\\s*(?:async\\s*)?\\(([^)]*)\\)\\s*=>`, 'm'),
    new RegExp(`(?:^|\\n)\\s*(?:module\\.exports|exports)\\.${escaped}\\s*=\\s*(?:async\\s+)?function\\s*\\(([^)]*)\\)`, 'm'),
  ]

  for (const pattern of patterns) {
    const match = String(code || '').match(pattern)
    if (!match) continue
    return String(match[1] || '')
      .split(',')
      .map((token) => token.trim())
      .filter(Boolean)
      .map((token) => token.replace(/^\.\.\./, '').split('=')[0].trim())
      .filter(Boolean)
  }
  return null
}

async function main() {
  try {
    const raw = (await readStdin()).trim()
    const payload = raw ? JSON.parse(raw) : {}

    const code = String(payload.code || '')
    const entrypoint = String(payload.entrypoint || 'main').trim() || 'main'
    const inputs =
      payload.inputs && typeof payload.inputs === 'object' && !Array.isArray(payload.inputs)
        ? payload.inputs
        : {}
    const timeoutMs = Number(payload.timeoutMs || 5000)
    const maxOutputChars = Number(payload.maxOutputChars || 16000)
    const allowed = new Set(Array.isArray(payload.allowedModules) ? payload.allowedModules.map((item) => String(item)) : [])

    if (!code.trim()) {
      throw new Error('Code is required')
    }

    if (/\bimport\s*\(/.test(code)) {
      throw new Error('Dynamic JavaScript import() is not allowed')
    }

    const { logger, getValue } = makeLimitedLogger(maxOutputChars)
    const nodeRequire = createRequire(import.meta.url)

    const safeRequire = (name) => {
      const modName = String(name || '')
      const root = rootModuleName(modName)
      if (!allowed.has(root)) {
        throw new Error(`Import not allowed: ${modName}`)
      }
      return nodeRequire(modName)
    }

    const sandbox = {
      console: logger,
      JSON,
      Math,
      Date,
      Buffer,
      URL,
      URLSearchParams,
      require: safeRequire,
      module: { exports: {} },
      exports: {},
    }
    sandbox.globalThis = sandbox

    const wrapped = `"use strict";\n${code}\n\n;globalThis.__codex_entry = (
      typeof ${entrypoint} === 'function'
        ? ${entrypoint}
        : (
            module && module.exports && typeof module.exports.${entrypoint} === 'function'
              ? module.exports.${entrypoint}
              : (exports && typeof exports.${entrypoint} === 'function' ? exports.${entrypoint} : null)
          )
    );`

    const context = vm.createContext(sandbox)
    const script = new vm.Script(wrapped, {
      filename: 'code_executor_user_script.js',
    })
    script.runInContext(context, { timeout: timeoutMs })

    const fn = context.__codex_entry
    if (typeof fn !== 'function') {
      throw new Error(`Entrypoint '${entrypoint}' is not defined or not callable`)
    }

    const paramNames = parseSignatureParams(code, entrypoint)
    if (!paramNames) {
      throw new Error(`Cannot determine parameter list for '${entrypoint}'`)
    }
    const args = paramNames.map((name) =>
      Object.prototype.hasOwnProperty.call(inputs, name) ? inputs[name] : undefined,
    )

    const result = await Promise.resolve(fn(...args))
    if (!result || typeof result !== 'object' || Array.isArray(result)) {
      throw new Error(`${entrypoint}(...) must return an object`)
    }

    emit({
      ok: true,
      output: result,
      stdout: getValue(),
      stderr: '',
    })
  } catch (error) {
    emit({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      stdout: '',
      stderr: error instanceof Error && error.stack ? error.stack : String(error),
    })
  }
}

main()
