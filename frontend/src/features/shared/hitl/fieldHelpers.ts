import type {
  HumanApprovalFieldSchema,
  HumanApprovalFieldWidget,
  HumanApprovalOption,
} from './types'

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/

export type NormalizedOption = {
  value: string
  label: string
  description?: string
}

export type FieldCollectErrorCode =
  | 'required'
  | 'select'
  | 'radio'
  | 'checkbox_group'
  | 'tag'
  | 'date'
  | 'time'
  | 'invalid_type'

export type FieldCollectResult =
  | { ok: true; values: Record<string, unknown> }
  | { ok: false; code: FieldCollectErrorCode; field: HumanApprovalFieldSchema }

export function normalizeDateText(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''
  const normalized = text.replace(/[/.]/g, '-')
  if (!DATE_RE.test(normalized)) return ''
  const [yearText, monthText, dayText] = normalized.split('-')
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return ''
  const check = new Date(Date.UTC(year, month - 1, day))
  if (
    check.getUTCFullYear() !== year
    || check.getUTCMonth() !== month - 1
    || check.getUTCDate() !== day
  ) {
    return ''
  }
  return `${yearText.padStart(4, '0')}-${monthText.padStart(2, '0')}-${dayText.padStart(2, '0')}`
}

export function normalizeTimeText(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''
  const short = text.slice(0, 5)
  return TIME_RE.test(short) ? short : ''
}

export function defaultWidgetForType(type: HumanApprovalFieldSchema['type']): HumanApprovalFieldWidget {
  if (type === 'boolean') return 'switch'
  if (type === 'array') return 'tag_selector'
  return 'input'
}

export function normalizeWidget(field: HumanApprovalFieldSchema): HumanApprovalFieldWidget {
  const raw = String(field.widget ?? '').trim().toLowerCase() as HumanApprovalFieldWidget
  if (raw) return raw
  return defaultWidgetForType(field.type)
}

export function normalizeOptions(field: HumanApprovalFieldSchema): NormalizedOption[] {
  if (!Array.isArray(field.options)) return []
  const deduped: NormalizedOption[] = []
  const seen = new Set<string>()
  field.options.forEach((item: HumanApprovalOption) => {
    let normalized: NormalizedOption | null = null
    if (typeof item === 'string') {
      const text = item.trim()
      if (text) {
        normalized = { value: text, label: text }
      }
    } else if (item && typeof item === 'object') {
      const value = String(item.value ?? '').trim()
      const label = String(item.label ?? value).trim()
      const description = String(item.description ?? '').trim()
      if (value && label) {
        normalized = {
          value,
          label,
          ...(description ? { description } : {}),
        }
      }
    }
    if (!normalized || seen.has(normalized.value)) return
    seen.add(normalized.value)
    deduped.push(normalized)
  })
  return deduped
}

export function toStringArray(raw: unknown): string[] {
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

export function normalizeInitialValue(field: HumanApprovalFieldSchema, raw: unknown): unknown {
  const widget = normalizeWidget(field)
  if (widget === 'tag_selector' || field.type === 'array') {
    return toStringArray(raw)
  }
  if (widget === 'switch' || field.type === 'boolean') {
    if (typeof raw === 'boolean') return raw
    return String(raw ?? '').trim().toLowerCase() === 'true'
  }
  if (widget === 'date') {
    return normalizeDateText(raw)
  }
  if (widget === 'time') {
    return normalizeTimeText(raw)
  }
  if (raw === null || raw === undefined) return ''
  return typeof raw === 'string' ? raw : String(raw)
}

/** Build form values from a field schema + base value map. */
export function buildEditableFieldValues(
  fields: HumanApprovalFieldSchema[],
  baseValues: Record<string, unknown>,
): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  fields.forEach((field) => {
    values[field.name] = normalizeInitialValue(field, baseValues[field.name])
  })
  return values
}

