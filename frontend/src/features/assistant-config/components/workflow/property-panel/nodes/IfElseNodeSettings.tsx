
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, X } from 'lucide-react'
import { CommonRichInput, CommonSelect, Label } from '../CommonInputs'
import { RichMentionInput } from '../../../RichMentionInput'
import type { IfElseBranch, NodeConfig } from '../../../../api/workflow'
import {
    IF_ELSE_OPERATOR_OPTIONS,
    createBranchId,
    createBranchLabel,
    createDefaultCondition,
    ifElseOperatorRequiresValue,
    normalizeIfElseConfig,
} from '../../ifElseConfig'
import type { NodeSettingsProps } from './ToolNodeSettings'
import { useWorkflowEditorStore } from '../../../../stores/workflow-editor-store'

export function IfElseNodeSettings({ config, onUpdate, mentionParams }: NodeSettingsProps) {
    const { t } = useTranslation()
    const edges = useWorkflowEditorStore((s) => s.edges)
    const setEdges = useWorkflowEditorStore((s) => s.setEdges)
    const selectedNodeId = useWorkflowEditorStore((s) => s.selectedNodeId)

    const ifElseNormalized = normalizeIfElseConfig(config)
    const conditionVariableOptions = mentionParams
        .map((item) => item.name)
        .filter((name) => name.includes('.'))

    const writeIfElseConfig = (branches: IfElseBranch[]) => {
        // We only update the branches in the config
        onUpdate({
            branches
        })
    }

    const handleLogicChange = (branchId: string, logic: 'and' | 'or') => {
        const nextBranches = ifElseNormalized.branches.map((item) =>
            item.id === branchId ? { ...item, logic } : item
        )
        writeIfElseConfig(nextBranches)
    }

    const handleDeleteBranch = (branchId: string) => {
        const nextBranches = ifElseNormalized.branches.filter((item) => item.id !== branchId)

        // Also remove edges connected to this branch
        if (selectedNodeId) {
            const filteredEdges = edges.filter(
                (edge) => !(edge.source === selectedNodeId && edge.sourceHandle === branchId)
            )
            setEdges(filteredEdges)
        }

        writeIfElseConfig(nextBranches)
    }

    const handleUpdateCondition = (branchId: string, conditionId: string, updates: Record<string, any>) => {
        const nextBranches = ifElseNormalized.branches.map((br) => {
            if (br.id !== branchId) return br
            return {
                ...br,
                conditions: br.conditions.map(c => c.id === conditionId ? { ...c, ...updates } : c)
            }
        })
        writeIfElseConfig(nextBranches)
    }

    const handleRemoveCondition = (branchId: string, conditionId: string) => {
        const nextBranches = ifElseNormalized.branches.map((br) => {
            if (br.id !== branchId) return br
            if (br.conditions.length <= 1) return br // Prevent removing last condition
            return {
                ...br,
                conditions: br.conditions.filter(c => c.id !== conditionId)
            }
        })
        writeIfElseConfig(nextBranches)
    }

    const handleAddCondition = (branchId: string) => {
        const nextBranches = ifElseNormalized.branches.map((br) => {
            if (br.id !== branchId) return br
            return {
                ...br,
                conditions: [...br.conditions, createDefaultCondition()]
            }
        })
        writeIfElseConfig(nextBranches)
    }

    const handleAddBranch = () => {
        const nextIndex = ifElseNormalized.branches.length
        const nextBranches = [
            ...ifElseNormalized.branches,
            {
                id: createBranchId('elif'),
                label: createBranchLabel(nextIndex),
                logic: 'and' as const,
                conditions: [createDefaultCondition()],
            },
        ]
        writeIfElseConfig(nextBranches)
    }

    return (
        <div className="space-y-6">
            <datalist id={`if_else_var_options_settings`}>
                {conditionVariableOptions.map((name) => (
                    <option key={name} value={name} />
                ))}
            </datalist>

            {ifElseNormalized.branches.map((branch, branchIndex) => (
                <div key={branch.id} className="relative rounded-lg border border-border shadow-sm bg-card/40 overflow-hidden">
                    {/* Branch Header */}
                    <div className="flex items-center justify-between px-3 py-2 bg-muted/30 border-b border-border/50">
                        <div className="font-bold text-xs text-primary">
                            {branchIndex === 0 ? t('settings.skills.ifElseIf') : t('settings.skills.ifElseElif')}
                        </div>

                        <div className="flex items-center gap-2">
                            <select
                                value={branch.logic}
                                onChange={(e) => handleLogicChange(branch.id, e.target.value as 'and' | 'or')}
                                className="h-6 text-[10px] font-bold uppercase rounded border border-primary/20 bg-background px-1 text-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
                            >
                                <option value="and">{t('settings.skills.ifElseAnd')}</option>
                                <option value="or">{t('settings.skills.ifElseOr')}</option>
                            </select>

                            {branchIndex > 0 && (
                                <button
                                    onClick={() => handleDeleteBranch(branch.id)}
                                    className="text-muted-foreground hover:text-red-500 transition-colors"
                                >
                                    <Trash2 className="w-3.5 h-3.5" />
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Conditions */}
                    <div className="p-3 space-y-3">
                        {branch.conditions.map((condition) => {
                            const requiresValue = ifElseOperatorRequiresValue(condition.operator)
                            return (
                                <div key={condition.id} className="group relative flex flex-col gap-2 p-2 rounded border bg-background hover:border-primary/30 transition-colors">
                                    <div className="grid grid-cols-[1fr,100px] gap-2">
                                        <div className="space-y-1">
                                            <input
                                                type="text"
                                                list={`if_else_var_options_settings`}
                                                value={condition.variable}
                                                onChange={(e) => handleUpdateCondition(branch.id, condition.id, { variable: e.target.value })}
                                                className="w-full px-2 py-1.5 text-xs rounded border bg-background focus:ring-1 focus:ring-primary/30"
                                                placeholder={t('settings.skills.ifElseVariable')}
                                            />
                                        </div>
                                        <div className="space-y-1">
                                            <select
                                                value={condition.operator}
                                                onChange={(e) => {
                                                    const nextOperator = e.target.value
                                                    handleUpdateCondition(branch.id, condition.id, {
                                                        operator: nextOperator,
                                                        value: ifElseOperatorRequiresValue(nextOperator) ? condition.value : null
                                                    })
                                                }}
                                                className="w-full px-2 py-1.5 text-xs rounded border bg-background"
                                            >
                                                {IF_ELSE_OPERATOR_OPTIONS.map((op) => (
                                                    <option key={op.value} value={op.value}>{t(`settings.skills.operators.${op.labelKey}`)}</option>
                                                ))}
                                            </select>
                                        </div>
                                    </div>

                                    {requiresValue && (
                                        <RichMentionInput
                                            value={condition.value ?? ''}
                                            onChange={(val: string) => handleUpdateCondition(branch.id, condition.id, { value: val })}
                                            inputParams={mentionParams}
                                            placeholder={t('settings.skills.ifElseValuePlaceholder')}
                                            multiline={false}
                                            className="min-h-[32px] text-xs"
                                        />
                                    )}

                                    {branch.conditions.length > 1 && (
                                        <button
                                            onClick={() => handleRemoveCondition(branch.id, condition.id)}
                                            className="absolute -right-2 -top-2 opacity-0 group-hover:opacity-100 bg-background border rounded-full p-0.5 shadow-sm text-muted-foreground hover:text-red-500 transition-all"
                                        >
                                            <X className="w-3 h-3" />
                                        </button>
                                    )}
                                </div>
                            )
                        })}

                        <button
                            onClick={() => handleAddCondition(branch.id)}
                            className="w-full py-1.5 flex items-center justify-center gap-1 text-[10px] uppercase font-medium text-muted-foreground border border-dashed rounded hover:bg-muted/50 transition-colors"
                        >
                            <Plus className="w-3 h-3" /> {t('settings.skills.ifElseAddCondition')}
                        </button>
                    </div>
                </div>
            ))}

            <button
                onClick={handleAddBranch}
                className="w-full py-2 flex items-center justify-center gap-2 text-xs font-semibold text-primary border border-primary/20 bg-primary/5 rounded-lg hover:bg-primary/10 transition-colors"
            >
                <Plus className="w-3.5 h-3.5" />
                {t('settings.skills.ifElseAddBranch')}
            </button>

            <div className="rounded-lg border border-dashed bg-muted/20 p-3 text-center">
                <div className="text-xs font-bold text-muted-foreground">{t('settings.skills.ifElseElse')}</div>
                <p className="mt-1 text-[10px] text-muted-foreground/80">
                    {t('settings.skills.ifElseElseDescription')}
                </p>
            </div>
        </div>
    )
}
