import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { SkillManager } from '../components/SkillManager'
import { SettingsPageHeader, SettingsPageShell, SettingsSection } from '@/features/settings/components/SettingsShell'

export function SkillSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.assistantSkills')}
        description={t('pages.settings.assistantSkillsDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection>
        <SkillManager />
      </SettingsSection>
    </SettingsPageShell>
  )
}
