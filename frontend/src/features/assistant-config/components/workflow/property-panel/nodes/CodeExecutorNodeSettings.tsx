import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { EditorView, placeholder as codePlaceholder } from '@codemirror/view'
import CodeMirror from '@uiw/react-codemirror'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import type { InputParam } from '../../../../api/tools'
import { RichMentionInput } from '../../../RichMentionInput'
import { CommonOutputList, Label } from '../CommonInputs'
import { formatCode } from './codeExecutorFormat'
import {
  getDefaultCodeTemplate,
  normalizeTemplateForCompare,
  type CodeLanguage,
} from './codeExecutorTemplates'
import type { NodeSettingsProps } from './ToolNodeSettings'

type OutputField = {
  name: string
  type: string
  nullable: boolean
  itemsType?: string
}

type InputBinding = {
  key: string
  value: string
}

const FIELD_TYPES = [
  { label: 'String', value: 'string' },
  { label: 'Number', value: 'number' },
  { label: 'Integer', value: 'integer' },
  { label: 'Boolean', value: 'boolean' },
  { label: 'Object', value: 'object' },
  { label: 'Array', value: 'array' },
]

const ARRAY_ITEM_TYPES = FIELD_TYPES.filter((item) => item.value !== 'array')
const DEFAULT_INPUT_BINDINGS: InputBinding[] = [
  { key: 'arg1', value: '' },
  { key: 'arg2', value: '' },
]

function normalizeOutputFields(raw: unknown): OutputField[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => ({
      name: String(item.name ?? ''),
      type: String(item.type ?? 'string') || 'string',
      nullable: Boolean(item.nullable),
      itemsType:
        typeof item.itemsType === 'string'
          ? item.itemsType
          : (typeof item.items_type === 'string' ? item.items_type : undefined),
    }))
}

function shouldSeedDefaultBindings(raw: unknown): boolean {
  if (raw === null || raw === undefined) return true
  if (typeof raw !== 'object' || Array.isArray(raw)) return true
  return false
}

function normalizeInputBindings(raw: unknown): InputBinding[] {
  if (shouldSeedDefaultBindings(raw)) return [...DEFAULT_INPUT_BINDINGS]
  const source = raw as Record<string, unknown>
  return Object.entries(source).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : String(value ?? ''),
  }))
}

function bindingsToRecord(rows: InputBinding[]): Record<string, string> {
  const result: Record<string, string> = {}
  rows.forEach((item) => {
    const key = item.key.trim()
    if (!key) return
    result[key] = item.value
  })
  return result
}

function getNextBindingKey(rows: InputBinding[]): string {
  const used = new Set(rows.map((item) => item.key.trim()).filter(Boolean))
  let index = Math.max(1, rows.length + 1)
  while (used.has(`arg${index}`)) {
    index += 1
  }
  return `arg${index}`
}

