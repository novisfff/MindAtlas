import { useTranslation } from 'react-i18next'
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
    return cred?.name ?? 'Unknown'
  }

  if (modelsLoading) {
    return <div className="h-10 bg-muted animate-pulse rounded-md" />
  }

  return (
    <select
      className="w-full h-10 px-3 rounded-md border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
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
