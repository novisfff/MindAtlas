import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ToolCallDisplay } from './ToolCallDisplay'
import { UnsupportedCapabilityNotice } from './UnsupportedCapabilityNotice'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { action?: string }) =>
      values?.action ? `${key}:${values.action}` : key,
  }),
}))

describe('UnsupportedCapabilityNotice', () => {
  it('renders fixed guidance for a known unsupported action', () => {
    render(<UnsupportedCapabilityNotice action="update_entry" />)

    expect(screen.getByRole('alert')).toHaveTextContent('update_entry')
    expect(screen.getByRole('alert')).toHaveTextContent('preGaLaunch.unsupportedCapability.description')
    expect(screen.getByRole('alert')).not.toHaveTextContent(/replacement|retry|relation fallback/i)
  })

  it('collapses unknown actions to generic safe copy', () => {
    render(<UnsupportedCapabilityNotice action="sentinel-password" />)

    expect(screen.getByRole('alert')).toHaveTextContent('unknown')
    expect(screen.getByRole('alert')).not.toHaveTextContent('sentinel-password')
  })

  it('does not render raw arguments or results for an unsupported ToolCall', () => {
    render(
      <ToolCallDisplay
        toolCalls={[{
          id: 'tool-1',
          name: 'update_entry',
          args: { title: 'sentinel-entry-title', password: 'sentinel-password' },
          result: 'capability_not_supported: sentinel-provider-prompt',
          status: 'error',
        }]}
      />,
    )

    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByTestId('unsupported-capability-notice')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('sentinel-entry-title')
    expect(document.body.textContent).not.toContain('sentinel-password')
    expect(document.body.textContent).not.toContain('sentinel-provider-prompt')
  })
})
