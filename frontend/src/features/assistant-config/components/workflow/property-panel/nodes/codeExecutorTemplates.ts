export type CodeLanguage = 'python' | 'javascript'

const PYTHON_TEMPLATE = `def main(arg1: str, arg2: str):
    return {
        "result": arg1 + arg2,
    }
`

const JAVASCRIPT_TEMPLATE = `function main(arg1, arg2) {
  return { result: String(arg1 ?? '') + String(arg2 ?? '') };
}
`

export function getDefaultCodeTemplate(language: CodeLanguage): string {
  return language === 'javascript' ? JAVASCRIPT_TEMPLATE : PYTHON_TEMPLATE
}

export function normalizeTemplateForCompare(source: string): string {
  const normalized = String(source ?? '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .split('\n')
    .map((line) => line.replace(/[ \t]+$/g, ''))
    .join('\n')

  return normalized.trimEnd()
}
