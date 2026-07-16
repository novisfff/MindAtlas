import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { normalizeOptions, normalizeWidget, toStringArray } from './fieldHelpers'
import type { HumanApprovalFieldSchema } from './types'

interface HumanApprovalFieldFormProps {
  fields: HumanApprovalFieldSchema[]
  values: Record<string, unknown>
  disabled?: boolean
  onChange: (name: string, value: unknown) => void
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
            const active = selected.includes(option.value)
            return (
              <button
                key={option.value}
                type="button"
                disabled={disabled}
                onClick={() => toggleOption(option.value)}
                className={`rounded-full border px-2 py-1 text-[11px] ${
                  active
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                {option.label}
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
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            ) : null}

            {widget === 'radio' ? (
              <div className="space-y-1">
                {options.map((option) => (
                  <label key={option.value} className="flex items-start gap-2 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700">
                    <input
                      type="radio"
                      name={`approval-${field.name}`}
                      checked={toStringValue(value) === option.value}
                      onChange={() => onChange(field.name, option.value)}
                      disabled={disabled}
                      className="mt-0.5"
                    />
                    <span className="min-w-0">
                      <span className="block font-medium text-slate-700">{option.label}</span>
                      {option.description ? (
                        <span className="mt-0.5 block text-[11px] text-slate-500">{option.description}</span>
                      ) : null}
                    </span>
                  </label>
                ))}
              </div>
            ) : null}

            {widget === 'checkbox_group' ? (
              <div className="space-y-1.5">
                {options.map((option) => {
                  const selectedValues = new Set(toStringArray(value))
                  const checked = selectedValues.has(option.value)
                  return (
                    <label key={option.value} className="flex items-start gap-2 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700">
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        className="mt-0.5"
                        onChange={(event) => {
                          const current = toStringArray(value)
                          if (event.target.checked) {
                            onChange(field.name, [...new Set([...current, option.value])])
                            return
                          }
                          onChange(field.name, current.filter((item) => item !== option.value))
                        }}
                      />
                      <span className="min-w-0">
                        <span className="block font-medium text-slate-700">{option.label}</span>
                        {option.description ? (
                          <span className="mt-0.5 block text-[11px] text-slate-500">{option.description}</span>
                        ) : null}
                      </span>
                    </label>
                  )
                })}
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
