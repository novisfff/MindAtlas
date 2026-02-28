import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, ChevronDown, ChevronRight, Plus, Trash2, Type, FileText, List, CheckCircle, XCircle, MessageSquare } from 'lucide-react'
import type { InputParam } from '../../../../api/tools'
import { RichMentionInput } from '../../../RichMentionInput'
import { Label, CommonSwitch } from '../CommonInputs'
import { Switch } from '@/components/ui/switch'
import type { NodeSettingsProps } from './ToolNodeSettings'

type HumanFieldType = 'string' | 'number' | 'integer' | 'boolean' | 'array'
type HumanFieldWidget =
  | 'input'
  | 'textarea'
  | 'switch'
  | 'select'
  | 'radio'
  | 'tag_selector'
  | 'date'
  | 'time'

type HumanField = {
  name: string
  label?: string
  type: HumanFieldType
  widget?: HumanFieldWidget
  options?: string[]
  optionsTemplate?: string
  optionValueKey?: string
  allowCustom?: boolean
  placeholder?: string
  required?: boolean
  valueTemplate?: string
}

type HumanFieldPayload = {
  name: string
  label?: string
  type: HumanFieldType
  widget: HumanFieldWidget
  placeholder?: string
  required?: boolean
  valueTemplate?: string
  options?: string[]
  optionsTemplate?: string
  optionValueKey?: string
  allowCustom?: boolean
}

type OptionSourceMode = 'static' | 'dynamic'

const WIDGETS_BY_TYPE: Record<HumanFieldType, HumanFieldWidget[]> = {
  string: ['input', 'textarea', 'select', 'radio', 'date', 'time'],
  number: ['input', 'select', 'radio'],
  integer: ['input', 'select', 'radio'],
  boolean: ['switch'],
  array: ['tag_selector'],
}

const WIDGET_LABEL_KEYS: Record<HumanFieldWidget, string> = {
  input: 'settings.skills.humanInLoop.widgets.input',
  textarea: 'settings.skills.humanInLoop.widgets.textarea',
  switch: 'settings.skills.humanInLoop.widgets.switch',
  select: 'settings.skills.humanInLoop.widgets.select',
  radio: 'settings.skills.humanInLoop.widgets.radio',
  tag_selector: 'settings.skills.humanInLoop.widgets.tag_selector',
  date: 'settings.skills.humanInLoop.widgets.date',
  time: 'settings.skills.humanInLoop.widgets.time',
}

const TYPE_LABEL_KEYS: Record<HumanFieldType, string> = {
  string: 'settings.skills.humanInLoop.types.string',
  number: 'settings.skills.humanInLoop.types.number',
  integer: 'settings.skills.humanInLoop.types.integer',
  boolean: 'settings.skills.humanInLoop.types.boolean',
  array: 'settings.skills.humanInLoop.types.array',
}

function defaultWidgetForType(type: HumanFieldType): HumanFieldWidget {
  if (type === 'boolean') return 'switch'
  if (type === 'array') return 'tag_selector'
  return 'input'
}

function normalizeOptions(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  return raw.map((item) => (typeof item === 'string' ? item : ''))
}

function normalizeField(raw: Partial<HumanField>): HumanField {
  const typeValue = String(raw.type ?? 'string').trim().toLowerCase()
  const type: HumanFieldType = (
    typeValue === 'number'
    || typeValue === 'integer'
    || typeValue === 'boolean'
    || typeValue === 'array'
  ) ? (typeValue as HumanFieldType) : 'string'

  const allowedWidgets = WIDGETS_BY_TYPE[type]
  const widgetValue = String(raw.widget ?? '').trim().toLowerCase() as HumanFieldWidget
  const widget: HumanFieldWidget = allowedWidgets.includes(widgetValue)
    ? widgetValue
    : defaultWidgetForType(type)

  const options = normalizeOptions(raw.options)
  const supportsOptions = widget === 'select' || widget === 'radio' || widget === 'tag_selector'
  const supportsCustom = widget === 'tag_selector'

  return {
    name: String(raw.name ?? '').trim(),
    label: String(raw.label ?? ''),
    type,
    widget,
    options: supportsOptions ? options : [],
    optionsTemplate: supportsOptions ? String(raw.optionsTemplate ?? '') : '',
    optionValueKey: supportsOptions ? String(raw.optionValueKey ?? '') : '',
    allowCustom: supportsCustom ? Boolean(raw.allowCustom ?? true) : false,
    placeholder: String(raw.placeholder ?? ''),
    required: Boolean(raw.required ?? false),
    valueTemplate: String(raw.valueTemplate ?? ''),
  }
}

