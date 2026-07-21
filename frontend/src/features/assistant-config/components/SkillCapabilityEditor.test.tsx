import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SkillCapabilityEditor } from './SkillCapabilityEditor'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'settings.universalSkills.capabilitiesHint':
          'Select published Capability identities from the shared Registry.',
        'settings.universalSkills.capabilities': 'Capabilities',
        'settings.universalSkills.noCapabilities': 'No capability keys declared.',
        'settings.universalSkills.capabilityKeyPlaceholder': 'capability.key',
        'settings.universalSkills.moveUp': 'Move up',
        'settings.universalSkills.moveDown': 'Move down',
        'settings.universalSkills.capabilityTarget': 'Target',
        'settings.universalSkills.capabilityVersion': 'Version',
        'settings.universalSkills.capabilityResolution': 'Resolution',
        'settings.universalSkills.capabilityRisk': 'Risk',
        'common.add': 'Add',
        'common.remove': 'Remove',
      }
      return map[key] ?? key
    },
    i18n: { language: 'en' },
  }),
}))

function renderCapabilityEditor(options?: {
  registryKeys?: string[]
  capabilityKeys?: string[]
  onChange?: (keys: string[]) => void
  registry?: Array<{
    key: string
    target?: string
    version?: string
    resolution?: string
    risk?: string
  }>
}) {
  const onChange = options?.onChange ?? vi.fn()
  return {
    onChange,
    ...render(
      <SkillCapabilityEditor
        capabilityKeys={options?.capabilityKeys ?? []}
        onChange={onChange}
        registryKeys={options?.registryKeys ?? options?.registry?.map((r) => r.key) ?? []}
        registry={options?.registry}
      />,
    ),
  }
}

describe('SkillCapabilityEditor', () => {
  it('rejects free-text capability keys outside the Registry', () => {
    renderCapabilityEditor({ registryKeys: ['tool:published'] })

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tool:unknown' } })
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
  })

  it('adds only Registry identities and preserves order', () => {
    const { onChange } = renderCapabilityEditor({
      registryKeys: ['tool:alpha', 'tool:beta'],
      capabilityKeys: ['tool:alpha'],
    })

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tool:beta' } })
    expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(onChange).toHaveBeenCalledWith(['tool:alpha', 'tool:beta'])
  })

  it('shows target/version/resolution/risk metadata for selected identities', () => {
    renderCapabilityEditor({
      capabilityKeys: ['tool:published'],
      registry: [
        {
          key: 'tool:published',
          target: 'search_entries',
          version: 'v3',
          resolution: 'pinned',
          risk: 'read',
        },
      ],
    })

    expect(screen.getByText('tool:published')).toBeVisible()
    expect(screen.getByText(/search_entries/)).toBeVisible()
    expect(screen.getByText(/v3/)).toBeVisible()
    expect(screen.getByText(/pinned/)).toBeVisible()
    expect(screen.getByText(/read/)).toBeVisible()
  })
})