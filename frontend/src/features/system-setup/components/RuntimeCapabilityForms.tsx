import { useState, type ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { uiChrome, uiField, uiRadius } from '@/components/ui/styles'
import { cn } from '@/lib/utils'
import type {
  RuntimeAutomationConfigResponse,
  RuntimeConfigModuleBase,
  RuntimeConfigSource,
  RuntimeDocumentParsingConfigResponse,
  RuntimeKnowledgeGraphConfigResponse,
  RuntimeStorageConfigResponse,
  SecretFieldState,
} from '../api/runtime-config'

export interface RuntimeStorageDraft extends RuntimeStorageConfigResponse {
  accessKey: string
  secretKey: string
}

export interface RuntimeKnowledgeGraphDraft extends RuntimeKnowledgeGraphConfigResponse {
  neo4jPassword: string
  rerankApiKey: string
}

export interface RuntimeDocumentParsingDraft extends RuntimeDocumentParsingConfigResponse {
  pictureDescriptionApiKey: string
}

export interface RuntimeAutomationDraft extends RuntimeAutomationConfigResponse {}

export const FIELD_CLASSNAME = uiField.input
export const TEXTAREA_CLASSNAME = uiField.textarea

export function Label({ children }: { children: ReactNode }) {
  return <label className="text-sm font-medium text-foreground">{children}</label>
}

export function InputField({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  disabled = false,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
  disabled?: boolean
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={FIELD_CLASSNAME}
        placeholder={placeholder}
        disabled={disabled}
      />
    </div>
  )
}

export function TextareaField({
  label,
  value,
  onChange,
  placeholder,
  rows = 4,
  disabled = false,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  rows?: number
  disabled?: boolean
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <textarea
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={TEXTAREA_CLASSNAME}
        placeholder={placeholder}
        disabled={disabled}
      />
    </div>
  )
}

export function ToggleCard({
  label,
  description,
  checked,
  onCheckedChange,
  disabled = false,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className={cn(uiChrome.inset, 'flex items-start justify-between gap-4 px-4 py-4')}>
      <div className="space-y-1">
        <p className="text-sm font-semibold text-foreground">{label}</p>
        <p className="text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
    </div>
  )
}

export function SecretHint({
  value,
  state,
  hint,
}: {
  value: string
  state: SecretFieldState
  hint: string
}) {
  if (value.trim()) return null
  if (!state.configured) return null
  return (
    <p className="text-xs leading-5 text-muted-foreground">
      {hint}
      {state.hint ? ` (${state.hint})` : ''}
    </p>
  )
}

function AdvancedSection({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)

  return (
    <div className={cn(uiRadius.panel, 'border border-dashed border-border/75 bg-muted/30')}>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center justify-between gap-4 px-4 py-4 text-left"
      >
        <div className="space-y-1">
          <p className="text-sm font-semibold text-foreground">{title}</p>
          <p className="text-sm leading-6 text-muted-foreground">{description}</p>
        </div>
        <span className="text-base font-semibold text-muted-foreground">
          {open ? '−' : '+'}
        </span>
      </button>
      {open ? <div className="border-t border-border/70 px-4 py-4">{children}</div> : null}
    </div>
  )
}

export function getRuntimeConfigSourceLabel(
  source: RuntimeConfigSource,
  t: (key: string) => string
) {
  if (source === 'app_config') return t('systemSetup.sources.appConfig')
  if (source === 'environment_default') return t('systemSetup.sources.environmentDefault')
  return t('systemSetup.sources.default')
}

export function getRuntimeCapabilityStatus(
  module: RuntimeConfigModuleBase,
  skipped: boolean,
  t: (key: string) => string
) {
  if (skipped) {
    return {
      label: t('systemSetup.status.skipped'),
      className: 'border-border bg-muted/80 text-muted-foreground',
    }
  }
  if (module.source === 'environment_default') {
    return {
      label: t('systemSetup.status.environmentDefault'),
      className:
        'border-sky-200/80 bg-sky-50/80 text-sky-700 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-200',
    }
  }
  if (module.configured) {
    return {
      label: t('systemSetup.status.configured'),
      className:
        'border-emerald-200/80 bg-emerald-50/80 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-200',
    }
  }
  return {
    label: t('systemSetup.status.notConfigured'),
    className:
      'border-amber-200/80 bg-amber-50/80 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200',
  }
}

export function RuntimeCapabilityMeta({
  module,
  skipped,
  t,
}: {
  module: RuntimeConfigModuleBase
  skipped: boolean
  t: (key: string) => string
}) {
  const status = getRuntimeCapabilityStatus(module, skipped, t)
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={cn('inline-flex rounded-full border px-3 py-1 text-xs font-semibold', status.className)}>
        {status.label}
      </span>
      <Badge variant="outline" className="px-3 py-1 text-[11px] font-medium text-muted-foreground">
        {getRuntimeConfigSourceLabel(module.source, t)}
      </Badge>
      {module.restartRequired ? (
        <Badge variant="outline" className="px-3 py-1 text-[11px] font-medium text-muted-foreground">
          {t('systemSetup.status.restartRequired')}
        </Badge>
      ) : null}
    </div>
  )
}

