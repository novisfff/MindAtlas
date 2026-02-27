import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import type { HumanApprovalFieldSchema, HumanApprovalFieldWidget } from './types'

interface HumanApprovalFieldFormProps {
  fields: HumanApprovalFieldSchema[]
  values: Record<string, unknown>
  disabled?: boolean
  onChange: (name: string, value: unknown) => void
}

function defaultWidgetForType(type: HumanApprovalFieldSchema['type']): HumanApprovalFieldWidget {
  if (type === 'boolean') return 'switch'
  if (type === 'array') return 'tag_selector'
  return 'input'
}

function normalizeWidget(field: HumanApprovalFieldSchema): HumanApprovalFieldWidget {
  const raw = String(field.widget ?? '').trim().toLowerCase() as HumanApprovalFieldWidget
  if (raw) return raw
  return defaultWidgetForType(field.type)
}

function normalizeOptions(field: HumanApprovalFieldSchema): string[] {
  if (!Array.isArray(field.options)) return []
  const deduped: string[] = []
  const seen = new Set<string>()
  field.options.forEach((item) => {
    if (typeof item !== 'string') return
    const text = item.trim()
    if (!text || seen.has(text)) return
    seen.add(text)
    deduped.push(text)
  })
  return deduped
}

function toStringValue(raw: unknown): string {
  if (raw === null || raw === undefined) return ''
  if (typeof raw === 'string') return raw
  if (typeof raw === 'number' || typeof raw === 'boolean') return String(raw)
  return ''
}

function toBooleanValue(raw: unknown): boolean {
  if (typeof raw === 'boolean') return raw
  const text = String(raw ?? '').trim().toLowerCase()
  return text === 'true' || text === '1'
}

function toStringArray(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw
      .map((item) => String(item ?? '').trim())
      .filter(Boolean)
  }
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return []
    return text.split(',').map((item) => item.trim()).filter(Boolean)
  }
  return []
}

interface TagSelectorFieldProps {
  field: HumanApprovalFieldSchema
  value: unknown
  disabled: boolean
  onChange: (next: string[]) => void
}

function TagSelectorField({ field, value, disabled, onChange }: TagSelectorFieldProps) {
  const { t } = useTranslation()
  const [draftTag, setDraftTag] = useState('')
  const selected = toStringArray(value)
  const options = normalizeOptions(field)
  const allowCustom = field.allowCustom ?? true

  const addTag = (tag: string) => {
    const text = tag.trim()
    if (!text) return
    if (selected.includes(text)) return
    onChange([...selected, text])
  }

  const removeTag = (tag: string) => {
    onChange(selected.filter((item) => item !== tag))
  }

  const toggleOption = (option: string) => {
    if (selected.includes(option)) {
      removeTag(option)
      return
    }
    addTag(option)
  }

  return (
    <div className="space-y-2">
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-700"
            >
              {tag}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => removeTag(tag)}
                  className="text-slate-500 hover:text-red-600"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {options.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {options.map((option) => {
            const active = selected.includes(option)
            return (
              <button
                key={option}
                type="button"
                disabled={disabled}
                onClick={() => toggleOption(option)}
                className={`rounded-full border px-2 py-1 text-[11px] ${
                  active
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                {option}
              </button>
            )
          })}
        </div>
      )}

      {allowCustom && (
        <div className="flex items-center gap-2">
          <input
            value={draftTag}
            onChange={(e) => setDraftTag(e.target.value)}
            disabled={disabled}
            placeholder={t('settings.skills.humanApproval.tagInputPlaceholder')}
            className="flex-1 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                if (disabled) return
                addTag(draftTag)
                setDraftTag('')
              }
            }}
          />
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              if (disabled) return
              addTag(draftTag)
              setDraftTag('')
            }}
            className="rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:bg-slate-100 disabled:text-slate-500"
          >
            {t('settings.skills.humanApproval.addTag')}
          </button>
        </div>
      )}
    </div>
  )
}

export function HumanApprovalFieldForm({
  fields,
  values,
  disabled = false,
  onChange,
}: HumanApprovalFieldFormProps) {
  return (
    <div className="space-y-2.5">
      {fields.map((field) => {
        const widget = normalizeWidget(field)
        const value = values[field.name]
        const options = normalizeOptions(field)
        const placeholder = String(field.placeholder ?? '').trim() || undefined
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-[11px] font-medium text-slate-700">
              {field.label?.trim() || field.name}
              {field.required ? ' *' : ''}
            </label>

            {widget === 'switch' ? (
              <label className="inline-flex items-center gap-2 text-xs text-slate-700">
                <input
                  type="checkbox"
                  checked={toBooleanValue(value)}
                  onChange={(e) => onChange(field.name, e.target.checked)}
                  disabled={disabled}
                />
                {toBooleanValue(value) ? 'true' : 'false'}
              </label>
            ) : null}

            {widget === 'textarea' ? (
              <textarea
                value={toStringValue(value)}
                onChange={(e) => onChange(field.name, e.target.value)}
                disabled={disabled}
                placeholder={placeholder}
                className="min-h-[72px] w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
              />
            ) : null}

            {widget === 'select' ? (
              <select
                value={toStringValue(value)}
                onChange={(e) => onChange(field.name, e.target.value)}
                disabled={disabled}
                className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
              >
                <option value="">-</option>
                {options.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            ) : null}

            {widget === 'radio' ? (
              <div className="space-y-1">
                {options.map((option) => (
                  <label key={option} className="inline-flex items-center gap-2 text-xs text-slate-700">
                    <input
                      type="radio"
                      name={`approval-${field.name}`}
                      checked={toStringValue(value) === option}
                      onChange={() => onChange(field.name, option)}
                      disabled={disabled}
                    />
                    {option}
                  </label>
                ))}
              </div>
            ) : null}

            {widget === 'tag_selector' ? (
              <TagSelectorField
                field={field}
                value={value}
                disabled={disabled}
                onChange={(next) => onChange(field.name, next)}
              />
            ) : null}

            {widget === 'date' ? (
              <input
                type="date"
                value={toStringValue(value)}
                onChange={(e) => onChange(field.name, e.target.value)}
                disabled={disabled}
                className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
              />
            ) : null}

            {widget === 'time' ? (
              <input
                type="time"
                value={toStringValue(value)}
                onChange={(e) => onChange(field.name, e.target.value)}
                disabled={disabled}
                className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
              />
            ) : null}

            {widget === 'input' ? (
              <input
                type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                step={field.type === 'integer' ? '1' : 'any'}
                value={toStringValue(value)}
                onChange={(e) => onChange(field.name, e.target.value)}
                disabled={disabled}
                placeholder={placeholder}
                className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-100 disabled:text-slate-500"
              />
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
