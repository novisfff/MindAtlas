import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { WorkflowEnvVarType, WorkflowSessionVar } from '../../api/workflow'
import {
  buildDefaultValueText,
  isValidEnvVarName,
  normalizeEnvVarName,
  parseEnvVarDefaultValue,
} from './workflowEnvVars'

interface EnvVarEditDialogProps {
  open: boolean
  mode: 'create' | 'edit'
  initialValue?: WorkflowSessionVar | null
  existingNames: string[]
  onOpenChange: (open: boolean) => void
  onSubmit: (value: WorkflowSessionVar) => void
}

const TYPE_OPTIONS: Array<{ value: WorkflowEnvVarType; label: string }> = [
  { value: 'string', label: 'String' },
  { value: 'number', label: 'Number' },
  { value: 'integer', label: 'Integer' },
  { value: 'boolean', label: 'Boolean' },
  { value: 'object', label: 'Object' },
  { value: 'array', label: 'Array' },
]

export function EnvVarEditDialog({
  open,
  mode,
  initialValue,
  existingNames,
  onOpenChange,
  onSubmit,
}: EnvVarEditDialogProps) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [varType, setVarType] = useState<WorkflowEnvVarType>('string')
  const [defaultValueText, setDefaultValueText] = useState('')
  const [description, setDescription] = useState('')
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    if (!open) return
    const current = initialValue ?? null
    setName(current?.name ?? '')
    setVarType((current?.type ?? 'string') as WorkflowEnvVarType)
    setDefaultValueText(buildDefaultValueText(current?.defaultValue, (current?.type ?? 'string') as WorkflowEnvVarType))
    setDescription(current?.description ?? '')
    setErrorMessage('')
  }, [initialValue, open])

  const nameSet = useMemo(
    () => new Set(existingNames.map((item) => item.trim().toLowerCase()).filter(Boolean)),
    [existingNames],
  )

  const handleConfirm = () => {
    const normalizedName = normalizeEnvVarName(name)
    if (!normalizedName) {
      setErrorMessage(t('settings.skills.envVars.validationNameRequired'))
      return
    }
    if (!isValidEnvVarName(normalizedName)) {
      setErrorMessage(t('settings.skills.envVars.validationNameInvalid'))
      return
    }
    const editingSameName = Boolean(initialValue?.name) && initialValue!.name.trim().toLowerCase() === normalizedName.toLowerCase()
    if (!editingSameName && nameSet.has(normalizedName.toLowerCase())) {
      setErrorMessage(t('settings.skills.envVars.validationNameDuplicated'))
      return
    }

    try {
      const parsedDefaultValue = parseEnvVarDefaultValue(defaultValueText, varType)
      onSubmit({
        name: normalizedName,
        type: varType,
        defaultValue: parsedDefaultValue,
        description: description.trim() || undefined,
      })
      onOpenChange(false)
    } catch (error) {
      const detail = error instanceof Error ? error.message : t('messages.error')
      setErrorMessage(`${t('settings.skills.envVars.validationDefaultInvalid')}: ${detail}`)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg rounded-[24px] border-white/80 bg-white/96 p-6 shadow-[0_24px_70px_rgba(15,23,42,0.22)]">
        <DialogHeader className="space-y-2 text-left">
          <DialogTitle>
            {mode === 'create' ? t('settings.skills.envVars.createTitle') : t('settings.skills.envVars.editTitle')}
          </DialogTitle>
          <DialogDescription>
            {t('settings.skills.envVars.formDialogDescription')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-foreground/80">{t('settings.skills.envVars.formName')}</label>
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-xs"
              placeholder="counter"
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-foreground/80">{t('settings.skills.envVars.formType')}</label>
            <select
              value={varType}
              onChange={(event) => setVarType(event.target.value as WorkflowEnvVarType)}
              className="w-full rounded-md border bg-background px-3 py-2 text-xs"
            >
              {TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-foreground/80">{t('settings.skills.envVars.formDefaultValue')}</label>
            <input
              type="text"
              value={defaultValueText}
              onChange={(event) => setDefaultValueText(event.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-xs font-mono"
              placeholder={t('settings.skills.envVars.formDefaultPlaceholder')}
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs font-medium text-foreground/80">{t('settings.skills.envVars.formDescription')}</label>
            <input
              type="text"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-xs"
              placeholder={t('settings.skills.envVars.formDescriptionPlaceholder')}
            />
          </div>

          {errorMessage && (
            <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-xs text-red-700">
              {errorMessage}
            </div>
          )}
        </div>

        <DialogFooter>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="h-10 rounded-xl border border-slate-200 px-4 text-sm text-slate-700 transition-colors hover:bg-slate-50"
          >
            {t('actions.cancel')}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            className="h-10 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:opacity-90"
          >
            {mode === 'create' ? t('actions.create') : t('actions.save')}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
