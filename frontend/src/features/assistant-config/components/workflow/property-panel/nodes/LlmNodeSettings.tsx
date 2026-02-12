
import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'
import { CommonRichInput, CommonSelect, CommonSwitch, Label, CommonSegmentedControl, CommonOutputList } from '../CommonInputs'

import type { InputParam } from '../../../../api/tools'

interface LlmNodeSettingsProps {
    config: Record<string, unknown>
    onChange: (field: string, value: unknown) => void
    mentionParams: InputParam[]
}

const FIELD_TYPES = [
    { label: 'String', value: 'string' },
    { label: 'Number', value: 'number' },
    { label: 'Integer', value: 'integer' },
    { label: 'Boolean', value: 'boolean' },
    { label: 'Object', value: 'object' },
    { label: 'Array', value: 'array' },
]

export function LlmNodeSettings({ config, onChange, mentionParams }: LlmNodeSettingsProps) {
    const { t } = useTranslation()

    const outputMode = String(config.outputMode ?? 'text').trim().toLowerCase() === 'structured' ? 'structured' : 'text'
    const outputFields = (Array.isArray(config.outputFields) ? config.outputFields : []) as Record<string, unknown>[]

    const handleAddField = () => {
        const newFields = [...outputFields, { name: 'field', type: 'string', nullable: false }]
        onChange('outputFields', newFields)
    }

    const handleRemoveField = (index: number) => {
        const newFields = [...outputFields]
        newFields.splice(index, 1)
        onChange('outputFields', newFields)
    }

    const handleUpdateField = (index: number, field: string, value: unknown) => {
        const newFields = [...outputFields]
        newFields[index] = { ...newFields[index], [field]: value }
        onChange('outputFields', newFields)
    }

    return (
        <div className="space-y-6">
            {/* System Prompt */}
            <div className="space-y-1.5">
                <Label>{t('settings.skills.llmSystemPrompt') || 'System Prompt'}</Label>
                <textarea
                    value={(config.systemPrompt as string) ?? ''}
                    onChange={(e) => onChange('systemPrompt', e.target.value)}
                    className="w-full px-3 py-2 text-xs rounded-md border bg-background/50 focus:ring-1 focus:ring-primary/20 focus:border-primary/50 outline-none resize-none min-h-[100px] font-mono"
                    placeholder="You are a helpful assistant..."
                />
            </div>

            {/* User Input */}
            <CommonRichInput
                label={t('settings.skills.llmUserInput') || 'User Input'}
                value={(config.userInput as string) ?? '{{start.user_input}}'}
                onChange={(value) => onChange('userInput', value)}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.llmUserInputPlaceholder') || 'Enter user input...'}
                rows={4}
            />

            <div className="h-px bg-border/50" />

            {/* Output Configuration */}
            <div className="space-y-4">
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    {t('settings.skills.llmOutputConfiguration')}
                </h4>

                <CommonSegmentedControl
                    label={t('settings.skills.outputMode')}
                    value={outputMode}
                    onChange={(val) => onChange('outputMode', val)}
                    options={[
                        { label: t('settings.skills.outputModeText'), value: 'text' },
                        { label: t('settings.skills.llmOutputModeStructured'), value: 'structured' },
                    ]}
                />

                {outputMode === 'structured' && (
                    <div className="space-y-3 pl-1 mt-4">
                        <div className="flex items-center justify-between">
                            <Label>{t('settings.skills.llmOutputFields') || 'Output Fields'}</Label>
                            <button
                                onClick={handleAddField}
                                className="flex items-center gap-1 text-[10px] bg-primary/10 text-primary hover:bg-primary/20 px-2 py-1 rounded transition-colors"
                            >
                                <Plus className="w-3 h-3" />
                                {t('actions.add')}
                            </button>
                        </div>

                        <div className="space-y-2">
                            {outputFields.map((field, idx) => (
                                <div key={idx} className="flex items-start gap-2 p-2 rounded-md border bg-card/50">
                                    <div className="flex-1 space-y-2">
                                        <input
                                            type="text"
                                            value={(field.name as string) ?? ''}
                                            onChange={(e) => handleUpdateField(idx, 'name', e.target.value)}
                                            className="w-full px-2 py-1 text-xs rounded border bg-background"
                                            placeholder={t('settings.skills.jsonFieldsPlaceholder')}
                                        />
                                        <div className="flex gap-2">
                                            <select
                                                value={(field.type as string) ?? 'string'}
                                                onChange={(e) => handleUpdateField(idx, 'type', e.target.value)}
                                                className="flex-1 px-2 py-1 text-xs rounded border bg-background"
                                            >
                                                {FIELD_TYPES.map(t => (
                                                    <option key={t.value} value={t.value}>{t.label}</option>
                                                ))}
                                            </select>

                                            {(field.type as string) === 'array' && (
                                                <input
                                                    type="text"
                                                    value={(field.itemsType as string) ?? 'string'}
                                                    onChange={(e) => handleUpdateField(idx, 'itemsType', e.target.value)}
                                                    className="flex-1 px-2 py-1 text-xs rounded border bg-background"
                                                    placeholder="Item Type"
                                                />
                                            )}
                                        </div>
                                    </div>

                                    <button
                                        onClick={() => handleRemoveField(idx)}
                                        className="text-muted-foreground hover:text-red-500 p-1 rounded hover:bg-red-50 transition-colors"
                                    >
                                        <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                </div>
                            ))}

                            {outputFields.length === 0 && (
                                <div className="text-center py-4 text-xs text-muted-foreground border border-dashed rounded-md">
                                    {t('settings.skills.noParams')}
                                </div>
                            )}
                        </div>
                    </div>
                )}

                <div className="pt-2">
                    <CommonSwitch
                        label={t('settings.skills.llmIsOutput') || 'Return as Workflow Output'}
                        checked={Boolean(config.isOutput)}
                        onChange={(checked) => onChange('isOutput', checked)}
                    />
                </div>

                <CommonOutputList
                    label={t('settings.skills.toolOutput')}
                    outputs={outputMode === 'structured' && outputFields.length > 0
                        ? outputFields.map(f => f.name as string).filter(Boolean)
                        : ['response']
                    }
                />
            </div>
        </div>
    )
}
