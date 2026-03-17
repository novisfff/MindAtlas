import { Plus, Trash2, Cpu, FileText, Terminal, List, MessageSquare, Search, Settings2, Box, Hash } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { CommonOutputList, CommonRichInput, CommonSelect, CommonSwitch, Label } from '../CommonInputs'
import { NodeSettingsProps } from './ToolNodeSettings'
import type { NodeType } from '../../../../api/workflow'

const DEFAULT_MODEL_VALUE = '__system_default_model__'
const FIELD_TYPES = [
    { label: 'String', value: 'string' },
    { label: 'Number', value: 'number' },
    { label: 'Integer', value: 'integer' },
    { label: 'Boolean', value: 'boolean' },
    { label: 'Object', value: 'object' },
    { label: 'Array', value: 'array' },
]
const ARRAY_ITEM_TYPES = FIELD_TYPES.filter((item) => item.value !== 'array')

type OutputField = {
    name: string
    type: string
    nullable: boolean
    itemsType?: string
    enum?: string[]
}

function normalizeOutputFields(raw: unknown): OutputField[] {
    if (!Array.isArray(raw)) return []
    return raw
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
        .map((item) => ({
            name: String(item.name ?? ''),
            type: String(item.type ?? 'string') || 'string',
            nullable: Boolean(item.nullable),
            itemsType: typeof item.itemsType === 'string'
                ? item.itemsType
                : (typeof item.items_type === 'string' ? item.items_type : undefined),
            enum: Array.isArray(item.enum)
                ? item.enum.map((value) => String(value)).filter(Boolean)
                : undefined,
        }))
}

