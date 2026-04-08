import { useNavigate } from 'react-router-dom'
import { TypeManager } from '../components/TypeManager'

import { useTranslation } from 'react-i18next'
import { SettingsPageHeader, SettingsPageShell, SettingsSection } from '@/features/settings/components/SettingsShell'

export function EntryTypeSettings() {
  const navigate = useNavigate()
  const { t } = useTranslation()

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('settings.entryTypes.title')}
        description={t('settings.entryTypes.subtitle')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection>
        <TypeManager />
      </SettingsSection>
    </SettingsPageShell>
  )
}
