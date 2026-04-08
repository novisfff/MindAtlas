
import { useTranslation } from 'react-i18next'
import { CommonRichInput, Label, CommonOutputList } from '../CommonInputs'
import type { WorkflowToolDefinition } from '../../../../components/workflow/types'
import { Info, Wrench, MessageSquare, ArrowRightToLine } from 'lucide-react'

// Redefining the component with better props
export interface NodeSettingsProps {
    config: Record<string, unknown>
    onUpdate: (updates: Record<string, unknown>) => void
    mentionParams: any[] // InputParam[] but using any to avoid strict type issues for now, or match CommonInputs
    tools?: WorkflowToolDefinition[] // Optional, only for ToolNode
    modelOptions?: Array<{ id: string; label: string }>
    onDeleteBranchEdges?: (branchId: string) => void
}

export function ToolNodeSettings({ config, onUpdate, mentionParams, tools = [] }: NodeSettingsProps) {
    const { t } = useTranslation()

    const selectedToolName = String(config.toolName ?? '').trim()
    const selectedTool = tools.find((tool) => tool.name === selectedToolName)
    const selectedToolDisplayName = selectedTool?.displayName ?? selectedToolName

    const rawBindings = config.inputBindings
    const inputBindings =
        rawBindings && typeof rawBindings === 'object' && !Array.isArray(rawBindings)
            ? (rawBindings as Record<string, string>)
            : {}

    const handleBindingChange = (paramName: string, value: string) => {
        onUpdate({
            inputBindings: {
                ...inputBindings,
                [paramName]: value
            }
        })
    }

    return (
        <div className="space-y-4">
            <div className="space-y-1.5">
                <Label icon={<Wrench className="w-4 h-4" />}>{t('settings.skills.workflowToolName') || 'Tool Name'}</Label>
                <div className="w-full rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-2 text-slate-700 shadow-sm">
                    <div className="text-sm font-medium break-all">
                        {selectedToolDisplayName || '-'}
                    </div>
                    {selectedTool && selectedToolDisplayName !== selectedToolName ? (
                        <code className="mt-1 block break-all text-xs text-slate-500">
                            {selectedToolName}
                        </code>
                    ) : null}
                </div>
            </div>

            {selectedTool && (
                <div className="space-y-3 pt-2">
                    <div className="mb-2">
                        <div className="h-px bg-slate-100 mb-4" />
                        <Label icon={<ArrowRightToLine className="w-4 h-4" />}>
                            {t('settings.skills.workflowInputBindings')}
                        </Label>
                    </div>

                    <div className="space-y-3">
                        {(selectedTool.inputParams ?? []).map((param) => (
                            <CommonRichInput
                                key={param.name}
                                label={`${param.name} ${param.required ? '*' : ''}`}
                                value={inputBindings[param.name] ?? ''}
                                onChange={(val) => handleBindingChange(param.name, val)}
                                mentionParams={mentionParams}
                                placeholder={param.description || `${t('settings.skills.workflowInputBindings')} ${param.name}`}
                                rows={1}
                                minHeight="42px"
                            />
                        ))}

                        {(selectedTool.inputParams ?? []).length === 0 && (
                            <div className="text-sm text-slate-400 italic text-center py-5 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                                {t('settings.skills.workflowNoToolInputs') || 'This tool has no input parameters.'}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {!selectedTool && (
                <div className="text-sm text-slate-400 italic text-center py-5 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                    {t('settings.skills.workflowNoTools')}
                </div>
            )}

            <CommonOutputList
                icon={<MessageSquare className="w-4 h-4" />}
                label={t('settings.skills.toolOutput')}
                outputs={(selectedTool?.outputParams ?? []).map((item) => item.name).filter(Boolean).length > 0
                    ? (selectedTool?.outputParams ?? []).map((item) => item.name)
                    : ['result']
                }
            />
        </div>
    )
}
