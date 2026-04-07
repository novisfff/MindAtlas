import { useTranslation } from 'react-i18next'
import { uiField } from '@/components/ui/styles'
import { useModelsQuery, useCredentialsQuery } from '../queries'
import type { AiModelType } from '../api/models'

interface ModelSelectorProps {
  modelType: AiModelType
  value: string | null
  onChange: (modelId: string | null) => void
  disabled?: boolean
}

export function ModelSelector({ modelType, value, onChange, disabled }: ModelSelectorProps) {
  const { t } = useTranslation()
  const { data: models = [], isLoading: modelsLoading } = useModelsQuery({ modelType })
  const { data: credentials = [] } = useCredentialsQuery()

  const getCredentialName = (credentialId: string) => {
    const cred = credentials.find((c) => c.id === credentialId)
    return cred?.name ?? t('common.unknown')
  }

  if (modelsLoading) {
    return <div className="h-10 animate-pulse rounded-[16px] bg-muted" />
  }

  return (
    <select
      className={uiField.select}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      aria-label={t(`settings.ai.modelTypes.${modelType}`)}
    >
      <option value="">{t('settings.ai.selectModel')}</option>
      {models.map((model) => (
        <option key={model.id} value={model.id}>
          {model.name} ({getCredentialName(model.credentialId)})
        </option>
      ))}
    </select>
  )
}
