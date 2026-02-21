import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import {
    Bot,
    ChevronDown,
    ChevronRight,
    ExternalLink,
    Pencil,
    Power,
    Trash2,
    Workflow,
    Zap
} from 'lucide-react'
import type { AssistantSkill } from '../api/skills'
import { cn } from '@/lib/utils'

interface SkillCardProps {
    skill: AssistantSkill
    isExpanded: boolean
    onToggleExpand: () => void
    onEdit: () => void
    onDelete: () => void
    onToggleEnabled: () => void
    isToggling: boolean
}

export const SkillCard = memo(function SkillCard({
    skill,
    isExpanded,
    onToggleExpand,
    onEdit,
    onDelete,
    onToggleEnabled,
    isToggling,
}: SkillCardProps) {
    const { t } = useTranslation()

    const targetLabel = skill.targetType === 'workflow'
        ? t('settings.skills.targetTypeWorkflow')
        : skill.targetType === 'agent'
            ? t('settings.skills.targetTypeAgent')
            : '-'

    const targetName = skill.targetSummary?.name || '-'
    const isGeneralChat = skill.name === 'general_chat'

    return (
        <div
            className={cn(
                "group rounded-xl border bg-card transition-all duration-200 ease-in-out",
                skill.enabled
                    ? "border-purple-500/30 bg-purple-50/30 dark:bg-purple-900/10"
                    : "hover:border-primary/30 hover:shadow-sm",
                isExpanded && "border-primary/50 shadow-md ring-1 ring-primary/10"
            )}
        >
            <div className="flex items-center gap-4 p-4">
                {/* Toggle Button */}
                <button
                    onClick={(e) => {
                        e.stopPropagation()
                        onToggleEnabled()
                    }}
                    disabled={isToggling || isGeneralChat}
                    title={
                        isGeneralChat
                            ? t('settings.skills.cannotDisable')
                            : skill.enabled
                                ? t('settings.skills.disable')
                                : t('settings.skills.enable')
                    }
                    className={cn(
                        "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border transition-all duration-200",
                        skill.enabled
                            ? "bg-purple-100 text-purple-600 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800/50"
                            : "bg-muted text-muted-foreground border-transparent hover:bg-muted/80",
                        isGeneralChat && "opacity-50 cursor-not-allowed",
                        isToggling && "animate-pulse"
                    )}
                >
                    <Power className="w-5 h-5" />
                </button>

                {/* Content */}
                <div
                    className="flex-1 min-w-0 grid gap-1 cursor-pointer"
                    onClick={onToggleExpand}
                >
                    <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-base truncate text-foreground flex items-center gap-2">
                            {skill.name}
                            {/* Target Type Icon */}
                            {skill.targetType === 'workflow' ? (
                                <Workflow className="w-3.5 h-3.5 text-muted-foreground/70" />
                            ) : (
                                <Bot className="w-3.5 h-3.5 text-muted-foreground/70" />
                            )}
                        </h3>
                        <div className="flex items-center gap-1.5">
                            <span className={cn(
                                "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
                                skill.isSystem
                                    ? "bg-purple-50 text-purple-700 ring-purple-600/10 dark:bg-purple-400/10 dark:text-purple-400 dark:ring-purple-400/20"
                                    : "bg-blue-50 text-blue-700 ring-blue-600/10 dark:bg-blue-400/10 dark:text-blue-400 dark:ring-blue-400/20"
                            )}>
                                {skill.isSystem ? t('settings.skills.system') : t('settings.skills.custom')}
                            </span>
                        </div>
                    </div>

                    <p className="text-sm text-muted-foreground truncate leading-relaxed">
                        {skill.description}
                    </p>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity focus-within:opacity-100">
                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            onEdit()
                        }}
                        className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                        title={t('common.edit')}
                    >
                        <Pencil className="w-4 h-4" />
                    </button>

                    {!skill.isSystem && (
                        <>
                            <div className="w-px h-4 bg-border mx-1" />
                            <button
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onDelete()
                                }}
                                title={t('common.delete')}
                                className="p-2 rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </>
                    )}
                </div>

                <button
                    onClick={onToggleExpand}
                    className="pl-2 text-muted-foreground hover:text-foreground transition-colors"
                >
                    {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </button>
            </div>

            {/* Expanded Content */}
            <div
                className={cn(
                    "grid transition-all duration-300 ease-in-out",
                    isExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                )}
            >
                <div className="overflow-hidden">
                    <div className="p-4 pt-0 border-t border-border/50 bg-muted/5">
                        <div className="pt-4 grid gap-4 md:grid-cols-2">
                            {/* Bound Target Info */}
                            <div className="space-y-2">
                                <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                                    <Zap className="w-3 h-3" />
                                    {t('settings.skills.boundTarget')}
                                </label>
                                <div className="text-sm p-3 rounded-lg bg-background border border-border/50 flex items-center justify-between group/target">
                                    <div className="flex items-center gap-2">
                                        {skill.targetType === 'workflow' ? (
                                            <div className="p-1 rounded bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                                                <Workflow className="w-3.5 h-3.5" />
                                            </div>
                                        ) : (
                                            <div className="p-1 rounded bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400">
                                                <Bot className="w-3.5 h-3.5" />
                                            </div>
                                        )}
                                        <span className="font-medium">{targetName}</span>
                                        <span className="text-xs text-muted-foreground">({targetLabel})</span>
                                    </div>
                                </div>
                            </div>

                            {/* Intent Examples */}
                            {skill.intentExamples && skill.intentExamples.length > 0 && (
                                <div className="space-y-2">
                                    <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                        {t('settings.skills.intentExamples')}
                                    </label>
                                    <div className="flex flex-wrap gap-1.5 p-3 rounded-lg bg-background border border-border/50 min-h-[46px]">
                                        {skill.intentExamples.map((ex, i) => (
                                            <span
                                                key={i}
                                                className="inline-flex items-center px-2 py-1 rounded-md text-xs bg-muted/50 text-foreground border border-border/50"
                                            >
                                                {ex}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
})
