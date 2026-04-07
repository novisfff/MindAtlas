import { useNavigate } from 'react-router-dom'
import { TagManager } from '../components/TagManager'

import { useTranslation } from 'react-i18next'
import { SettingsPageHeader, SettingsPageShell, SettingsSection } from '@/features/settings/components/SettingsShell'

export function TagSettings() {
  const navigate = useNavigate()
  const { t } = useTranslation()

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('settings.tags.title')}
        description={t('settings.tags.subtitle')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection>
        <TagManager />
      </SettingsSection>
    </SettingsPageShell>
  )
}
