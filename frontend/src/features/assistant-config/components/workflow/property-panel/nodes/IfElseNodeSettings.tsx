
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, X, GitBranch, GitCommit } from 'lucide-react'
import { RichMentionInput } from '../../../RichMentionInput'
import type { IfElseBranch } from '../../../../api/workflow'
import {
    IF_ELSE_OPERATOR_OPTIONS,
    createBranchId,
    createBranchLabel,
    createDefaultCondition,
    ifElseOperatorRequiresValue,
    normalizeIfElseConfig,
} from '../../ifElseConfig'
import type { NodeSettingsProps } from './ToolNodeSettings'

export function IfElseNodeSettings({ config, onUpdate, mentionParams, onDeleteBranchEdges }: NodeSettingsProps) {
    const { t } = useTranslation()

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
        writeIfElseConfig(nextBranches)
        onDeleteBranchEdges?.(branchId)
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
        <div className="space-y-4">
            <datalist id={`if_else_var_options_settings`}>
                {conditionVariableOptions.map((name) => (
                    <option key={name} value={name} />
                ))}
            </datalist>

            {ifElseNormalized.branches.map((branch, branchIndex) => (
                <div key={branch.id} className="relative rounded-xl border border-slate-200/80 shadow-sm bg-slate-50/50 overflow-hidden">
                    {/* Branch Header */}
                    <div className="flex items-center justify-between px-3 py-2 bg-slate-100 border-b border-slate-200/80">
                        <div className="flex items-center gap-1.5 font-bold text-xs text-primary uppercase tracking-widest">
                            <GitBranch className="w-3.5 h-3.5" />
                            {branchIndex === 0 ? t('settings.skills.ifElseIf') : t('settings.skills.ifElseElif')}
                        </div>

                        <div className="flex items-center gap-2">
                            <div className="relative">
                                <select
                                    value={branch.logic}
                                    onChange={(e) => handleLogicChange(branch.id, e.target.value as 'and' | 'or')}
                                    className="h-6 text-[10px] font-bold uppercase rounded border border-slate-300 bg-white px-1.5 focus:outline-none focus:ring-2 focus:ring-primary/20 text-slate-700 shadow-sm cursor-pointer"
                                >
                                    <option value="and">{t('settings.skills.ifElseAnd')}</option>
                                    <option value="or">{t('settings.skills.ifElseOr')}</option>
                                </select>
                            </div>

                            {branchIndex > 0 && (
                                <button
                                    onClick={() => handleDeleteBranch(branch.id)}
                                    className="text-slate-400 hover:text-red-500 transition-colors p-1"
                                >
                                    <Trash2 className="w-4 h-4" />
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Conditions */}
                    <div className="p-2.5 space-y-2.5">
                        {branch.conditions.map((condition) => {
                            const requiresValue = ifElseOperatorRequiresValue(condition.operator)
                            return (
                                <div key={condition.id} className="group relative flex flex-col gap-2.5 p-2.5 rounded-xl border border-slate-200 bg-white shadow-sm hover:border-slate-300 transition-all">
                                    <div className="flex gap-2 w-full pr-8">
                                        <input
                                            type="text"
                                            list={`if_else_var_options_settings`}
                                            value={condition.variable}
                                            onChange={(e) => handleUpdateCondition(branch.id, condition.id, { variable: e.target.value })}
                                            className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-200/60 bg-slate-50 hover:bg-slate-100/60 focus:bg-white focus:ring-[3px] focus:ring-primary/10 focus:border-primary/30 outline-none transition-all shadow-[inset_0_1px_2px_rgba(0,0,0,0.02)] text-slate-700 font-mono min-w-0"
                                            placeholder={t('settings.skills.ifElseVariable')}
                                        />
                                        <div className="relative shrink-0 w-36">
                                            <select
                                                value={condition.operator}
                                                onChange={(e) => {
                                                    const nextOperator = e.target.value
                                                    handleUpdateCondition(branch.id, condition.id, {
                                                        operator: nextOperator,
                                                        value: ifElseOperatorRequiresValue(nextOperator) ? condition.value : null
                                                    })
                                                }}
                                                className="w-full px-2.5 py-1.5 text-sm rounded-lg border border-slate-200 focus:ring-2 focus:ring-primary/20 outline-none transition-all shadow-sm appearance-none cursor-pointer"
                                                style={{
                                                    backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`,
                                                    backgroundPosition: 'right 0.5rem center',
                                                    backgroundRepeat: 'no-repeat',
                                                    backgroundSize: '1.5em 1.5em',
                                                    paddingRight: '2.5rem'
                                                }}
                                            >
                                                {IF_ELSE_OPERATOR_OPTIONS.map((op) => (
                                                    <option key={op.value} value={op.value}>{t(`settings.skills.operators.${op.labelKey}`)}</option>
                                                ))}
                                            </select>
                                        </div>
                                    </div>

                                    {requiresValue && (
                                        <div className="w-full pr-8">
                                            <div className="rounded-lg border border-slate-200 shadow-sm focus-within:ring-2 focus-within:ring-primary/20 outline-none transition-all overflow-hidden p-0.5">
                                                <RichMentionInput
                                                    value={condition.value ?? ''}
                                                    onChange={(val: string) => handleUpdateCondition(branch.id, condition.id, { value: val })}
                                                    inputParams={mentionParams}
                                                    placeholder={t('settings.skills.ifElseValuePlaceholder')}
                                                    multiline={false}
                                                    className="min-h-[32px] text-sm border-0 focus:ring-0"
                                                />
                                            </div>
                                        </div>
                                    )}

                                    {branch.conditions.length > 1 && (
                                        <button
                                            onClick={() => handleRemoveCondition(branch.id, condition.id)}
                                            className="absolute right-2 top-3 opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 transition-all p-1.5 hover:bg-red-50 rounded-lg"
                                            title={t('actions.remove')}
                                        >
                                            <X className="w-4 h-4" />
                                        </button>
                                    )}
                                </div>
                            )
                        })}

                        <button
                            onClick={() => handleAddCondition(branch.id)}
                            className="w-full py-1.5 flex items-center justify-center gap-1 text-xs font-semibold text-slate-500 border-2 border-dashed border-slate-200 rounded-xl hover:bg-slate-50 hover:border-slate-300 hover:text-slate-700 transition-colors"
                        >
                            <Plus className="w-3.5 h-3.5" /> {t('settings.skills.ifElseAddCondition')}
                        </button>
                    </div>
                </div>
            ))}

            <button
                onClick={handleAddBranch}
                className="w-full py-2 flex items-center justify-center gap-2 text-sm font-semibold text-primary border border-primary/20 bg-primary/5 rounded-xl hover:bg-primary/10 transition-colors shadow-sm"
            >
                <Plus className="w-4 h-4" />
                {t('settings.skills.ifElseAddBranch')}
            </button>

            <div className="rounded-xl border-2 border-dashed border-slate-200 bg-slate-50 p-3 text-center flex flex-col items-center">
                <GitCommit className="w-5 h-5 text-slate-400 mb-1" />
                <div className="text-sm font-bold text-slate-500 uppercase tracking-widest">{t('settings.skills.ifElseElse')}</div>
                <p className="mt-1 text-xs text-slate-400">
                    {t('settings.skills.ifElseElseDescription')}
                </p>
            </div>
        </div>
    )
}