export function StorageCapabilityFields({
  value,
  onChange,
  t,
}: {
  value: RuntimeStorageDraft
  onChange: (patch: Partial<RuntimeStorageDraft>) => void
  t: (key: string) => string
}) {
  return (
    <div className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2">
        <InputField
          label={t('systemSetup.forms.storage.endpoint.label')}
          value={value.endpoint}
          onChange={(endpoint) => onChange({ endpoint })}
          placeholder={t('systemSetup.forms.storage.endpoint.placeholder')}
        />
        <InputField
          label={t('systemSetup.forms.storage.bucket.label')}
          value={value.bucket}
          onChange={(bucket) => onChange({ bucket })}
          placeholder={t('systemSetup.forms.storage.bucket.placeholder')}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <InputField
            label={t('systemSetup.forms.storage.accessKey.label')}
            value={value.accessKey}
            onChange={(accessKey) => onChange({ accessKey })}
            placeholder={t('systemSetup.forms.storage.accessKey.placeholder')}
          />
          <SecretHint
            value={value.accessKey}
            state={value.accessKeyState}
            hint={t('systemSetup.forms.secret.keepExisting')}
          />
        </div>
        <div className="space-y-2">
          <InputField
            type="password"
            label={t('systemSetup.forms.storage.secretKey.label')}
            value={value.secretKey}
            onChange={(secretKey) => onChange({ secretKey })}
            placeholder={t('systemSetup.forms.storage.secretKey.placeholder')}
          />
          <SecretHint
            value={value.secretKey}
            state={value.secretKeyState}
            hint={t('systemSetup.forms.secret.keepExisting')}
          />
        </div>
      </div>

      <ToggleCard
        label={t('systemSetup.forms.storage.secure.label')}
        description={t('systemSetup.forms.storage.secure.description')}
        checked={value.secure}
        onCheckedChange={(secure) => onChange({ secure })}
      />

      <AdvancedSection
        title={t('systemSetup.forms.storage.advanced.title')}
        description={t('systemSetup.forms.storage.advanced.description')}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <InputField
            type="number"
            label={t('systemSetup.forms.storage.maxFileSizeMb.label')}
            value={String(value.maxFileSizeMb)}
            onChange={(next) => onChange({ maxFileSizeMb: Number(next) || 0 })}
          />
          <InputField
            type="number"
            label={t('systemSetup.forms.storage.maxPdfPages.label')}
            value={String(value.maxPdfPages)}
            onChange={(next) => onChange({ maxPdfPages: Number(next) || 0 })}
          />
        </div>
      </AdvancedSection>
    </div>
  )
}