function normalizeFields(config: Record<string, unknown>): HumanField[] {
  const raw = config.fields
  if (!Array.isArray(raw)) {
    return []
  }
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    .map((item) => normalizeField({
      name: String(item.name ?? '').trim(),
      label: String(item.label ?? ''),
      type: String(item.type ?? 'string') as HumanFieldType,
      widget: String(item.widget ?? '') as HumanFieldWidget,
      options: Array.isArray(item.options) ? item.options as string[] : [],
      optionsTemplate: String(item.optionsTemplate ?? item.options_template ?? ''),
      optionValueKey: String(item.optionValueKey ?? item.option_value_key ?? ''),
      allowCustom: Boolean(item.allowCustom ?? item.allow_custom ?? true),
      placeholder: String(item.placeholder ?? ''),
      required: Boolean(item.required ?? false),
      valueTemplate: String(item.valueTemplate ?? item.value_template ?? ''),
    }))
}

function serializeField(field: HumanField): HumanFieldPayload {
  const normalized = normalizeField(field)
  const payload: HumanFieldPayload = {
    name: normalized.name,
    label: normalized.label,
    type: normalized.type,
    widget: normalized.widget ?? defaultWidgetForType(normalized.type),
    placeholder: normalized.placeholder,
    required: Boolean(normalized.required),
    valueTemplate: normalized.valueTemplate,
  }

  const supportsOptions = payload.widget === 'select' || payload.widget === 'radio' || payload.widget === 'tag_selector'
  if (supportsOptions) {
    const options = normalizeOptions(normalized.options)
    if (options.length > 0) {
      payload.options = options
    }
    const optionsTemplate = String(normalized.optionsTemplate ?? '').trim()
    if (optionsTemplate) {
      payload.optionsTemplate = optionsTemplate
    }
    const optionValueKey = String(normalized.optionValueKey ?? '').trim()
    if (optionValueKey) {
      payload.optionValueKey = optionValueKey
    }
    if (payload.widget === 'tag_selector') {
      payload.allowCustom = Boolean(normalized.allowCustom ?? true)
    }
  }

  return payload
}

