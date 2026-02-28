import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { InputParam } from '../../../../api/tools'
import { RichMentionInput } from '../../../RichMentionInput'
import type { NodeSettingsProps } from './ToolNodeSettings'
import { Label } from '../CommonInputs'
import { Box, Settings2, FileText } from 'lucide-react'

type VariableAssignOperation = 'set' | 'increment' | 'append' | 'clear'
type EnvParamType = 'string' | 'number' | 'boolean' | 'object' | 'array' | 'unknown'

function normalizeEnvParamType(raw: string): EnvParamType {
  const value = String(raw ?? '').trim().toLowerCase()
  if (value === 'string') return 'string'
  if (value === 'number' || value === 'integer') return 'number'
  if (value === 'boolean') return 'boolean'
  if (value === 'object') return 'object'
  if (value === 'array') return 'array'
  return 'unknown'
}

function allowedOperationsByType(envType?: EnvParamType): VariableAssignOperation[] {
  if (!envType || envType === 'unknown') {
    return ['set', 'increment', 'append', 'clear']
  }
  if (envType === 'number') return ['set', 'increment', 'clear']
  if (envType === 'string') return ['set', 'append', 'clear']
  if (envType === 'array') return ['set', 'append', 'clear']
  return ['set', 'clear']
}

function extractEnvOptions(params: InputParam[]): Array<{ label: string; value: string; envType: EnvParamType }> {
  const seen = new Set<string>()
  const options: Array<{ label: string; value: string; envType: EnvParamType }> = []
  params.forEach((item) => {
    const path = String(item.referencePath ?? item.name ?? '').trim()
    if (!path.startsWith('env.')) return
    const envName = path.slice('env.'.length).trim()
    if (!envName || seen.has(envName)) return
    seen.add(envName)
    options.push({
      label: envName,
      value: envName,
      envType: normalizeEnvParamType(item.paramType),
    })
  })
  return options.sort((a, b) => a.label.localeCompare(b.label))
}

export function VariableAssignNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
  const { t } = useTranslation()
  const normalizedMentionParams = Array.isArray(mentionParams) ? (mentionParams as InputParam[]) : []
  const envOptions = useMemo(
    () => extractEnvOptions(normalizedMentionParams),
    [normalizedMentionParams],
  )

  const variableName = String(config.variableName ?? config.variable_name ?? '').trim()
  const hasVariableSelected = variableName.length > 0
  const operation = String(config.operation ?? 'set').trim().toLowerCase() || 'set'
  const valueTemplate = String(config.valueTemplate ?? config.value_template ?? '')
  const envTypeByName = useMemo(
    () => new Map(envOptions.map((item) => [item.value, item.envType])),
    [envOptions],
  )
  const selectedEnvType = variableName ? envTypeByName.get(variableName) : undefined
  const allowedOperations = useMemo(
    () => allowedOperationsByType(selectedEnvType),
    [selectedEnvType],
  )
  const operationOptions = useMemo(
    () =>
      allowedOperations.map((op) => ({
        label: t(`settings.skills.variableAssign.operations.${op}`),
        value: op,
      })),
    [allowedOperations, t],
  )

  useEffect(() => {
    if (!allowedOperations.includes(operation as VariableAssignOperation)) {
      onUpdate({ operation: 'set' })
    }
  }, [allowedOperations, onUpdate, operation])

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label icon={<Box className="w-4 h-4" />}>{t('settings.skills.variableAssign.variable')}</Label>
        <select
          value={variableName}
          onChange={(e) => onUpdate({ variableName: e.target.value })}
          className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all appearance-none cursor-pointer"
          style={{
            backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
            backgroundPosition: 'right 0.75rem center',
            backgroundRepeat: 'no-repeat',
            backgroundSize: '1.2em 1.2em',
            paddingRight: '2.5rem'
          }}
        >
          <option value="" disabled className="text-slate-400">
            {t('settings.skills.variableAssign.variablePlaceholder')}
          </option>
          {envOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        {envOptions.length === 0 && (
          <div className="mt-1.5 text-xs text-slate-500 bg-slate-50 rounded-lg border border-dashed border-slate-300 px-2.5 py-3 text-center">
            {t('settings.skills.variableAssign.noEnvVars')}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label icon={<Settings2 className="w-4 h-4" />}>{t('settings.skills.variableAssign.operation')}</Label>
        <select
          value={operation}
          onChange={(e) => onUpdate({ operation: e.target.value || 'set' })}
          disabled={!hasVariableSelected}
          className={`w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 outline-none transition-all appearance-none ${!hasVariableSelected ? 'bg-slate-50 cursor-not-allowed opacity-60' : 'bg-white hover:bg-slate-50 focus:ring-2 focus:ring-primary/20 focus:border-primary/50 cursor-pointer'}`}
          style={{
            backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
            backgroundPosition: 'right 0.75rem center',
            backgroundRepeat: 'no-repeat',
            backgroundSize: '1.2em 1.2em',
            paddingRight: '2.5rem'
          }}
        >
          {operationOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>

      {hasVariableSelected && operation !== 'clear' && (
        <div className="space-y-1.5">
          <Label icon={<FileText className="w-4 h-4" />}>{t('settings.skills.variableAssign.valueTemplate')}</Label>
          <div className="rounded-xl border border-slate-200 bg-white focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/50 transition-all overflow-hidden p-0.5 shadow-sm">
            <RichMentionInput
              value={valueTemplate}
              onChange={(value) => onUpdate({ valueTemplate: value })}
              inputParams={normalizedMentionParams}
              placeholder={t('settings.skills.argsTemplatePlaceholder')}
              className="min-w-0 border-0 focus:ring-0 min-h-[56px] text-sm"
              multiline
            />
          </div>
        </div>
      )}
    </div>
  )
}