export function isEmptyValue(field: HumanApprovalFieldSchema, raw: unknown): boolean {
  if (raw === null || raw === undefined) return true
  if (field.type === 'array' || normalizeWidget(field) === 'tag_selector') {
    return toStringArray(raw).length === 0
  }
  if (field.type === 'boolean' || normalizeWidget(field) === 'switch') {
    return false
  }
  return String(raw).trim() === ''
}

export function coerceFieldValue(field: HumanApprovalFieldSchema, raw: unknown): unknown {
  const widget = normalizeWidget(field)
  const options = normalizeOptions(field)
  const optionValues = options.map((item) => item.value)

  if (field.type === 'string') {
    const value = typeof raw === 'string' ? raw : String(raw ?? '')
    if (widget === 'date') {
      const rawText = value.trim()
      if (!rawText) {
        return ''
      }
      const normalized = normalizeDateText(rawText)
      if (!normalized) {
        throw new Error('date')
      }
      return normalized
    }
    if (widget === 'time') {
      const rawText = value.trim()
      if (!rawText) {
        return ''
      }
      const normalized = normalizeTimeText(rawText)
      if (!normalized) {
        throw new Error('time')
      }
      return normalized
    }
    if (widget === 'select' || widget === 'radio') {
      const normalized = value.trim()
      if (!optionValues.includes(normalized)) {
        throw new Error(widget)
      }
      return normalized
    }
    return value
  }

  if (field.type === 'array') {
    const values = toStringArray(raw)
    if (widget === 'checkbox_group') {
      const invalid = values.filter((item) => !optionValues.includes(item))
      if (invalid.length > 0) {
        throw new Error('checkbox_group')
      }
      return Array.from(new Set(values))
    }
    if (widget === 'tag_selector') {
      const allowCustom = field.allowCustom ?? true
      if (!allowCustom && optionValues.length > 0) {
        const unknown = values.filter((item) => !optionValues.includes(item))
        if (unknown.length > 0) {
          throw new Error('tag')
        }
      }
    }
    return values
  }

  const text = String(raw ?? '').trim()
  if (field.type === 'boolean') {
    if (typeof raw === 'boolean') return raw
    if (text === 'true') return true
    if (text === 'false') return false
    throw new Error('boolean')
  }
  if (field.type === 'integer') {
    const parsed = Number(text)
    if (!Number.isInteger(parsed)) {
      throw new Error('integer')
    }
    if (widget === 'select' || widget === 'radio') {
      const candidates = [String(parsed), String(Math.trunc(parsed))]
      if (!candidates.some((item) => optionValues.includes(item))) {
        throw new Error(widget)
      }
    }
    return parsed
  }
  if (field.type === 'number') {
    const parsed = Number(text)
    if (!Number.isFinite(parsed)) {
      throw new Error('number')
    }
    if (widget === 'select' || widget === 'radio') {
      const candidates = [String(parsed), Number.isInteger(parsed) ? String(Math.trunc(parsed)) : '']
      if (!candidates.some((item) => item && optionValues.includes(item))) {
        throw new Error(widget)
      }
    }
    return parsed
  }
  return raw ?? ''
}

/**
 * Coerce + validate field values for submit. Pure — callers map error codes to UX.
 */
export function collectFieldValues(
  fields: HumanApprovalFieldSchema[],
  values: Record<string, unknown>,
): FieldCollectResult {
  const payloadValues: Record<string, unknown> = {}
  for (const field of fields) {
    const raw = values[field.name]
    const widget = normalizeWidget(field)
    if (isEmptyValue(field, raw)) {
      if (field.required) {
        return { ok: false, code: 'required', field }
      }
      if (widget === 'date' || widget === 'time') {
        payloadValues[field.name] = ''
      }
      continue
    }
    try {
      payloadValues[field.name] = coerceFieldValue(field, raw)
    } catch (error) {
      const code = error instanceof Error ? error.message : ''
      if (
        code === 'select'
        || code === 'radio'
        || code === 'checkbox_group'
        || code === 'tag'
        || code === 'date'
        || code === 'time'
      ) {
        return { ok: false, code, field }
      }
      return { ok: false, code: 'invalid_type', field }
    }
  }
  return { ok: true, values: payloadValues }
}
