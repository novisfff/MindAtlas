import * as prettier from 'prettier/standalone'
import prettierPluginBabel from 'prettier/plugins/babel'
import prettierPluginEstree from 'prettier/plugins/estree'
import initRuff, { format as formatPythonWithRuff } from '@wasm-fmt/ruff_fmt/vite'

type ScriptLanguage = 'python' | 'javascript'

let ruffInitPromise: Promise<unknown> | null = null

async function ensureRuffInitialized(): Promise<void> {
  if (!ruffInitPromise) {
    ruffInitPromise = initRuff().catch((error) => {
      ruffInitPromise = null
      throw error
    })
  }
  await ruffInitPromise
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
    const formatted = await prettier.format(source, {
      parser: 'babel',
      plugins: [prettierPluginBabel, prettierPluginEstree],
      semi: true,
      singleQuote: true,
      trailingComma: 'all',
    })
    return formatted
  }
  return formatPythonWithRuffWasm(source)
}