export function KnowledgeGraphCapabilityFields({
  value,
  onChange,
  t,
}: {
  value: RuntimeKnowledgeGraphDraft
  onChange: (patch: Partial<RuntimeKnowledgeGraphDraft>) => void
  t: (key: string) => string
}) {
  return (
    <div className="space-y-5">
      <ToggleCard
        label={t('systemSetup.forms.knowledgeGraph.enabled.label')}
        description={t('systemSetup.forms.knowledgeGraph.enabled.description')}
        checked={value.enabled}
        onCheckedChange={(enabled) => onChange({ enabled })}
      />

      <div className="grid gap-4 md:grid-cols-2">
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.neo4jUri.label')}
          value={value.neo4jUri}
          onChange={(neo4jUri) => onChange({ neo4jUri })}
          placeholder="bolt://localhost:7687"
        />
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.neo4jDatabase.label')}
          value={value.neo4jDatabase}
          onChange={(neo4jDatabase) => onChange({ neo4jDatabase })}
          placeholder="neo4j"
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.neo4jUser.label')}
          value={value.neo4jUser}
          onChange={(neo4jUser) => onChange({ neo4jUser })}
          placeholder="neo4j"
        />
        <div className="space-y-2">
          <InputField
            type="password"
            label={t('systemSetup.forms.knowledgeGraph.neo4jPassword.label')}
            value={value.neo4jPassword}
            onChange={(neo4jPassword) => onChange({ neo4jPassword })}
            placeholder={t('systemSetup.forms.knowledgeGraph.neo4jPassword.placeholder')}
          />
          <SecretHint
            value={value.neo4jPassword}
            state={value.neo4jPasswordState}
            hint={t('systemSetup.forms.secret.keepExisting')}
          />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.workspace.label')}
          value={value.workspace}
          onChange={(workspace) => onChange({ workspace })}
          placeholder={t('systemSetup.forms.knowledgeGraph.workspace.placeholder')}
        />
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.graphStorage.label')}
          value={value.graphStorage}
          onChange={(graphStorage) => onChange({ graphStorage })}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.summaryLanguage.label')}
          value={value.summaryLanguage}
          onChange={(summaryLanguage) => onChange({ summaryLanguage })}
          placeholder={t('systemSetup.forms.knowledgeGraph.summaryLanguage.placeholder')}
        />
        <InputField
          label={t('systemSetup.forms.knowledgeGraph.llmModelName.label')}
          value={value.llmModelName || ''}
          onChange={(llmModelName) => onChange({ llmModelName })}
          placeholder={t('systemSetup.forms.knowledgeGraph.llmModelName.placeholder')}
        />
      </div>

      <InputField
        label={t('systemSetup.forms.knowledgeGraph.embeddingModelName.label')}
        value={value.embeddingModelName || ''}
        onChange={(embeddingModelName) => onChange({ embeddingModelName })}
        placeholder={t('systemSetup.forms.knowledgeGraph.embeddingModelName.placeholder')}
      />

      <AdvancedSection
        title={t('systemSetup.forms.knowledgeGraph.advanced.title')}
        description={t('systemSetup.forms.knowledgeGraph.advanced.description')}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <InputField
            label={t('systemSetup.forms.knowledgeGraph.rerankModel.label')}
            value={value.rerankModel}
            onChange={(rerankModel) => onChange({ rerankModel })}
            placeholder={t('systemSetup.forms.knowledgeGraph.rerankModel.placeholder')}
          />
          <InputField
            label={t('systemSetup.forms.knowledgeGraph.rerankHost.label')}
            value={value.rerankHost}
            onChange={(rerankHost) => onChange({ rerankHost })}
            placeholder="https://your-rerank-host/v1"
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <InputField
              type="password"
              label={t('systemSetup.forms.knowledgeGraph.rerankApiKey.label')}
              value={value.rerankApiKey}
              onChange={(rerankApiKey) => onChange({ rerankApiKey })}
              placeholder={t('systemSetup.forms.knowledgeGraph.rerankApiKey.placeholder')}
            />
            <SecretHint
              value={value.rerankApiKey}
              state={value.rerankApiKeyState}
              hint={t('systemSetup.forms.secret.keepExisting')}
            />
          </div>
          <InputField
            label={t('systemSetup.forms.knowledgeGraph.rerankRequestFormat.label')}
            value={value.rerankRequestFormat}
            onChange={(rerankRequestFormat) => onChange({ rerankRequestFormat })}
            placeholder="standard"
          />
        </div>
      </AdvancedSection>
    </div>
  )
}

