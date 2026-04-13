import initRuff, { format as formatPythonWithRuff } from '@wasm-fmt/ruff_fmt/vite'

type ScriptLanguage = 'python' | 'javascript'

let ruffInitPromise: Promise<unknown> | null = null
let prettierBundlePromise: Promise<{
  prettier: typeof import('prettier/standalone')
  babel: typeof import('prettier/plugins/babel')
  estree: typeof import('prettier/plugins/estree')
}> | null = null

async function ensureRuffInitialized(): Promise<void> {
  if (!ruffInitPromise) {
    ruffInitPromise = initRuff().catch((error) => {
      ruffInitPromise = null
      throw error
    })
  }
  await ruffInitPromise
}

async function getPrettierBundle() {
  if (!prettierBundlePromise) {
    prettierBundlePromise = Promise.all([
      import('prettier/standalone'),
      import('prettier/plugins/babel'),
      import('prettier/plugins/estree'),
    ]).then(([prettier, babel, estree]) => ({
      prettier,
      babel,
      estree,
    }))
  }
  return prettierBundlePromise
}

async function formatPythonWithRuffWasm(source: string): Promise<string> {
  await ensureRuffInitialized()
  return formatPythonWithRuff(String(source ?? ''), 'main.py', {
    indent_style: 'space',
    indent_width: 4,
    line_width: 88,
    quote_style: 'double',
    magic_trailing_comma: 'respect',
  })
}

export async function formatCode(language: ScriptLanguage, source: string): Promise<string> {
  if (language === 'javascript') {
    const { prettier, babel, estree } = await getPrettierBundle()
    const formatted = await prettier.format(source, {
      parser: 'babel',
      plugins: [babel, estree],
      semi: true,
      singleQuote: true,
      trailingComma: 'all',
    })
    return formatted
  }
  return formatPythonWithRuffWasm(source)
}
