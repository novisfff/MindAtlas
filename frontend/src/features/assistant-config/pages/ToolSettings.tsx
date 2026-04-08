import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ToolManager } from '../components/ToolManager'
import { SettingsPageHeader, SettingsPageShell, SettingsSection } from '@/features/settings/components/SettingsShell'

export function ToolSettings() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('pages.settings.assistantTools')}
        description={t('pages.settings.assistantToolsDesc')}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection>
        <ToolManager />
      </SettingsSection>
    </SettingsPageShell>
  )
}