export function DocumentParsingCapabilityFields({
  value,
  onChange,
  t,
}: {
  value: RuntimeDocumentParsingDraft
  onChange: (patch: Partial<RuntimeDocumentParsingDraft>) => void
  t: (key: string) => string
}) {
  return (
    <div className="space-y-5">
      <ToggleCard
        label={t('systemSetup.forms.documentParsing.workerEnabled.label')}
        description={t('systemSetup.forms.documentParsing.workerEnabled.description')}
        checked={value.workerEnabled}
        onCheckedChange={(workerEnabled) => onChange({ workerEnabled })}
      />

      <div className="grid gap-4 md:grid-cols-2">
        <ToggleCard
          label={t('systemSetup.forms.documentParsing.ocrEnabled.label')}
          description={t('systemSetup.forms.documentParsing.ocrEnabled.description')}
          checked={value.ocrEnabled}
          onCheckedChange={(ocrEnabled) => onChange({ ocrEnabled })}
        />
        <InputField
          label={t('systemSetup.forms.documentParsing.ocrLangs.label')}
          value={value.ocrLangs}
          onChange={(ocrLangs) => onChange({ ocrLangs })}
          placeholder={t('systemSetup.forms.documentParsing.ocrLangs.placeholder')}
        />
      </div>

      <ToggleCard
        label={t('systemSetup.forms.documentParsing.pictureDescriptionEnabled.label')}
        description={t('systemSetup.forms.documentParsing.pictureDescriptionEnabled.description')}
        checked={value.pictureDescriptionEnabled}
        onCheckedChange={(pictureDescriptionEnabled) => onChange({ pictureDescriptionEnabled })}
      />

      {value.pictureDescriptionEnabled ? (
        <div className={cn(uiChrome.inset, 'space-y-5 p-5')}>
          <div className="grid gap-4 md:grid-cols-2">
            <InputField
              label={t('systemSetup.forms.documentParsing.pictureDescriptionUrl.label')}
              value={value.pictureDescriptionUrl}
              onChange={(pictureDescriptionUrl) => onChange({ pictureDescriptionUrl })}
              placeholder="https://api.openai.com/v1"
            />
            <InputField
              label={t('systemSetup.forms.documentParsing.pictureDescriptionModel.label')}
              value={value.pictureDescriptionModel}
              onChange={(pictureDescriptionModel) => onChange({ pictureDescriptionModel })}
              placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionModel.placeholder')}
            />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <InputField
                type="password"
                label={t('systemSetup.forms.documentParsing.pictureDescriptionApiKey.label')}
                value={value.pictureDescriptionApiKey}
                onChange={(pictureDescriptionApiKey) => onChange({ pictureDescriptionApiKey })}
                placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionApiKey.placeholder')}
              />
              <SecretHint
                value={value.pictureDescriptionApiKey}
                state={value.pictureDescriptionApiKeyState}
                hint={t('systemSetup.forms.secret.keepExisting')}
              />
            </div>
            <InputField
              type="number"
              label={t('systemSetup.forms.documentParsing.pictureDescriptionTimeoutSec.label')}
              value={String(value.pictureDescriptionTimeoutSec)}
              onChange={(next) => onChange({ pictureDescriptionTimeoutSec: Number(next) || 0 })}
            />
          </div>

          <TextareaField
            label={t('systemSetup.forms.documentParsing.pictureDescriptionPrompt.label')}
            value={value.pictureDescriptionPrompt}
            onChange={(pictureDescriptionPrompt) => onChange({ pictureDescriptionPrompt })}
            placeholder={t('systemSetup.forms.documentParsing.pictureDescriptionPrompt.placeholder')}
          />

          <TextareaField
            label={t('systemSetup.forms.documentParsing.pictureDescriptionParamsJson.label')}
            value={value.pictureDescriptionParamsJson}
            onChange={(pictureDescriptionParamsJson) => onChange({ pictureDescriptionParamsJson })}
            placeholder='{"temperature":0.2}'
            rows={3}
          />
        </div>
      ) : null}
    </div>
  )
}

export function AutomationCapabilityFields({
  value,
  onChange,
  t,
}: {
  value: RuntimeAutomationDraft
  onChange: (patch: Partial<RuntimeAutomationDraft>) => void
  t: (key: string) => string
}) {
  return (
    <div className="space-y-5">
      <ToggleCard
        label={t('systemSetup.forms.automation.schedulerEnabled.label')}
        description={t('systemSetup.forms.automation.schedulerEnabled.description')}
        checked={value.schedulerEnabled}
        onCheckedChange={(schedulerEnabled) => onChange({ schedulerEnabled })}
      />

      <div className={cn(uiChrome.inset, 'px-4 py-4 text-sm leading-6 text-muted-foreground')}>
        {t('systemSetup.forms.automation.note')}
      </div>
    </div>
  )
}
