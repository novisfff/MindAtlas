import type { ChangeEvent, KeyboardEvent } from 'react'
/**
 * Ordered multi-select of published Capability identities from shared Registry.
 * Does NOT embed Tool/Workflow/Agent target editors (Plan 09 rule).
 * Free-text keys outside the Registry cannot be added.
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

export interface CapabilityRegistryEntry {
  key: string
  target?: string
  version?: string
  resolution?: string
  risk?: string
}

export interface SkillCapabilityEditorProps {
  capabilityKeys: string[]
  onChange: (keys: string[]) => void
  /** Published identity keys only. Free-text outside this set is rejected. */
  registryKeys?: string[]
  /** Optional metadata for selected identities. */
  registry?: CapabilityRegistryEntry[]
  disabled?: boolean
  className?: string
}

function parseCapabilityKeysFromYaml(yaml: string): string[] {
  const lines = yaml.split(/\r?\n/)
  const keys: string[] = []
  let inCaps = false
  let pendingType: string | null = null
  for (const raw of lines) {
    const line = raw.replace(/\t/g, '  ')
    if (/^capabilities\s*:/.test(line.trimStart()) && !line.trimStart().startsWith('#')) {
      inCaps = true
      pendingType = null
      continue
    }
    if (inCaps) {
      if (/^\S/.test(line) && !line.trimStart().startsWith('#')) {
        inCaps = false
        pendingType = null
        continue
      }
      // Structured: - type: tool / key: search_entries
      const typeMatch = line.match(/^\s*-\s+type\s*:\s*["']?([^"'#]+?)["']?\s*(?:#.*)?$/)
      if (typeMatch) {
        pendingType = typeMatch[1].trim()
        continue
      }
      const keyField = line.match(/^\s+key\s*:\s*["']?([^"'#]+?)["']?\s*(?:#.*)?$/)
      if (keyField && pendingType) {
        keys.push(`${pendingType}:${keyField[1].trim()}`)
        pendingType = null
        continue
      }
      // Flat: - tool:search_entries or - search_entries
      const m = line.match(/^\s*-\s+["']?([^"'#]+?)["']?\s*(?:#.*)?$/)
      if (m) {
        keys.push(m[1].trim())
        pendingType = null
      }
    }
  }
  return keys.filter(Boolean)
}

export function extractCapabilityKeys(yaml: string): string[] {
  return parseCapabilityKeysFromYaml(yaml)
}

export function isRegistryCapabilityKey(
  key: string,
  registryKeys: string[],
): boolean {
  const normalized = key.trim()
  if (!normalized) return false
  return registryKeys.includes(normalized)
}

export function SkillCapabilityEditor({
  capabilityKeys,
  onChange,
  registryKeys = [],
  registry = [],
  disabled = false,
  className,
}: SkillCapabilityEditorProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState('')

  const registryMeta = useMemo(() => {
    const map = new Map<string, CapabilityRegistryEntry>()
    for (const entry of registry) map.set(entry.key, entry)
    for (const key of registryKeys) {
      if (!map.has(key)) map.set(key, { key })
    }
    return map
  }, [registry, registryKeys])

  const allowedKeys = useMemo(() => {
    if (registryKeys.length > 0) return registryKeys
    return Array.from(registryMeta.keys())
  }, [registryKeys, registryMeta])

  const suggestions = useMemo(() => {
    const existing = new Set(capabilityKeys)
    const q = draft.trim().toLowerCase()
    return allowedKeys.filter((k) => !existing.has(k) && (!q || k.toLowerCase().includes(q)))
  }, [capabilityKeys, allowedKeys, draft])

  const canAdd = isRegistryCapabilityKey(draft, allowedKeys) && !capabilityKeys.includes(draft.trim())

  function addKey(key: string) {
    const normalized = key.trim()
    if (!isRegistryCapabilityKey(normalized, allowedKeys)) return
    if (capabilityKeys.includes(normalized)) return
    onChange([...capabilityKeys, normalized])
    setDraft('')
  }

  function removeKey(key: string) {
    onChange(capabilityKeys.filter((k) => k !== key))
  }

  function move(key: string, delta: number) {
    const idx = capabilityKeys.indexOf(key)
    if (idx < 0) return
    const next = [...capabilityKeys]
    const target = idx + delta
    if (target < 0 || target >= next.length) return
    const [item] = next.splice(idx, 1)
    next.splice(target, 0, item)
    onChange(next)
  }

  return (
    <div className={cn('space-y-3', className)}>
      <p className="text-sm text-muted-foreground">{t('settings.universalSkills.capabilitiesHint')}</p>

      <ul className="space-y-2" aria-label={t('settings.universalSkills.capabilities')}>
        {capabilityKeys.length === 0 ? (
          <li className="text-sm text-muted-foreground">{t('settings.universalSkills.noCapabilities')}</li>
        ) : (
          capabilityKeys.map((key, index) => {
            const meta = registryMeta.get(key)
            return (
              <li key={key} className="flex flex-col gap-1 rounded-md border px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono">{key}</span>
                  <Button type="button" size="sm" variant="ghost" disabled={disabled || index === 0} onClick={() => move(key, -1)} aria-label={t('settings.universalSkills.moveUp')}>↑</Button>
                  <Button type="button" size="sm" variant="ghost" disabled={disabled || index === capabilityKeys.length - 1} onClick={() => move(key, 1)} aria-label={t('settings.universalSkills.moveDown')}>↓</Button>
                  <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => removeKey(key)} aria-label={t('common.remove')}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                {meta && (meta.target || meta.version || meta.resolution || meta.risk) ? (
                  <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                    {meta.target ? (
                      <span>
                        {t('settings.universalSkills.capabilityTarget')}: {meta.target}
                      </span>
                    ) : null}
                    {meta.version ? (
                      <span>
                        {t('settings.universalSkills.capabilityVersion')}: {meta.version}
                      </span>
                    ) : null}
                    {meta.resolution ? (
                      <span>
                        {t('settings.universalSkills.capabilityResolution')}: {meta.resolution}
                      </span>
                    ) : null}
                    {meta.risk ? (
                      <span>
                        {t('settings.universalSkills.capabilityRisk')}: {meta.risk}
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </li>
            )
          })
        )}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <input
          role="combobox"
          aria-expanded={suggestions.length > 0}
          aria-controls="capability-suggestions"
          list="capability-registry-options"
          value={draft}
          disabled={disabled || allowedKeys.length === 0}
          placeholder={t('settings.universalSkills.capabilityKeyPlaceholder')}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setDraft(e.target.value)}
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (canAdd) addKey(draft)
            }
          }}
          className={cn(uiField.input, 'max-w-md font-mono')}
        />
        <datalist id="capability-registry-options">
          {allowedKeys.map((key) => (
            <option key={key} value={key} />
          ))}
        </datalist>
        <Button type="button" size="sm" disabled={disabled || !canAdd} onClick={() => addKey(draft)}>
          <Plus className="mr-1 h-4 w-4" />
          {t('common.add')}
        </Button>
      </div>

      {suggestions.length > 0 ? (
        <div id="capability-suggestions" className="flex flex-wrap gap-2">
          {suggestions.slice(0, 8).map((key) => (
            <button
              key={key}
              type="button"
              disabled={disabled}
              className="rounded-full border px-2 py-0.5 font-mono text-xs hover:bg-muted"
              onClick={() => addKey(key)}
            >
              {key}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
