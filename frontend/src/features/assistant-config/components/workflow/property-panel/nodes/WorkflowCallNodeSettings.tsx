import { ArrowRightToLine, History, MessageSquare, Network } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { CommonOutputList, CommonRichInput, CommonSelect, Label } from '../CommonInputs'
import { resolveCallableWorkflowVersion } from '../../../../components/workflow/nodeFactory'
import type { CallableWorkflowDefinition } from '../../../../components/workflow/types'
import type { NodeSettingsProps } from './ToolNodeSettings'

type WorkflowCallNodeSettingsProps = NodeSettingsProps & {
  workflows: CallableWorkflowDefinition[]
}

function normalizeBindings(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  return Object.fromEntries(
    Object.entries(raw as Record<string, unknown>).map(([key, value]) => [key, typeof value === 'string' ? value : String(value ?? '')]),
  )
}

function buildBindingsForParams(
  paramNames: string[],
  existing: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(paramNames.map((name) => [name, existing[name] ?? '']))
}

export function WorkflowCallNodeSettings({
  config,
  onUpdate,
  mentionParams,
  workflows,
}: WorkflowCallNodeSettingsProps) {
  const { t } = useTranslation()
  const targetWorkflowId = String(config.targetWorkflowId ?? '').trim()
  const bindingMode = String(config.bindingMode ?? 'pinned').trim().toLowerCase() === 'latest' ? 'latest' : 'pinned'
  const selectedWorkflow = workflows.find((workflow) => workflow.id === targetWorkflowId)
  const selectedVersionId = String(config.targetPublishedVersionId ?? '').trim()
  const resolvedVersion = selectedWorkflow
    ? resolveCallableWorkflowVersion(selectedWorkflow, selectedVersionId || null)
    : undefined
  const inputBindings = normalizeBindings(config.inputBindings)

  const versionOptions = useMemo(
    () => (selectedWorkflow?.availableVersions ?? []).map((version) => ({
      value: version.id,
      label: version.versionName || `v${version.sequenceNo}`,
    })),
    [selectedWorkflow],
  )

  const workflowOptions = useMemo(
    () => workflows.map((workflow) => ({
      value: workflow.id,
      label: workflow.name,
    })),
    [workflows],
  )

  const resolvedInputs = resolvedVersion?.inputParams ?? selectedWorkflow?.inputParams ?? []
  const resolvedOutputs = resolvedVersion?.outputParams ?? selectedWorkflow?.outputParams ?? []
  const resolvedOutputMode = resolvedVersion?.outputMode ?? selectedWorkflow?.outputMode ?? 'structured'
  const outputNames = ['response', ...resolvedOutputs.map((item) => item.name)].filter(
    (value, index, array) => Boolean(value) && array.indexOf(value) === index,
  )

  const handleSelectWorkflow = (workflowId: string) => {
    const workflow = workflows.find((item) => item.id === workflowId)
    if (!workflow) {
      onUpdate({
        targetWorkflowId: '',
        targetPublishedVersionId: null,
        inputBindings: {},
      })
      return
    }
    const nextVersion = resolveCallableWorkflowVersion(workflow, workflow.publishedVersionId)
    const nextBindings = buildBindingsForParams(
      (nextVersion?.inputParams ?? workflow.inputParams ?? []).map((item) => item.name),
      inputBindings,
    )
    onUpdate({
      targetWorkflowId: workflow.id,
      bindingMode: 'pinned',
      targetPublishedVersionId: nextVersion?.id ?? workflow.publishedVersionId ?? null,
      inputBindings: nextBindings,
    })
  }

  const handleSelectBindingMode = (nextMode: string) => {
    if (nextMode !== 'pinned' && nextMode !== 'latest') return
    onUpdate({
      bindingMode: nextMode,
      targetPublishedVersionId: selectedVersionId || selectedWorkflow?.publishedVersionId || null,
    })
  }

  const handleSelectVersion = (versionId: string) => {
    if (!selectedWorkflow) return
    const nextVersion = resolveCallableWorkflowVersion(selectedWorkflow, versionId)
    const nextBindings = buildBindingsForParams(
      (nextVersion?.inputParams ?? []).map((item) => item.name),
      inputBindings,
    )
    onUpdate({
      targetPublishedVersionId: versionId,
      inputBindings: nextBindings,
    })
  }

  const handleBindingChange = (paramName: string, value: string) => {
    onUpdate({
      inputBindings: {
        ...inputBindings,
        [paramName]: value,
      },
    })
  }

  return (
    <div className="space-y-4">
      <CommonSelect
        icon={<Network className="w-4 h-4" />}
        label={t('settings.skills.workflowCallTarget')}
        value={targetWorkflowId}
        onChange={handleSelectWorkflow}
        options={workflowOptions}
        placeholder={t('settings.skills.workflowCallWorkflowPlaceholder')}
      />

      {selectedWorkflow && (
        <div className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5 text-xs text-slate-600">
          <div className="font-medium text-slate-700">{selectedWorkflow.name}</div>
          {selectedWorkflow.description ? (
            <p className="mt-1 leading-5">{selectedWorkflow.description}</p>
          ) : null}
        </div>
      )}

      <CommonSelect
        icon={<History className="w-4 h-4" />}
        label={t('settings.skills.workflowCallBindingMode')}
        value={bindingMode}
        onChange={handleSelectBindingMode}
        options={[
          { value: 'pinned', label: t('settings.skills.workflowCallPinned') },
          { value: 'latest', label: t('settings.skills.workflowCallLatest') },
        ]}
      />

      {bindingMode === 'pinned' && (
        <CommonSelect
          icon={<History className="w-4 h-4" />}
          label={t('settings.skills.workflowCallVersion')}
          value={resolvedVersion?.id ?? selectedVersionId}
          onChange={handleSelectVersion}
          options={versionOptions}
          placeholder={t('settings.skills.workflowCallVersionPlaceholder')}
          disabled={!selectedWorkflow || versionOptions.length === 0}
        />
      )}

      <div className="space-y-3 pt-1">
        <Label icon={<ArrowRightToLine className="w-4 h-4" />}>
          {t('settings.skills.workflowInputBindings')}
        </Label>

        {resolvedInputs.map((param) => (
          <CommonRichInput
            key={param.name}
            label={`${param.name}${param.required ? ' *' : ''}`}
            value={inputBindings[param.name] ?? ''}
            onChange={(value) => handleBindingChange(param.name, value)}
            mentionParams={mentionParams}
            placeholder={param.description || `${t('settings.skills.workflowInputBindings')} ${param.name}`}
            rows={1}
            minHeight="42px"
          />
        ))}

        {resolvedInputs.length === 0 && (
          <div className="text-sm text-slate-400 italic text-center py-5 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
            {selectedWorkflow
              ? t('settings.skills.workflowCallNoInputs')
              : t('settings.skills.workflowNoCallableWorkflows')}
          </div>
        )}
      </div>

      <CommonOutputList
        icon={<MessageSquare className="w-4 h-4" />}
        label={t('settings.skills.workflowCallOutputs')}
        outputs={outputNames}
        description={resolvedVersion
          ? (
              resolvedOutputMode === 'text'
                ? t('settings.skills.workflowCallTextOutputsHint')
                : t('settings.skills.workflowCallOutputsHint')
            )
          : t('settings.skills.workflowCallSelectWorkflowHint')}
      />
    </div>
  )
}