export function ParameterExtractorNodeSettings({ config, onUpdate, mentionParams, modelOptions = [] }: NodeSettingsProps) {
    const { t } = useTranslation()
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
    const outputFields = normalizeOutputFields(config.outputFields)
    const outputNames = outputFields.map((item) => item.name.trim()).filter(Boolean)

    const updateOutputFields = (next: OutputField[]) => {
        onUpdate({ outputFields: next })
    }

    const addOutputField = () => {
        updateOutputFields([...outputFields, { name: 'field', type: 'string', nullable: false }])
    }

    const removeOutputField = (index: number) => {
        const next = [...outputFields]
        next.splice(index, 1)
        updateOutputFields(next)
    }

    const updateOutputField = (index: number, patch: Partial<OutputField>) => {
        const next = [...outputFields]
        const current = next[index] ?? { name: '', type: 'string', nullable: false }
        const merged: OutputField = { ...current, ...patch }
        if (merged.type !== 'array') {
            delete merged.itemsType
        } else if (!merged.itemsType) {
            merged.itemsType = 'string'
        }
        next[index] = merged
        updateOutputFields(next)
    }

    return (
        <div className="space-y-4">
            <CommonSelect
                icon={<Cpu className="w-4 h-4" />}
                label={t('settings.skills.nodeModel')}
                value={modelSelectValue}
                onChange={(val) => {
                    if (val === DEFAULT_MODEL_VALUE) {
                        onUpdate({ modelSource: 'default', modelId: undefined })
                        return
                    }
                    onUpdate({ modelSource: 'custom', modelId: val || undefined })
                }}
                options={modelSelectOptions}
            />

            <CommonRichInput
                icon={<FileText className="w-4 h-4" />}
                label={t('settings.skills.extractionInputContent')}
                value={(config.inputContent as string) ?? ''}
                onChange={(val) => onUpdate({ inputContent: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.extractionInputContentPlaceholder')}
                rows={3}
            />

            <CommonRichInput
                icon={<Terminal className="w-4 h-4" />}
                label={t('settings.skills.extractionInstructions')}
                value={(config.instruction as string) ?? ''}
                onChange={(val) => onUpdate({ instruction: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.extractionPlaceholder')}
                rows={4}
            />

            <p className="text-[11px] text-muted-foreground bg-muted/30 border rounded-md px-2 py-1.5">
                {t('settings.skills.extractionBuiltinPromptHint')}
            </p>

            <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                    <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.extractionOutputConfiguration')}</Label>
                    <button
                        onClick={addOutputField}
                        className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2 py-1.5 rounded-lg transition-colors border border-primary/10"
                    >
                        <Plus className="w-3.5 h-3.5" />
                        {t('actions.add')}
                    </button>
                </div>

                <div className="space-y-2.5">
                    {outputFields.map((field, index) => (
                        <div key={index} className="relative group flex flex-col gap-2.5 p-3 rounded-xl border border-slate-200/80 bg-slate-50/50 hover:bg-slate-50 transition-all shadow-sm">
                            <div className="flex items-start gap-2.5 w-full pr-8">
                                <input
                                    type="text"
                                    value={field.name}
                                    onChange={(e) => updateOutputField(index, { name: e.target.value })}
                                    className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono"
                                    placeholder={t('settings.skills.jsonFieldsPlaceholder')}
                                />
                            </div>

                            <div className="flex gap-2.5 w-full pr-8">
                                <div className="relative flex-1">
                                    <select
                                        value={field.type}
                                        onChange={(e) => updateOutputField(index, { type: e.target.value })}
                                        className="w-full px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all shadow-sm appearance-none cursor-pointer"
                                        style={{
                                            backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                                            backgroundPosition: 'right 0.5rem center',
                                            backgroundRepeat: 'no-repeat',
                                            backgroundSize: '1.5em 1.5em',
                                            paddingRight: '2.5rem'
                                        }}
                                    >
                                        {FIELD_TYPES.map((item) => (
                                            <option key={item.value} value={item.value}>
                                                {item.label}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                {field.type === 'array' && (
                                    <div className="relative flex-1">
                                        <select
                                            value={field.itemsType ?? 'string'}
                                            onChange={(e) => updateOutputField(index, { itemsType: e.target.value })}
                                            className="w-full px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary/50 outline-none transition-all shadow-sm appearance-none cursor-pointer"
                                            style={{
                                                backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                                                backgroundPosition: 'right 0.5rem center',
                                                backgroundRepeat: 'no-repeat',
                                                backgroundSize: '1.5em 1.5em',
                                                paddingRight: '2.5rem'
                                            }}
                                        >
                                            {ARRAY_ITEM_TYPES.map((item) => (
                                                <option key={item.value} value={item.value}>
                                                    {item.label}
                                                </option>
                                            ))}
                                        </select>
                                    </div>
                                )}
                            </div>

                            <div className="w-full pr-8">
                                <input
                                    type="text"
                                    value={Array.isArray(field.enum) ? field.enum.join(', ') : ''}
                                    onChange={(e) =>
                                        updateOutputField(index, {
                                            enum: e.target.value
                                                .split(',')
                                                .map((item) => item.trim())
                                                .filter(Boolean),
                                        })
                                    }
                                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                                    placeholder="enum1, enum2 (optional)"
                                />
                            </div>

                            <div className="w-full pr-8">
                                <CommonSwitch
                                    label="Nullable"
                                    checked={Boolean(field.nullable)}
                                    onChange={(checked) => updateOutputField(index, { nullable: checked })}
                                />
                            </div>

                            <button
                                onClick={() => removeOutputField(index)}
                                className="absolute right-3 top-4 p-1.5 text-slate-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>
                    ))}

                    {outputFields.length === 0 && (
                        <div className="text-center py-5 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                            {t('settings.skills.noParams')}
                        </div>
                    )}
                </div>
            </div>

            <CommonOutputList
                icon={<MessageSquare className="w-4 h-4" />}
                label={t('settings.skills.extractionOutputFields')}
                outputs={outputNames}
            />
        </div>
    )
}

export function KnowledgeRetrievalNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    const rawTopK = config.topK
    const topKValue = typeof rawTopK === 'number' ? String(rawTopK) : ''

    return (
        <div className="space-y-3">
            <CommonRichInput
                icon={<Search className="w-4 h-4" />}
                label={t('settings.skills.retrievalQuery')}
                value={(config.query as string) ?? '{{start.user_input}}'}
                onChange={(val) => onUpdate({ query: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.retrievalQueryPlaceholder')}
                rows={2}
            />

            <CommonSelect
                icon={<Settings2 className="w-4 h-4" />}
                label={t('settings.skills.retrievalMode')}
                value={(config.mode as string) ?? ''}
                onChange={(val) => onUpdate({ mode: val || undefined })}
                options={[
                    { label: t('settings.skills.retrievalModeFallback'), value: '' },
                    { label: 'Hybrid', value: 'hybrid' },
                    { label: 'Mix', value: 'mix' },
                    { label: 'Naive', value: 'naive' },
                    { label: 'Local', value: 'local' },
                    { label: 'Global', value: 'global' },
                ]}
            />

            <div className="space-y-1.5">
                <Label icon={<Settings2 className="w-4 h-4" />}>{t('settings.skills.retrievalTopK')}</Label>
                <input
                    type="number"
                    value={topKValue}
                    onChange={(e) => {
                        const val = e.target.value.trim()
                        if (!val) {
                            onUpdate({ topK: undefined })
                            return
                        }
                        const parsed = Number.parseInt(val, 10)
                        if (Number.isNaN(parsed)) return
                        onUpdate({ topK: Math.max(1, Math.min(50, parsed)) })
                    }}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                    min={1}
                    max={50}
                    placeholder={t('settings.skills.retrievalTopKFallback')}
                />
            </div>
        </div>
    )
}

type ContainerBodyNode = {
    nodeId: string
    nodeType: NodeType
    label: string
    config?: Record<string, unknown> | null
}

type ContainerBodyEdge = {
    edgeId: string
    sourceNodeId: string
    targetNodeId: string
    sourceHandle?: string
    targetHandle?: string
}

const CONTAINER_NODE_TYPES: Array<{ value: NodeType; labelKey: string }> = [
  { value: 'llm', labelKey: 'settings.skills.nodeTypes.llm' },
  { value: 'agent', labelKey: 'settings.skills.nodeTypes.agent' },
  { value: 'tool', labelKey: 'settings.skills.nodeTypes.tool' },
    { value: 'if_else', labelKey: 'settings.skills.nodeTypes.if_else' },
    { value: 'parameter_extractor', labelKey: 'settings.skills.nodeTypes.parameter_extractor' },
    { value: 'knowledge_retrieval', labelKey: 'settings.skills.nodeTypes.knowledge_retrieval' },
    { value: 'code_executor', labelKey: 'settings.skills.nodeTypes.code_executor' },
    { value: 'http_request', labelKey: 'settings.skills.nodeTypes.http_request' },
    { value: 'variable_assign', labelKey: 'settings.skills.nodeTypes.variable_assign' },
    { value: 'human_in_loop', labelKey: 'settings.skills.nodeTypes.human_in_loop' },
]

function normalizeBodyNodes(config: Record<string, unknown>): ContainerBodyNode[] {
    const raw = (config.bodyNodes ?? config.body_nodes) as unknown
    if (!Array.isArray(raw)) {
        return [{ nodeId: 'start', nodeType: 'start', label: 'Start', config: null }]
    }
    const nodes = raw
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
        .map((item) => ({
            nodeId: String(item.nodeId ?? item.node_id ?? ''),
            nodeType: String(item.nodeType ?? item.node_type ?? '') as NodeType,
            label: String(item.label ?? ''),
            config: item.config && typeof item.config === 'object' ? (item.config as Record<string, unknown>) : null,
        }))
        .filter((item) => item.nodeId)
    if (!nodes.some((item) => item.nodeType === 'start')) {
        return [{ nodeId: 'start', nodeType: 'start', label: 'Start', config: null }, ...nodes]
    }
    return nodes
}

function normalizeBodyEdges(config: Record<string, unknown>): ContainerBodyEdge[] {
    const raw = (config.bodyEdges ?? config.body_edges) as unknown
    if (!Array.isArray(raw)) return []
    return raw
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
        .map((item) => ({
            edgeId: String(item.edgeId ?? item.edge_id ?? ''),
            sourceNodeId: String(item.sourceNodeId ?? item.source_node_id ?? ''),
            targetNodeId: String(item.targetNodeId ?? item.target_node_id ?? ''),
            sourceHandle: String(item.sourceHandle ?? item.source_handle ?? 'output'),
            targetHandle: String(item.targetHandle ?? item.target_handle ?? 'input'),
        }))
        .filter((item) => item.edgeId && item.sourceNodeId && item.targetNodeId)
}

function useContainerBodyEditor(config: Record<string, unknown>, onUpdate: (updates: Record<string, unknown>) => void) {
    const bodyNodes = normalizeBodyNodes(config)
    const bodyEdges = normalizeBodyEdges(config)

    const persist = (nextNodes: ContainerBodyNode[], nextEdges: ContainerBodyEdge[]) => {
        onUpdate({ bodyNodes: nextNodes, bodyEdges: nextEdges })
    }

    const addNode = (nodeType: NodeType, label: string) => {
        const nodeId = `${nodeType}_${Date.now().toString(36)}`
        const nextNodes: ContainerBodyNode[] = [
            ...bodyNodes,
            {
                nodeId,
                nodeType,
                label,
                config: nodeType === 'tool' ? { toolName: '', inputBindings: {} } : null,
            },
        ]
        const lastNode = bodyNodes[bodyNodes.length - 1]
        const nextEdges: ContainerBodyEdge[] = [
            ...bodyEdges,
            {
                edgeId: `edge_${Date.now().toString(36)}`,
                sourceNodeId: lastNode?.nodeId || 'start',
                targetNodeId: nodeId,
                sourceHandle: 'output',
                targetHandle: 'input',
            },
        ]
        persist(nextNodes, nextEdges)
    }

    const removeNode = (nodeId: string) => {
        if (nodeId === 'start') return
        const nextNodes = bodyNodes.filter((node) => node.nodeId !== nodeId)
        const nextEdges = bodyEdges.filter((edge) => edge.sourceNodeId !== nodeId && edge.targetNodeId !== nodeId)
        persist(nextNodes, nextEdges)
    }

    return { bodyNodes, addNode, removeNode }
}

function ContainerBodyEditor({
    config,
    onUpdate,
}: {
    config: Record<string, unknown>
    onUpdate: (updates: Record<string, unknown>) => void
}) {
    const { t } = useTranslation()
    const { bodyNodes, addNode, removeNode } = useContainerBodyEditor(config, onUpdate)
    const userNodes = bodyNodes.filter((node) => node.nodeType !== 'start')
    return (
        <div className="space-y-2.5 pt-1.5">
            <Label icon={<Box className="w-4 h-4" />}>{t('settings.skills.containerBodyFlow')}</Label>
            <div className="space-y-2.5 rounded-xl border border-slate-200 bg-slate-50/50 p-3 shadow-sm">
                {userNodes.map((node) => (
                    <div key={node.nodeId} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm shadow-sm">
                        <span className="truncate font-medium text-slate-700">{node.label || node.nodeType}</span>
                        <button
                            type="button"
                            onClick={() => removeNode(node.nodeId)}
                            className="text-slate-400 hover:text-red-500 transition-colors p-1 hover:bg-red-50 rounded"
                        >
                            <Trash2 className="w-4 h-4" />
                        </button>
                    </div>
                ))}
                {userNodes.length === 0 && (
                    <div className="text-sm text-slate-500 py-3 text-center border-2 border-dashed border-slate-200 rounded-xl bg-white/50">
                        {t('settings.skills.containerBodyEmpty')}
                    </div>
                )}
                <div className="grid grid-cols-2 gap-2 mt-2">
                    {CONTAINER_NODE_TYPES.map((item) => (
                        <button
                            key={item.value}
                            type="button"
                            className="text-xs font-medium text-slate-600 border border-slate-200 bg-white rounded-lg px-3 py-2 hover:bg-primary/5 hover:border-primary/20 hover:text-primary transition-all shadow-sm text-left flex items-center gap-1.5"
                            onClick={() => addNode(item.value, t(item.labelKey))}
                        >
                            <Plus className="w-3.5 h-3.5" /> {t(item.labelKey)}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    )
}

export function IterationNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    const outputVariable = String(config.outputVariable ?? 'results')
    const outputs = [outputVariable, 'count', 'errors']
    return (
        <div className="space-y-4">
            <CommonRichInput
                icon={<FileText className="w-4 h-4" />}
                label={t('settings.skills.iterationInputSource')}
                value={String(config.inputSource ?? '')}
                onChange={(val) => onUpdate({ inputSource: val })}
                mentionParams={mentionParams}
                placeholder={t('settings.skills.iterationInputSourcePlaceholder')}
                rows={2}
                required
            />

            <div className="space-y-1.5">
                <Label icon={<Box className="w-4 h-4" />} required>{t('settings.skills.iterationOutputVariable')}</Label>
                <input
                    type="text"
                    value={outputVariable}
                    onChange={(e) => onUpdate({ outputVariable: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                    placeholder="results"
                />
            </div>

            <CommonRichInput
                icon={<Settings2 className="w-4 h-4" />}
                label={t('settings.skills.iterationOutputSelector')}
                value={String(config.outputSelector ?? '{{container.item}}')}
                onChange={(val) => onUpdate({ outputSelector: val })}
                mentionParams={mentionParams}
                placeholder="{{node.field}}"
                rows={2}
                required
            />

            <CommonSwitch
                icon={<Settings2 className="w-4 h-4" />}
                label={t('settings.skills.iterationParallelMode')}
                checked={Boolean(config.parallelMode)}
                onChange={(checked) => onUpdate({ parallelMode: checked })}
            />

            <CommonSelect
                icon={<Settings2 className="w-4 h-4" />}
                label={t('settings.skills.iterationErrorStrategy')}
                value={String(config.errorStrategy ?? 'fail_fast')}
                onChange={(val) => onUpdate({ errorStrategy: val || 'fail_fast' })}
                options={[
                    { label: t('settings.skills.iterationErrorFailFast'), value: 'fail_fast' },
                    { label: t('settings.skills.iterationErrorSkipItem'), value: 'skip_item' },
                ]}
            />

            <CommonSwitch
                icon={<Settings2 className="w-4 h-4" />}
                label={t('settings.skills.iterationFlattenOutput')}
                checked={config.flattenOutput !== false}
                onChange={(checked) => onUpdate({ flattenOutput: checked })}
            />

            <ContainerBodyEditor config={config} onUpdate={onUpdate} />

            <CommonOutputList icon={<MessageSquare className="w-4 h-4" />} label={t('settings.skills.workflowOutputList')} outputs={outputs} />
        </div>
    )
}

export function LoopNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    const maxIterations = Number(config.maxIterations ?? 10)
    const initialVars = Array.isArray(config.initialVars) ? config.initialVars : []
    const updateMappings = Array.isArray(config.updateMappings) ? config.updateMappings : []
    const terminationConditions = Array.isArray(config.terminationConditions) ? config.terminationConditions : []

    const updateInitialVar = (index: number, patch: Record<string, unknown>) => {
        const next = [...initialVars]
        const current = (next[index] as Record<string, unknown> | undefined) ?? { name: '', value: '' }
        next[index] = { ...current, ...patch }
        onUpdate({ initialVars: next })
    }

    const updateMapping = (index: number, patch: Record<string, unknown>) => {
        const next = [...updateMappings]
        const current = (next[index] as Record<string, unknown> | undefined) ?? { name: '', value: '' }
        next[index] = { ...current, ...patch }
        onUpdate({ updateMappings: next })
    }

    return (
        <div className="space-y-4">
            <CommonSelect
                icon={<Settings2 className="w-4 h-4" />}
                label={t('settings.skills.loopTerminationLogic')}
                value={String(config.terminationLogic ?? 'and')}
                onChange={(val) => onUpdate({ terminationLogic: val || 'and' })}
                options={[
                    { label: t('settings.skills.ifElseAnd'), value: 'and' },
                    { label: t('settings.skills.ifElseOr'), value: 'or' },
                ]}
            />

            <div className="space-y-1.5">
                <Label icon={<Hash className="w-4 h-4" />}>{t('settings.skills.loopMaxIterations')}</Label>
                <input
                    type="number"
                    value={Number.isFinite(maxIterations) ? maxIterations : 10}
                    min={1}
                    max={1000}
                    onChange={(e) => onUpdate({ maxIterations: Math.max(1, Math.min(1000, Number(e.target.value || 10))) })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700"
                />
            </div>

            <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                    <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.loopInitialVars')}</Label>
                    <button
                        type="button"
                        className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2 py-1.5 rounded-lg transition-colors border border-primary/10"
                        onClick={() => onUpdate({ initialVars: [...initialVars, { name: '', value: '' }] })}
                    >
                        <Plus className="w-3.5 h-3.5" />
                        {t('actions.add')}
                    </button>
                </div>
                <div className="space-y-2.5">
                    {initialVars.map((item, index) => (
                        <div key={index} className="flex gap-2.5 items-center">
                            <input
                                type="text"
                                value={String((item as Record<string, unknown>)?.name ?? '')}
                                onChange={(e) => updateInitialVar(index, { name: e.target.value })}
                                className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono"
                                placeholder="name"
                            />
                            <input
                                type="text"
                                value={String((item as Record<string, unknown>)?.value ?? '')}
                                onChange={(e) => updateInitialVar(index, { value: e.target.value })}
                                className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono"
                                placeholder="value"
                            />
                        </div>
                    ))}
                    {initialVars.length === 0 && (
                        <div className="text-center py-3 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                            No initial variables defined
                        </div>
                    )}
                </div>
            </div>

            <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                    <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.loopUpdateMappings')}</Label>
                    <button
                        type="button"
                        className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2 py-1.5 rounded-lg transition-colors border border-primary/10"
                        onClick={() => onUpdate({ updateMappings: [...updateMappings, { name: '', value: '' }] })}
                    >
                        <Plus className="w-3.5 h-3.5" />
                        {t('actions.add')}
                    </button>
                </div>
                <div className="space-y-2.5">
                    {updateMappings.map((item, index) => (
                        <div key={index} className="grid grid-cols-[1fr_2fr] gap-2.5 items-start">
                            <input
                                type="text"
                                value={String((item as Record<string, unknown>)?.name ?? '')}
                                onChange={(e) => updateMapping(index, { name: e.target.value })}
                                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono"
                                placeholder="name"
                            />
                            <div className="w-full">
                                <CommonRichInput
                                    value={String((item as Record<string, unknown>)?.value ?? '')}
                                    onChange={(val) => updateMapping(index, { value: val })}
                                    mentionParams={mentionParams}
                                    rows={2}
                                />
                            </div>
                        </div>
                    ))}
                    {updateMappings.length === 0 && (
                        <div className="text-center py-3 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                            No update mappings defined
                        </div>
                    )}
                </div>
            </div>

            <div className="space-y-2.5">
                <div className="flex items-center justify-between">
                    <Label icon={<List className="w-4 h-4" />}>{t('settings.skills.loopTerminationConditions')}</Label>
                    <button
                        type="button"
                        className="flex items-center gap-1.5 text-xs font-semibold bg-primary/5 text-primary hover:bg-primary/10 px-2 py-1.5 rounded-lg transition-colors border border-primary/10"
                        onClick={() => onUpdate({ terminationConditions: [...terminationConditions, { id: `cond_${Date.now()}`, variable: '', operator: 'is', value: '' }] })}
                    >
                        <Plus className="w-3.5 h-3.5" />
                        {t('settings.skills.ifElseAddCondition')}
                    </button>
                </div>
                <div className="space-y-2.5">
                    {terminationConditions.map((cond, index) => (
                        <div key={String((cond as Record<string, unknown>)?.id ?? index)} className="space-y-3 rounded-xl border border-slate-200/80 bg-slate-50/50 p-3 shadow-sm relative group">
                            <CommonRichInput
                                label={t('settings.skills.ifElseVariable')}
                                value={String((cond as Record<string, unknown>)?.variable ?? '')}
                                onChange={(val) => {
                                    const next = [...terminationConditions]
                                    next[index] = { ...(cond as Record<string, unknown>), variable: val }
                                    onUpdate({ terminationConditions: next })
                                }}
                                mentionParams={mentionParams}
                                rows={2}
                            />
                            <CommonSelect
                                label={t('settings.skills.ifElseOperator')}
                                value={String((cond as Record<string, unknown>)?.operator ?? 'is')}
                                onChange={(val) => {
                                    const next = [...terminationConditions]
                                    next[index] = { ...(cond as Record<string, unknown>), operator: val }
                                    onUpdate({ terminationConditions: next })
                                }}
                                options={[
                                    { label: t('settings.skills.operators.is'), value: 'is' },
                                    { label: t('settings.skills.operators.is_not'), value: 'is_not' },
                                    { label: t('settings.skills.operators.contains'), value: 'contains' },
                                    { label: t('settings.skills.operators.not_contains'), value: 'not_contains' },
                                    { label: t('settings.skills.operators.is_empty'), value: 'is_empty' },
                                    { label: t('settings.skills.operators.is_not_empty'), value: 'is_not_empty' },
                                    { label: t('settings.skills.operators.gt'), value: 'gt' },
                                    { label: t('settings.skills.operators.lt'), value: 'lt' },
                                    { label: t('settings.skills.operators.gte'), value: 'gte' },
                                    { label: t('settings.skills.operators.lte'), value: 'lte' },
                                ]}
                            />
                            <CommonRichInput
                                label={t('settings.skills.ifElseValue')}
                                value={String((cond as Record<string, unknown>)?.value ?? '')}
                                onChange={(val) => {
                                    const next = [...terminationConditions]
                                    next[index] = { ...(cond as Record<string, unknown>), value: val }
                                    onUpdate({ terminationConditions: next })
                                }}
                                mentionParams={mentionParams}
                                rows={2}
                            />
                            <button
                                onClick={() => {
                                    const next = [...terminationConditions]
                                    next.splice(index, 1)
                                    onUpdate({ terminationConditions: next })
                                }}
                                className="absolute right-3 top-3 opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 transition-all p-1.5 hover:bg-red-50 rounded-lg"
                                title={t('actions.remove')}
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>
                    ))}
                    {terminationConditions.length === 0 && (
                        <div className="text-center py-3 text-sm text-slate-500 border-2 border-dashed border-slate-200 rounded-xl bg-slate-50/50">
                            No termination conditions defined
                        </div>
                    )}
                </div>
            </div>

            <ContainerBodyEditor config={config} onUpdate={onUpdate} />

            <CommonOutputList
                icon={<MessageSquare className="w-4 h-4" />}
                label={t('settings.skills.workflowOutputList')}
                outputs={['iterations', 'terminated', 'last_item']}
            />
        </div>
    )
}