export function HumanInLoopNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
  const { t } = useTranslation()
  const [optionModes, setOptionModes] = useState<Record<number, OptionSourceMode>>({})
  const [expandedFields, setExpandedFields] = useState<Record<number, boolean>>({})
  const normalizedMentionParams = useMemo(
    () => (Array.isArray(mentionParams) ? mentionParams as InputParam[] : []),
    [mentionParams],
  )

  const title = String(config.title ?? '')
  const instruction = String(config.instruction ?? '')
  const approveLabel = String(config.approveLabel ?? config.approve_label ?? '')
  const rejectLabel = String(config.rejectLabel ?? config.reject_label ?? '')
  const requireRejectComment = Boolean(config.requireRejectComment ?? config.require_reject_comment ?? true)
  const fields = normalizeFields(config)

  const commitFields = (nextFields: HumanField[]) => {
    onUpdate({ fields: nextFields.map(serializeField) })
  }

  const handleFieldUpdate = (index: number, patch: Partial<HumanField>) => {
    const next = fields.map((field, idx) => (idx === index ? normalizeField({ ...field, ...patch }) : field))
    commitFields(next)
  }

  const handleFieldOptionUpdate = (fieldIndex: number, optionIndex: number, value: string) => {
    const next = fields.map((field, idx) => {
      if (idx !== fieldIndex) return field
      const options = [...(field.options ?? [])]
      options[optionIndex] = value
      return normalizeField({ ...field, options })
    })
    commitFields(next)
  }

  const handleFieldOptionAdd = (fieldIndex: number) => {
    const next = fields.map((field, idx) => {
      if (idx !== fieldIndex) return field
      const options = [...(field.options ?? []), '']
      return normalizeField({ ...field, options })
    })
    commitFields(next)
  }

  const handleFieldOptionsTemplateUpdate = (fieldIndex: number, value: string) => {
    const next = fields.map((field, idx) => {
      if (idx !== fieldIndex) return field
      return normalizeField({ ...field, optionsTemplate: value })
    })
    commitFields(next)
  }

  const handleFieldOptionValueKeyUpdate = (fieldIndex: number, value: string) => {
    const next = fields.map((field, idx) => {
      if (idx !== fieldIndex) return field
      return normalizeField({ ...field, optionValueKey: value })
    })
    commitFields(next)
  }

  const handleFieldOptionRemove = (fieldIndex: number, optionIndex: number) => {
    const next = fields.map((field, idx) => {
      if (idx !== fieldIndex) return field
      const options = (field.options ?? []).filter((_, i) => i !== optionIndex)
      return normalizeField({ ...field, options })
    })
    commitFields(next)
  }

  const deriveOptionMode = (field: HumanField): OptionSourceMode => (
    String(field.optionsTemplate ?? '').trim() ? 'dynamic' : 'static'
  )

  const getOptionMode = (index: number, field: HumanField): OptionSourceMode => (
    optionModes[index] ?? deriveOptionMode(field)
  )

  const handleOptionModeChange = (fieldIndex: number, mode: OptionSourceMode) => {
    setOptionModes((prev) => ({
      ...prev,
      [fieldIndex]: mode,
    }))
    if (mode === 'static') {
      const next = fields.map((field, idx) => {
        if (idx !== fieldIndex) return field
        return normalizeField({ ...field, optionsTemplate: '', optionValueKey: '' })
      })
      commitFields(next)
    }
  }

  const handleFieldRemove = (index: number) => {
    const next = fields.filter((_, idx) => idx !== index)

    // Cleanup option modes
    setOptionModes((prev) => {
      const rebuilt: Record<number, OptionSourceMode> = {}
      for (const [key, value] of Object.entries(prev)) {
        const idx = Number(key)
        if (!Number.isFinite(idx) || idx === index) continue
        rebuilt[idx > index ? idx - 1 : idx] = value
      }
      return rebuilt
    })

    // Cleanup expanded states
    setExpandedFields((prev) => {
      const rebuilt: Record<number, boolean> = {}
      for (const [key, value] of Object.entries(prev)) {
        const idx = Number(key)
        if (!Number.isFinite(idx) || idx === index) continue
        rebuilt[idx > index ? idx - 1 : idx] = value
      }
      return rebuilt
    })

    commitFields(next)
  }

  const toggleFieldExpand = (index: number) => {
    setExpandedFields((prev) => ({
      ...prev,
      [index]: !(prev[index] ?? false)
    }))
  }

  const handleAddField = () => {
    const newIndex = fields.length
    setExpandedFields((prev) => ({ ...prev, [newIndex]: true }))
    commitFields([
      ...fields,
      normalizeField({
        name: '',
        label: '',
        type: 'string',
        widget: 'input',
        options: [],
        optionsTemplate: '',
        optionValueKey: '',
        allowCustom: false,
        placeholder: '',
        required: false,
        valueTemplate: '',
      }),
    ])
  }

  const renderHintIcon = (tooltip: string) => (
    <span
      className="inline-flex h-4 w-4 items-center justify-center rounded-full text-amber-600/90"
      title={tooltip}
      aria-label={tooltip}
      role="img"
      tabIndex={0}
    >
      <AlertCircle className="h-3.5 w-3.5" />
    </span>
  )

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label icon={<Type className="w-4 h-4" />}>
          {t('settings.skills.humanInLoop.title')}
        </Label>
        <input
          type="text"
          value={title}
          onChange={(e) => onUpdate({ title: e.target.value })}
          className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
          placeholder={t('settings.skills.humanInLoop.titlePlaceholder')}
        />
      </div>

      <div className="space-y-1.5">
        <Label icon={<FileText className="w-4 h-4" />}>
          {t('settings.skills.humanInLoop.instruction')}
        </Label>
        <textarea
          value={instruction}
          onChange={(e) => onUpdate({ instruction: e.target.value })}
          className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 min-h-[72px]"
          placeholder={t('settings.skills.humanInLoop.instructionPlaceholder')}
        />
      </div>

      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <Label icon={<List className="w-4 h-4" />}>
            {t('settings.skills.humanInLoop.fields')}
          </Label>
        </div>
        <div className="space-y-2.5">
          {fields.length === 0 ? (
            <div className="rounded-xl border-2 border-dashed border-slate-200 bg-slate-50/50 px-2.5 py-5 text-sm text-slate-500 text-center">
              {t('settings.skills.humanInLoop.fieldsEmpty')}
            </div>
          ) : (
            fields.map((field, index) => {
              const isExpanded = expandedFields[index] ?? false
              return (
                <div key={`${index}-${field.name}`} className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden flex flex-col relative group transition-all">
                  <div className="absolute top-0 left-0 w-1 h-full bg-slate-200 group-hover:bg-primary/50 transition-colors" />

                  {/* Header */}
                  <div
                    className={`flex items-center justify-between cursor-pointer pl-2.5 pr-2 py-2 transition-colors ${isExpanded ? 'bg-slate-50/50 border-b border-slate-100' : 'hover:bg-slate-50'}`}
                    onClick={() => toggleFieldExpand(index)}
                  >
                    <div className="flex items-center gap-2">
                      <button type="button" className="text-slate-400 hover:text-slate-600 transition-colors">
                        {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </button>
                      <span className="text-xs font-semibold text-slate-700 bg-slate-100 px-2.5 py-1.5 rounded-lg">
                        #{index + 1} {field.name || t('settings.skills.humanInLoop.fieldPlaceholder')}
                      </span>
                    </div>

                    <div className="flex items-center gap-1.5">
                      {!isExpanded && (
                        <span className="text-[11px] font-medium text-slate-500 bg-white px-2 py-1 rounded-md border border-slate-100 shadow-sm max-w-[100px] truncate">
                          {t(WIDGET_LABEL_KEYS[field.widget ?? defaultWidgetForType(field.type)])}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleFieldRemove(index)
                        }}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-600 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                        title={t('actions.delete')}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>

                  {/* Content */}
                  {isExpanded && (
                    <div className="p-3 space-y-3 animate-in fade-in slide-in-from-top-2 bg-white">
                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-slate-500">{t('settings.skills.humanInLoop.fieldName')}</label>
                          <input
                            value={field.name}
                            onChange={(e) => handleFieldUpdate(index, { name: e.target.value })}
                            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono"
                            placeholder={t('settings.skills.humanInLoop.fieldName')}
                          />
                        </div>
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-slate-500">{t('settings.skills.humanInLoop.fieldLabel')}</label>
                          <input
                            value={field.label ?? ''}
                            onChange={(e) => handleFieldUpdate(index, { label: e.target.value })}
                            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                            placeholder={t('settings.skills.humanInLoop.fieldLabel')}
                          />
                        </div>
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-slate-500">{t('settings.skills.humanInLoop.type')}</label>
                          <select
                            value={field.type}
                            onChange={(e) => handleFieldUpdate(index, { type: e.target.value as HumanFieldType })}
                            className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition-all hover:bg-slate-50 focus:bg-white focus:border-primary/50 focus:ring-2 focus:ring-primary/20 shadow-sm appearance-none cursor-pointer"
                            style={{
                              backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                              backgroundPosition: 'right 0.5rem center',
                              backgroundRepeat: 'no-repeat',
                              backgroundSize: '1.5em 1.5em',
                              paddingRight: '2.5rem'
                            }}
                          >
                            <option value="string">{t(TYPE_LABEL_KEYS.string)}</option>
                            <option value="number">{t(TYPE_LABEL_KEYS.number)}</option>
                            <option value="integer">{t(TYPE_LABEL_KEYS.integer)}</option>
                            <option value="boolean">{t(TYPE_LABEL_KEYS.boolean)}</option>
                            <option value="array">{t(TYPE_LABEL_KEYS.array)}</option>
                          </select>
                        </div>
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-slate-500">{t('settings.skills.humanInLoop.widget')}</label>
                          <select
                            value={field.widget}
                            onChange={(e) => handleFieldUpdate(index, { widget: e.target.value as HumanFieldWidget })}
                            className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition-all hover:bg-slate-50 focus:bg-white focus:border-primary/50 focus:ring-2 focus:ring-primary/20 shadow-sm appearance-none cursor-pointer"
                            style={{
                              backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                              backgroundPosition: 'right 0.5rem center',
                              backgroundRepeat: 'no-repeat',
                              backgroundSize: '1.5em 1.5em',
                              paddingRight: '2.5rem'
                            }}
                          >
                            {WIDGETS_BY_TYPE[field.type].map((widget) => (
                              <option key={widget} value={widget}>
                                {t(WIDGET_LABEL_KEYS[widget])}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>

                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-slate-500">{t('settings.skills.humanInLoop.fieldPlaceholder')}</label>
                        <input
                          value={field.placeholder ?? ''}
                          onChange={(e) => handleFieldUpdate(index, { placeholder: e.target.value })}
                          className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition-all hover:bg-slate-50 focus:bg-white focus:border-primary/50 focus:ring-2 focus:ring-primary/20 shadow-sm"
                          placeholder={t('settings.skills.humanInLoop.fieldPlaceholder')}
                        />
                      </div>
                      <div className="flex items-center gap-6 rounded-lg border border-slate-100 bg-slate-50/50 p-2.5">
                        <label className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 cursor-pointer">
                          <Switch
                            checked={Boolean(field.required)}
                            onCheckedChange={(checked) => handleFieldUpdate(index, { required: checked })}
                            className="scale-90"
                          />
                          {t('settings.skills.humanInLoop.fieldRequired')}
                        </label>
                        {field.widget === 'tag_selector' && (
                          <label className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 cursor-pointer">
                            <Switch
                              checked={Boolean(field.allowCustom ?? true)}
                              onCheckedChange={(checked) => handleFieldUpdate(index, { allowCustom: checked })}
                              className="scale-90"
                            />
                            {t('settings.skills.humanInLoop.options.allowCustom')}
                          </label>
                        )}
                      </div>
                      {(field.widget === 'select' || field.widget === 'radio' || field.widget === 'tag_selector') && (
                        <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50/70 p-2.5">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-medium text-slate-700">
                              {t('settings.skills.humanInLoop.options.sourceModeLabel')}
                            </span>
                            <div className="inline-flex items-center rounded-lg border border-slate-200 bg-white p-0.5">
                              {(['static', 'dynamic'] as const).map((mode) => {
                                const active = getOptionMode(index, field) === mode
                                return (
                                  <button
                                    key={mode}
                                    type="button"
                                    onClick={() => handleOptionModeChange(index, mode)}
                                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${active ? 'bg-primary/10 text-primary' : 'text-slate-600 hover:bg-slate-100'}`}
                                  >
                                    {mode === 'static'
                                      ? t('settings.skills.humanInLoop.options.sourceModeStatic')
                                      : t('settings.skills.humanInLoop.options.sourceModeDynamic')}
                                  </button>
                                )
                              })}
                            </div>
                          </div>

                          {getOptionMode(index, field) === 'dynamic' ? (
                            <div className="space-y-3">
                              <div className="space-y-1.5">
                                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-700">
                                  {t('settings.skills.humanInLoop.options.templateLabel')}
                                  {renderHintIcon(t('settings.skills.humanInLoop.options.templateTooltip'))}
                                </span>
                                <div className="rounded-lg border border-slate-200 bg-white p-1 transition-all hover:border-slate-300 focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20 shadow-sm">
                                  <RichMentionInput
                                    value={field.optionsTemplate ?? ''}
                                    onChange={(value) => handleFieldOptionsTemplateUpdate(index, value)}
                                    inputParams={normalizedMentionParams}
                                    placeholder={t('settings.skills.humanInLoop.options.templatePlaceholder')}
                                    className="min-h-[32px]"
                                  />
                                </div>
                                <div className="text-xs text-slate-500">
                                  {t('settings.skills.humanInLoop.options.templateHint')}
                                </div>
                              </div>

                              <div className="space-y-1.5">
                                <span className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-700">
                                  {t('settings.skills.humanInLoop.options.optionValueKeyLabel')}
                                  {renderHintIcon(t('settings.skills.humanInLoop.options.optionValueKeyTooltip'))}
                                </span>
                                <input
                                  value={field.optionValueKey ?? ''}
                                  onChange={(e) => handleFieldOptionValueKeyUpdate(index, e.target.value)}
                                  className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm outline-none transition-all hover:bg-slate-50 focus:bg-white focus:border-primary/50 focus:ring-2 focus:ring-primary/20 shadow-sm"
                                  placeholder={t('settings.skills.humanInLoop.options.optionValueKeyPlaceholder')}
                                />
                                <div className="text-xs text-slate-500">
                                  {t('settings.skills.humanInLoop.options.optionValueKeyHint')}
                                </div>
                              </div>
                            </div>
                          ) : (
                            <div className="space-y-2.5">
                              <div className="flex items-center justify-between">
                                <span className="text-xs font-medium text-slate-700">
                                  {t('settings.skills.humanInLoop.options.staticLabel')}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => handleFieldOptionAdd(index)}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 hover:text-primary transition-colors shadow-sm"
                                >
                                  <Plus className="h-3.5 w-3.5" />
                                  {t('settings.skills.humanInLoop.options.add')}
                                </button>
                              </div>
                              <div className="space-y-2">
                                {(field.options ?? []).length === 0 ? (
                                  <div className="text-xs text-slate-500 text-center py-2 border border-dashed border-slate-300 rounded-lg bg-white/50">
                                    {field.widget === 'tag_selector'
                                      ? t('settings.skills.humanInLoop.options.emptyTagSelector')
                                      : t('settings.skills.humanInLoop.options.emptySelectRadio')}
                                  </div>
                                ) : (
                                  (field.options ?? []).map((option, optionIndex) => (
                                    <div key={`${index}-opt-${optionIndex}`} className="flex items-center gap-2">
                                      <input
                                        value={option}
                                        onChange={(e) => handleFieldOptionUpdate(index, optionIndex, e.target.value)}
                                        className="flex-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm outline-none transition-all hover:bg-slate-50 focus:bg-white focus:border-primary/50 focus:ring-2 focus:ring-primary/20 shadow-sm"
                                        placeholder={t('settings.skills.humanInLoop.options.placeholder')}
                                      />
                                      <button
                                        type="button"
                                        onClick={() => handleFieldOptionRemove(index, optionIndex)}
                                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent text-slate-400 hover:bg-red-50 hover:text-red-600 hover:border-red-100 transition-colors"
                                        title={t('actions.delete')}
                                      >
                                        <Trash2 className="h-4 w-4" />
                                      </button>
                                    </div>
                                  ))
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-slate-500">{t('settings.skills.defaultValueTemplateTitle') || 'Default Value Template'}</label>
                        <div className="rounded-lg border border-slate-200 bg-white p-1 transition-all hover:border-slate-300 focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20 shadow-sm">
                          <RichMentionInput
                            value={field.valueTemplate ?? ''}
                            onChange={(value) => handleFieldUpdate(index, { valueTemplate: value })}
                            inputParams={normalizedMentionParams}
                            placeholder={t('settings.skills.argsTemplatePlaceholder')}
                            className="min-h-[36px]"
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )
            })
          )}
          <button
            type="button"
            onClick={handleAddField}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl border-2 border-dashed border-slate-200 bg-white py-2 text-xs font-medium text-slate-500 transition-colors hover:border-primary/50 hover:bg-slate-50 hover:text-primary"
          >
            <Plus className="h-4 w-4" />
            {t('settings.skills.humanInLoop.addField')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5">
        <div className="space-y-1.5">
          <Label icon={<CheckCircle className="w-4 h-4" />}>
            {t('settings.skills.humanInLoop.approveLabel')}
          </Label>
          <input
            type="text"
            value={approveLabel}
            onChange={(e) => onUpdate({ approveLabel: e.target.value })}
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
            placeholder={t('settings.skills.humanInLoop.approveLabelPlaceholder')}
          />
        </div>
        <div className="space-y-1.5">
          <Label icon={<XCircle className="w-4 h-4" />}>
            {t('settings.skills.humanInLoop.rejectLabel')}
          </Label>
          <input
            type="text"
            value={rejectLabel}
            onChange={(e) => onUpdate({ rejectLabel: e.target.value })}
            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
            placeholder={t('settings.skills.humanInLoop.rejectLabelPlaceholder')}
          />
        </div>
      </div>

      <CommonSwitch
        icon={<MessageSquare className="w-4 h-4" />}
        label={t('settings.skills.humanInLoop.requireRejectComment') || "拒绝时必须填写备注"}
        checked={requireRejectComment}
        onChange={(checked) => onUpdate({ requireRejectComment: checked })}
      />
    </div>
  )
}
