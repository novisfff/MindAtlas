
import { useTranslation } from 'react-i18next'
import { CommonRichInput, CommonSelect, Label, CommonOutputList } from '../CommonInputs'
import type { WorkflowToolDefinition } from '../../../../components/workflow/types'
import { Info } from 'lucide-react'

// Redefining the component with better props
export interface NodeSettingsProps {
    config: Record<string, unknown>
    onUpdate: (updates: Record<string, unknown>) => void
    mentionParams: any[] // InputParam[] but using any to avoid strict type issues for now, or match CommonInputs
    tools?: WorkflowToolDefinition[] // Optional, only for ToolNode
}

export function ToolNodeSettings({ config, onUpdate, mentionParams, tools = [] }: NodeSettingsProps) {
    const { t } = useTranslation()

    const selectedToolName = String(config.toolName ?? '').trim()
    const selectedTool = tools.find((tool) => tool.name === selectedToolName)

    const rawBindings = config.inputBindings
    const inputBindings =
        rawBindings && typeof rawBindings === 'object' && !Array.isArray(rawBindings)
            ? (rawBindings as Record<string, string>)
            : {}

    const handleToolChange = (toolName: string) => {
        const nextTool = tools.find((tool) => tool.name === toolName)
        const nextBindings = Object.fromEntries(
            (nextTool?.inputParams ?? []).map((param) => [
                param.name,
                typeof inputBindings[param.name] === 'string' ? inputBindings[param.name] : '',
            ]),
        )

        onUpdate({
            toolName: toolName,
            inputBindings: nextBindings
        })
    }

    const handleBindingChange = (paramName: string, value: string) => {
        onUpdate({
            inputBindings: {
                ...inputBindings,
                [paramName]: value
            }
        })
    }

    const toolOptions = tools.map(t => ({ label: t.name, value: t.name }))

    return (
        <div className="space-y-6">
            <CommonSelect
                label={t('settings.skills.workflowToolName') || 'Select Tool'}
                value={selectedToolName}
                onChange={handleToolChange}
                options={toolOptions}
                placeholder={t('settings.skills.selectTool') || 'Choose a tool...'}
            />

            {selectedTool && (
                <div className="space-y-4">
                    <div className="flex items-center gap-2 text-primary/80 bg-primary/5 p-2 rounded-md">
                        <Info className="w-4 h-4" />
                        <span className="text-xs font-medium">{t('settings.skills.workflowInputBindings')}</span>
                    </div>

                    <div className="space-y-4">
                        {(selectedTool.inputParams ?? []).map((param) => (
                            <CommonRichInput
                                key={param.name}
                                label={`${param.name} ${param.required ? '*' : ''}`}
                                value={inputBindings[param.name] ?? ''}
                                onChange={(val) => handleBindingChange(param.name, val)}
                                mentionParams={mentionParams}
                                placeholder={param.description || `${t('settings.skills.workflowInputBindings')} ${param.name}`}
                                rows={2}
                                minHeight="60px"
                            />
                        ))}

                        {(selectedTool.inputParams ?? []).length === 0 && (
                            <div className="text-xs text-muted-foreground italic text-center py-2">
                                {t('settings.skills.workflowNoToolInputs') || 'This tool has no input parameters.'}
                            </div>
                        )}
                    </div>
                </div>
            )}

            <CommonOutputList
                label={t('settings.skills.toolOutput')}
                outputs={(selectedTool?.outputParams ?? []).map((item) => item.name).filter(Boolean).length > 0
                    ? (selectedTool?.outputParams ?? []).map((item) => item.name)
                    : ['result']
                }
            />
        </div>
    )
}
