import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { SkillManager } from '../components/SkillManager'
import { SettingsPageHeader, SettingsPageShell, SettingsSection } from '@/features/settings/components/SettingsShell'
import { useSkillAdminSurfaceQuery } from '../queries'

export function SkillSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const surface = useSkillAdminSurfaceQuery()

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.assistantSkillsLegacy')}
        description={t('pages.settings.assistantSkillsLegacyDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection className="space-y-4">
        {surface.data?.available ? (
          <div className="rounded-md border border-dashed bg-muted/20 p-3 text-sm text-muted-foreground">
            <Link to="/settings/universal-skills" className="text-primary underline">
              {t('pages.settings.universalSkills')}
            </Link>
          </div>
        ) : null}
        <SkillManager />
      </SettingsSection>
    </SettingsPageShell>
  )
}
