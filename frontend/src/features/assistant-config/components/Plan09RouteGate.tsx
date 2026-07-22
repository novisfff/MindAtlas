/**
 * Fail-closed gate for Plan 09 Universal Skills / Profile routes.
 * Waits for the server feature/principal probe before rendering children.
 * Missing feature or principal never mounts protected package queries.
 */
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'

import { useSkillAdminSurfaceQuery } from '../queries'

export interface Plan09RouteGateProps {
  children: ReactNode
  /** Optional title override for the unavailable surface. */
  titleKey?: string
}

export function Plan09RouteGate({
  children,
  titleKey = 'settings.universalSkills.title',
}: Plan09RouteGateProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const surface = useSkillAdminSurfaceQuery()

  if (surface.isLoading) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t(titleKey)}
          description={t('messages.loading')}
        />
      </SettingsPageShell>
    )
  }

  // Fail closed: missing feature/principal (available=false) never mounts children.
  // Probe errors also fail closed.
  if (surface.isError || !surface.data?.available) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t(titleKey)}
          description={t('settings.universalSkills.unavailableDesc')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
        <SettingsSection>
          <div
            role="alert"
            className="rounded-md border border-dashed p-6 text-sm text-muted-foreground"
          >
            <p className="font-medium text-foreground">
              {t('settings.universalSkills.unavailableDesc')}
            </p>
            <p className="mt-2">{t('settings.universalSkills.unavailableBody')}</p>
          </div>
        </SettingsSection>
      </SettingsPageShell>
    )
  }

  return <>{children}</>
}

/** True when Plan 09 navigation tiles may be shown. */
export function isPlan09NavigationAllowed(
  surface: { available?: boolean } | null | undefined,
): boolean {
  return Boolean(surface?.available)
}