export function CodeExecutorNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
  const { t } = useTranslation()
  const [isFormatting, setIsFormatting] = useState(false)
  const [pendingLanguage, setPendingLanguage] = useState<CodeLanguage | null>(null)
  const [isLanguageResetConfirmOpen, setIsLanguageResetConfirmOpen] = useState(false)

  const normalizedMentionParams = Array.isArray(mentionParams) ? (mentionParams as InputParam[]) : []
  const language: CodeLanguage = String(config.language ?? 'python') === 'javascript' ? 'javascript' : 'python'
  const timeoutValue = Number.isFinite(Number(config.timeoutMs)) ? String(config.timeoutMs) : ''
  const codeValue = String(config.code ?? '')
  const inputBindingRows = useMemo(
    () => normalizeInputBindings(config.inputBindings),
    [config.inputBindings],
  )
  const outputFields = normalizeOutputFields(config.outputFields)

  useEffect(() => {
    if (!shouldSeedDefaultBindings(config.inputBindings)) return
    onUpdate({ inputBindings: bindingsToRecord(DEFAULT_INPUT_BINDINGS) })
  }, [config.inputBindings, onUpdate])

  const scriptPlaceholder = useMemo(
    () => getDefaultCodeTemplate(language),
    [language],
  )
  const editorExtensions = useMemo(
    () => [
      language === 'javascript' ? javascript({ jsx: false }) : python(),
      EditorView.lineWrapping,
      codePlaceholder(scriptPlaceholder),
    ],
    [language, scriptPlaceholder],
  )

  const setBindingRows = (rows: InputBinding[]) => {
    onUpdate({ inputBindings: bindingsToRecord(rows) })
  }

  const updateBindingRow = (index: number, patch: Partial<InputBinding>) => {
    if (typeof patch.key === 'string' && !patch.key.trim()) {
      return
    }
    const next = [...inputBindingRows]
    const current = next[index] ?? { key: '', value: '' }
    next[index] = { ...current, ...patch }
    setBindingRows(next)
  }

  const addBindingRow = () => {
    setBindingRows([...inputBindingRows, { key: getNextBindingKey(inputBindingRows), value: '' }])
  }

  const removeBindingRow = (index: number) => {
    const next = [...inputBindingRows]
    next.splice(index, 1)
    setBindingRows(next)
  }

  const updateOutputFields = (next: OutputField[]) => {
    onUpdate({
      outputFields: next.map((field) => ({
        name: field.name,
        type: field.type,
        nullable: field.nullable,
        ...(field.type === 'array' && field.itemsType ? { itemsType: field.itemsType } : {}),
      })),
    })
  }

  const addOutputField = () => {
    updateOutputFields([...outputFields, { name: 'result', type: 'string', nullable: false }])
  }

  const updateOutputField = (index: number, patch: Partial<OutputField>) => {
    const next = [...outputFields]
    const current = next[index] ?? { name: '', type: 'string', nullable: false }
    const merged: OutputField = { ...current, ...patch }
    if (merged.type !== 'array') {
      delete merged.itemsType
    } else if (!merged.itemsType) {
      merged.itemsType = 'string'
    }
    next[index] = merged
    updateOutputFields(next)
  }

  const removeOutputField = (index: number) => {
    const next = [...outputFields]
    next.splice(index, 1)
    updateOutputFields(next)
  }

  const handleFormat = async () => {
    try {
      setIsFormatting(true)
      const formatted = await formatCode(language, codeValue)
      onUpdate({ code: formatted })
      toast.success(t('settings.skills.codeExecutor.formatSuccess'))
    } catch (error) {
      const detail = error instanceof Error ? `: ${error.message}` : ''
      toast.error(`${t('settings.skills.codeExecutor.formatFailed')}${detail}`)
    } finally {
      setIsFormatting(false)
    }
  }

  const applyLanguageSwitch = (nextLanguage: CodeLanguage) => {
    onUpdate({
      language: nextLanguage,
      code: getDefaultCodeTemplate(nextLanguage),
    })
  }

  const handleLanguageSwitch = (nextLanguage: CodeLanguage) => {
    if (nextLanguage === language) return

    const currentTemplate = getDefaultCodeTemplate(language)
    const isModified =
      normalizeTemplateForCompare(codeValue) !== normalizeTemplateForCompare(currentTemplate)

    if (!isModified) {
      applyLanguageSwitch(nextLanguage)
      return
    }

    setPendingLanguage(nextLanguage)
    setIsLanguageResetConfirmOpen(true)
  }

  const handleLanguageSwitchConfirm = () => {
    if (!pendingLanguage) return
    applyLanguageSwitch(pendingLanguage)
    setPendingLanguage(null)
    setIsLanguageResetConfirmOpen(false)
  }

  const outputNames = outputFields.map((item) => item.name.trim()).filter(Boolean)
  const pendingLanguageName = pendingLanguage === 'javascript' ? 'JavaScript' : 'Python'

  return (
    <>
      <div className="space-y-6">
        <div className="space-y-1.5">
          <Label>{t('settings.skills.codeExecutor.timeoutMs')}</Label>
          <input
            type="number"
            min={100}
            max={5000}
            value={timeoutValue}
            onChange={(event) => {
              const text = event.target.value.trim()
              if (!text) {
                onUpdate({ timeoutMs: undefined })
                return
              }
              const parsed = Number.parseInt(text, 10)
              if (Number.isNaN(parsed)) return
              onUpdate({ timeoutMs: Math.max(100, Math.min(5000, parsed)) })
            }}
            className="w-full px-2.5 py-2 text-xs rounded-md border bg-background/50"
            placeholder="5000"
          />
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>{t('settings.skills.codeExecutor.inputBindings')}</Label>
            <button
              type="button"
              onClick={addBindingRow}
              className="flex items-center gap-1 text-[10px] bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1 rounded transition-colors"
            >
              <Plus className="w-3 h-3" />
              {t('actions.add')}
            </button>
          </div>
          <p className="text-[11px] text-muted-foreground">{t('settings.skills.codeExecutor.defaultBindingsHint')}</p>
          <div className="space-y-2">
            {inputBindingRows.map((item, index) => (
              <div key={`${index}-${item.key}`} className="grid grid-cols-[120px_1fr_auto] items-center gap-2">
                <input
                  type="text"
                  value={item.key}
                  onChange={(event) => updateBindingRow(index, { key: event.target.value })}
                  className="w-full h-8 px-2.5 text-xs rounded-md border bg-background/50 font-mono"
                  placeholder={t('settings.skills.codeExecutor.inputKeyPlaceholder')}
                />
                <RichMentionInput
                  value={item.value}
                  onChange={(val) => updateBindingRow(index, { value: val })}
                  inputParams={normalizedMentionParams}
                  placeholder={t('settings.skills.argsTemplatePlaceholder')}
                  className="min-w-0"
                />
                <button
                  type="button"
                  onClick={() => removeBindingRow(index)}
                  className="text-muted-foreground hover:text-red-500 p-1 rounded hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
            {inputBindingRows.length === 0 && (
              <div className="text-[11px] text-muted-foreground">
                {t('settings.skills.codeExecutor.inputBindingsEmpty')}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>{t('settings.skills.codeExecutor.outputFields')}</Label>
            <button
              type="button"
              onClick={addOutputField}
              className="flex items-center gap-1 text-[10px] bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1 rounded transition-colors"
            >
              <Plus className="w-3 h-3" />
              {t('actions.add')}
            </button>
          </div>

          <div className="space-y-2">
            {outputFields.map((field, index) => (
              <div key={index} className="flex items-center gap-2 p-2 rounded-md border bg-card/50">
                <input
                  type="text"
                  value={field.name}
                  onChange={(event) => updateOutputField(index, { name: event.target.value })}
                  className="flex-1 min-w-0 h-8 px-2.5 text-xs rounded border bg-background"
                  placeholder={t('settings.skills.jsonFieldsPlaceholder')}
                />
                <select
                  value={field.type}
                  onChange={(event) => updateOutputField(index, { type: event.target.value })}
                  className="w-[112px] h-8 px-2 py-1 text-xs rounded border bg-background shrink-0"
                >
                  {FIELD_TYPES.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
                {field.type === 'array' && (
                  <select
                    value={field.itemsType ?? 'string'}
                    onChange={(event) => updateOutputField(index, { itemsType: event.target.value })}
                    className="w-[112px] h-8 px-2 py-1 text-xs rounded border bg-background shrink-0"
                  >
                    {ARRAY_ITEM_TYPES.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  type="button"
                  onClick={() => removeOutputField(index)}
                  className="text-muted-foreground hover:text-red-500 p-1 rounded hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>{t('settings.skills.codeExecutor.script')}</Label>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">{t('settings.skills.codeExecutor.language')}</span>
              <select
                value={language}
                onChange={(event) =>
                  handleLanguageSwitch(
                    event.target.value === 'javascript' ? 'javascript' : 'python',
                  )
                }
                className="h-7 rounded-md border bg-background px-2 text-xs"
              >
                <option value="python">Python</option>
                <option value="javascript">JavaScript</option>
              </select>
              <button
                type="button"
                onClick={() => void handleFormat()}
                disabled={isFormatting}
                className="text-[10px] bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1 rounded transition-colors disabled:opacity-50"
              >
                {isFormatting ? `${t('messages.saving')}` : t('settings.skills.codeExecutor.format')}
              </button>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">{t('settings.skills.codeExecutor.signatureHint')}</p>
          <div className="rounded-md border bg-background/50 overflow-hidden">
            <CodeMirror
              value={codeValue}
              height="240px"
              extensions={editorExtensions}
              onChange={(value) => onUpdate({ code: value })}
              basicSetup={{
                lineNumbers: true,
                highlightActiveLine: true,
                highlightActiveLineGutter: true,
                foldGutter: true,
              }}
            />
          </div>
        </div>

        {outputNames.length > 0 ? (
          <CommonOutputList
            label={t('settings.skills.workflowOutputList')}
            outputs={outputNames}
          />
        ) : (
          <div className="text-[11px] text-muted-foreground">
            {t('settings.skills.codeExecutor.outputFieldsEmpty')}
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={isLanguageResetConfirmOpen}
        title={t('settings.skills.codeExecutor.languageResetTitle')}
        description={t('settings.skills.codeExecutor.languageResetDescription', {
          language: t('settings.skills.codeExecutor.languageResetTargetLabel', {
            language: pendingLanguageName,
          }),
        })}
        onConfirm={handleLanguageSwitchConfirm}
        onCancel={() => {
          setIsLanguageResetConfirmOpen(false)
          setPendingLanguage(null)
        }}
        confirmText={t('settings.skills.codeExecutor.languageResetConfirm')}
        cancelText={t('settings.skills.codeExecutor.languageResetCancel')}
        variant="destructive"
      />
    </>
  )
}
