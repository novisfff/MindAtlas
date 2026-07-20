import type { ChangeEvent, KeyboardEvent } from 'react'
/**
 * Ordered multi-select of published Capability identities from shared Registry.
 * Does NOT embed Tool/Workflow/Agent target editors (Plan 09 rule).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import { cn } from '@/lib/utils'

export interface SkillCapabilityEditorProps {
  capabilityKeys: string[]
  onChange: (keys: string[]) => void
  registryKeys?: string[]
  disabled?: boolean
  className?: string
}

function parseCapabilityKeysFromYaml(yaml: string): string[] {
  const lines = yaml.split(/\r?\n/)
  const keys: string[] = []
  let inCaps = false
  for (const raw of lines) {
    const line = raw.replace(/\t/g, '  ')
    if (/^capabilities\s*:/.test(line.trimStart()) && !line.trimStart().startsWith('#')) {
      inCaps = true
      continue
    }
    if (inCaps) {
      if (/^\S/.test(line) && !line.trimStart().startsWith('#')) {
        inCaps = false
        continue
      }
      const m = line.match(/^\s*-\s+["']?([^"'#]+?)["']?\s*(?:#.*)?$/)
      if (m) keys.push(m[1].trim())
    }
  }
  return keys.filter(Boolean)
}

export function extractCapabilityKeys(yaml: string): string[] {
  return parseCapabilityKeysFromYaml(yaml)
}

export function SkillCapabilityEditor({
  capabilityKeys,
  onChange,
  registryKeys = [],
  disabled = false,
  className,
}: SkillCapabilityEditorProps) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState('')

  const suggestions = useMemo(() => {
    const existing = new Set(capabilityKeys)
    return registryKeys.filter((k) => !existing.has(k) && k.includes(draft.trim()))
  }, [capabilityKeys, registryKeys, draft])

  function addKey(key: string) {
    const normalized = key.trim()
    if (!normalized || capabilityKeys.includes(normalized)) return
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
          capabilityKeys.map((key, index) => (
            <li key={key} className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-mono">{key}</span>
              <Button type="button" size="sm" variant="ghost" disabled={disabled || index === 0} onClick={() => move(key, -1)} aria-label={t('settings.universalSkills.moveUp')}>↑</Button>
              <Button type="button" size="sm" variant="ghost" disabled={disabled || index === capabilityKeys.length - 1} onClick={() => move(key, 1)} aria-label={t('settings.universalSkills.moveDown')}>↓</Button>
              <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => removeKey(key)} aria-label={t('common.remove')}>
                <X className="h-4 w-4" />
              </Button>
            </li>
          ))
        )}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={draft}
          disabled={disabled}
          placeholder={t('settings.universalSkills.capabilityKeyPlaceholder')}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setDraft(e.target.value)}
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addKey(draft)
            }
          }}
          className={cn(uiField.input, 'max-w-md font-mono')}
        />
        <Button type="button" size="sm" disabled={disabled || !draft.trim()} onClick={() => addKey(draft)}>
          <Plus className="mr-1 h-4 w-4" />
          {t('common.add')}
        </Button>
      </div>

      {suggestions.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {suggestions.slice(0, 8).map((key) => (
            <button key={key} type="button" disabled={disabled} className="rounded-full border px-2 py-0.5 font-mono text-xs hover:bg-muted" onClick={() => addKey(key)}>
              {key}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
