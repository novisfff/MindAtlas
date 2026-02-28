
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Cpu, Terminal, User, Settings2, List, Database, MessageSquare } from 'lucide-react'
import { CommonRichInput, CommonSelect, CommonSwitch, Label, CommonSegmentedControl, CommonOutputList } from '../CommonInputs'

import type { InputParam } from '../../../../api/tools'

interface LlmNodeSettingsProps {
    config: Record<string, unknown>
    onChange: (field: string, value: unknown) => void
    mentionParams: InputParam[]
    knowledgeSourceOptions: Array<{ id: string; label: string }>
    modelOptions: Array<{ id: string; label: string }>
}

const FIELD_TYPES = [
    { label: 'String', value: 'string' },
    { label: 'Number', value: 'number' },
    { label: 'Integer', value: 'integer' },
    { label: 'Boolean', value: 'boolean' },
    { label: 'Object', value: 'object' },
    { label: 'Array', value: 'array' },
]
const DEFAULT_MODEL_VALUE = '__system_default_model__'

export function LlmNodeSettings({
    config,
    onChange,
    mentionParams,
    knowledgeSourceOptions,
    modelOptions,
}: LlmNodeSettingsProps) {
    const { t } = useTranslation()

    const outputMode = String(config.outputMode ?? 'text').trim().toLowerCase() === 'structured' ? 'structured' : 'text'
    const outputFields = (Array.isArray(config.outputFields) ? config.outputFields : []) as Record<string, unknown>[]
    const knowledgeEnabled = Boolean(config.knowledgeEnabled)
    const knowledgeSourceNodeIds = Array.isArray(config.knowledgeSourceNodeIds)
        ? config.knowledgeSourceNodeIds.map((item) => String(item)).filter(Boolean)
        : []
    const knowledgeInjectMode = String(config.knowledgeInjectMode ?? 'references_only') === 'full_payload'
        ? 'full_payload'
        : 'references_only'
    const knowledgeMaxRefsValue = typeof config.knowledgeMaxRefs === 'number'
        ? String(config.knowledgeMaxRefs)
        : ''
    const modelSource = String(config.modelSource ?? 'default') === 'custom' ? 'custom' : 'default'
    const rawModelId = typeof config.modelId === 'string' ? config.modelId : ''
    const isModelInList = modelOptions.some((item) => item.id === rawModelId)
    const modelSelectValue = modelSource === 'custom' && rawModelId ? rawModelId : DEFAULT_MODEL_VALUE
    const modelSelectOptions = [
        { label: t('settings.skills.nodeModelDefault'), value: DEFAULT_MODEL_VALUE },
        ...modelOptions.map((item) => ({ label: item.label, value: item.id })),
        ...(!isModelInList && modelSource === 'custom' && rawModelId
            ? [{ label: `${t('settings.skills.nodeModelCustom')}: ${rawModelId}`, value: rawModelId }]
            : []),
    ]

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

    const toggleKnowledgeSource = (nodeId: string) => {
        const set = new Set(knowledgeSourceNodeIds)
        if (set.has(nodeId)) {
            set.delete(nodeId)
        } else {
            set.add(nodeId)
        }
        onChange('knowledgeSourceNodeIds', Array.from(set))
    }

    return (
        <div className="space-y-4">
            <CommonSelect
                icon={<Cpu className="w-4 h-4" />}
                label={t('settings.skills.nodeModel')}
                value={modelSelectValue}
                onChange={(val) => {
                    if (val === DEFAULT_MODEL_VALUE) {
                        onChange('modelSource', 'default')
                        onChange('modelId', undefined)
                        return
                    }
                    onChange('modelSource', 'custom')
                    onChange('modelId', val || undefined)
                }}
                options={modelSelectOptions}
            />

            {/* System Prompt */}
            <div className="space-y-1.5">
                <Label icon={<Terminal className="w-4 h-4" />}>{t('settings.skills.llmSystemPrompt') || 'System Prompt'}</Label>
                <textarea
                    value={(config.systemPrompt as string) ?? ''}
                    onChange={(e) => onChange('systemPrompt', e.target.value)}
                    className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none resize-none min-h-[80px] font-mono shadow-sm transition-all"
                    placeholder="You are a helpful assistant..."
                />
            </div>

            {/* User Input */}
            <CommonRichInput
                icon={<User className="w-4 h-4" />}
                label={t('settings.skills.llmUserInput') || 'User Input'}
                value={(config.userInput as string) ?? '{{start.user_input}}'}
                onChange={(value) => onChange('userInput', value)}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.llmUserInputPlaceholder') || 'Enter user input...'}
                rows={4}
            />

            <div className="h-px bg-slate-200/60" />

            {/* Output Configuration */}
            <div className="space-y-3">
                <h4 className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 px-1">
                    {t('settings.skills.llmOutputConfiguration')}
                </h4>

                <CommonSegmentedControl
                    icon={<Settings2 className="w-4 h-4" />}
                    label={t('settings.skills.outputMode')}
                    value={outputMode}
                    onChange={(val) => onChange('outputMode', val)}
                    options={[
                        { label: t('settings.skills.outputModeText'), value: 'text' },
                        { label: t('settings.skills.llmOutputModeStructured'), value: 'structured' },
                    ]}
                />

                {outputMode === 'structured' && (
                    <div className="space-y-2.5 pl-1 mt-3">
                        <div className="flex items-center justify-between">
                            <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.llmOutputFields') || 'Output Fields'}</Label>
                            <button
                                onClick={handleAddField}
                                className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2.5 py-1.5 rounded-lg transition-colors border border-primary/10"
                            >
                                <Plus className="w-3.5 h-3.5" />
                                {t('actions.add')}
                            </button>
                        </div>

                        <div className="space-y-2.5">
                            {outputFields.map((field, idx) => (
                                <div key={idx} className="group relative flex flex-col gap-2.5 p-3 rounded-xl border border-slate-200/80 bg-slate-50/50 hover:bg-slate-50 transition-all shadow-sm">
                                    <div className="flex items-start gap-2 w-full pr-8">
                                        <input
                                            type="text"
                                            value={(field.name as string) ?? ''}
                                            onChange={(e) => handleUpdateField(idx, 'name', e.target.value)}
                                            className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono"
                                            placeholder={t('settings.skills.jsonFieldsPlaceholder')}
                                        />
                                    </div>
                                    <div className="flex gap-2 w-full pr-8">
                                        <div className="relative flex-1">
                                            <select
                                                value={(field.type as string) ?? 'string'}
                                                onChange={(e) => handleUpdateField(idx, 'type', e.target.value)}
                                                className="w-full px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all shadow-sm appearance-none cursor-pointer"
                                                style={{
                                                    backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                                                    backgroundPosition: 'right 0.5rem center',
                                                    backgroundRepeat: 'no-repeat',
                                                    backgroundSize: '1.5em 1.5em',
                                                    paddingRight: '2.5rem'
                                                }}
                                            >
                                                {FIELD_TYPES.map(t => (
                                                    <option key={t.value} value={t.value}>{t.label}</option>
                                                ))}
                                            </select>
                                        </div>

                                        {(field.type as string) === 'array' && (
                                            <input
                                                type="text"
                                                value={(field.itemsType as string) ?? 'string'}
                                                onChange={(e) => handleUpdateField(idx, 'itemsType', e.target.value)}
                                                className="flex-1 px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all shadow-sm"
                                                placeholder="Item Type"
                                            />
                                        )}
                                    </div>

                                    <button
                                        onClick={() => handleRemoveField(idx)}
                                        className="absolute right-2 top-3 p-1.5 text-slate-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            ))}

                            {outputFields.length === 0 && (
                                <div className="text-center py-6 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                                    {t('settings.skills.noParams')}
                                </div>
                            )}
                        </div>
                    </div>
                )}

                <div className="space-y-3 pt-3 border-t border-slate-200/80 mt-3">
                    <CommonSwitch
                        icon={<Database className="w-4 h-4" />}
                        label={t('settings.skills.llmKnowledgeEnabled')}
                        checked={knowledgeEnabled}
                        onChange={(checked) => onChange('knowledgeEnabled', checked)}
                        description={t('settings.skills.llmKnowledgeEnabledDesc')}
                    />

                    {knowledgeEnabled && (
                        <div className="space-y-3 pl-1">
                            <div className="space-y-1.5">
                                <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.llmKnowledgeSources')}</Label>
                                {knowledgeSourceOptions.length === 0 ? (
                                    <div className="text-xs text-slate-500 border border-dashed border-slate-200 rounded-lg px-2.5 py-2.5 bg-slate-50">
                                        {t('settings.skills.llmKnowledgeNoSources')}
                                    </div>
                                ) : (
                                    <div className="space-y-1.5">
                                        {knowledgeSourceOptions.map((item) => (
                                            <label key={item.id} className="flex items-center gap-2.5 text-sm rounded-xl border border-slate-200 px-2.5 py-2 bg-white hover:bg-slate-50 cursor-pointer transition-colors shadow-sm">
                                                <input
                                                    type="checkbox"
                                                    className="rounded border-slate-300 text-primary focus:ring-primary/20 w-4 h-4 cursor-pointer"
                                                    checked={knowledgeSourceNodeIds.includes(item.id)}
                                                    onChange={() => toggleKnowledgeSource(item.id)}
                                                />
                                                <span className="truncate">{item.label}</span>
                                            </label>
                                        ))}
                                    </div>
                                )}
                            </div>

                            <CommonSelect
                                icon={<Settings2 className="w-4 h-4" />}
                                label={t('settings.skills.llmKnowledgeInjectMode')}
                                value={knowledgeInjectMode}
                                onChange={(val) => onChange('knowledgeInjectMode', val)}
                                options={[
                                    { label: t('settings.skills.llmKnowledgeInjectReferencesOnly'), value: 'references_only' },
                                    { label: t('settings.skills.llmKnowledgeInjectFullPayload'), value: 'full_payload' },
                                ]}
                            />

                            <div className="space-y-1.5">
                                <Label icon={<Settings2 className="w-4 h-4" />}>{t('settings.skills.llmKnowledgeMaxRefs')}</Label>
                                <input
                                    type="number"
                                    value={knowledgeMaxRefsValue}
                                    onChange={(e) => {
                                        const val = e.target.value.trim()
                                        if (!val) {
                                            onChange('knowledgeMaxRefs', undefined)
                                            return
                                        }
                                        const parsed = Number.parseInt(val, 10)
                                        if (Number.isNaN(parsed)) return
                                        onChange('knowledgeMaxRefs', Math.max(1, Math.min(100, parsed)))
                                    }}
                                    className="w-full px-2.5 py-1.5 text-sm rounded-xl border border-slate-200 bg-white hover:bg-slate-50 focus:bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none shadow-sm transition-all"
                                    min={1}
                                    max={100}
                                    placeholder="20"
                                />
                            </div>
                        </div>
                    )}
                </div>

                <CommonOutputList
                    icon={<MessageSquare className="w-4 h-4" />}
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
