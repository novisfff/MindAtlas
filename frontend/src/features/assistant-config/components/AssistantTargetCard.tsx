import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import {
    Bot,
    ChevronDown,
    ChevronRight,
    Copy,
    ExternalLink,
    Loader2,
    Trash2,
    Workflow,
    Clock,
    Hash
} from 'lucide-react'
import { WorkflowReadonlyPreview } from './workflow/WorkflowReadonlyPreview'
import type { AssistantExecutableTarget } from './skillTargetOptions'
import type { AssistantAgentProfile } from '../api/agents'
import type { AssistantWorkflow } from '../api/workflows'
import { cn } from '@/lib/utils'

interface AssistantTargetCardProps {
    target: AssistantExecutableTarget
    workflow?: AssistantWorkflow
    agent?: AssistantAgentProfile
    isExpanded: boolean
    onToggleExpand: () => void
    onEdit: () => void
    onCopy: () => void
    onDelete: () => void
    isCopying: boolean
    disableCopy: boolean
    isDeleting: boolean
    disableDelete: boolean
}

export const AssistantTargetCard = memo(function AssistantTargetCard({
    target,
    workflow,
    agent,
    isExpanded,
    onToggleExpand,
    onEdit,
    onCopy,
    onDelete,
    isCopying,
    disableCopy,
    isDeleting,
    disableDelete,
}: AssistantTargetCardProps) {
    const { t } = useTranslation()
    const isWorkflow = target.type === 'workflow'
    const agentTools = Array.isArray(agent?.tools)
        ? agent.tools.map((item: string) => String(item)).filter(Boolean)
        : []

    return (
        <div
            className={cn(
                "group rounded-xl border bg-card transition-all duration-200 ease-in-out",
                isExpanded
                    ? "border-primary/50 shadow-md ring-1 ring-primary/10"
                    : "hover:border-primary/30 hover:shadow-sm"
            )}
        >
            <div
                role="button"
                tabIndex={0}
                onClick={onToggleExpand}
                className="flex items-center gap-4 p-4 cursor-pointer"
            >
                {/* Icon & Type Indicator */}
                <div className={cn(
                    "flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border transition-colors",
                    isWorkflow
                        ? "bg-blue-50 text-blue-600 border-blue-100 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-900/30"
                        : "bg-purple-50 text-purple-600 border-purple-100 dark:bg-purple-900/20 dark:text-purple-400 dark:border-purple-900/30"
                )}>
                    {isWorkflow ? <Workflow className="h-6 w-6" /> : <Bot className="h-6 w-6" />}
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0 grid gap-1">
                    <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-base truncate text-foreground">
                            {target.name}
                        </h3>
                        <div className="flex items-center gap-1.5">
                            {target.isSystemDefault && (
                                <span className="inline-flex items-center rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-500/20">
                                    {t('settings.skills.systemDefaultTarget')}
                                </span>
                            )}
                            {target.isSystem && (
                                <span className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground ring-1 ring-inset ring-gray-500/10">
                                    {t('settings.skills.system')}
                                </span>
                            )}
                        </div>
                    </div>

                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <div className="flex items-center gap-1">
                            <Hash className="w-3.5 h-3.5" />
                            <span>{t('settings.skills.referenceCount', { count: target.referenceCount })}</span>
                        </div>
                        {(target.systemBehaviorReferenceCount ?? 0) > 0 && (
                          <div className="flex items-center gap-1">
                            <Hash className="w-3.5 h-3.5" />
                            <span>{t('settings.systemBehaviors.referenceCount', { count: target.systemBehaviorReferenceCount })}</span>
                          </div>
                        )}
                        {workflow?.updatedAt && (
                            <div className="flex items-center gap-1">
                                <Clock className="w-3.5 h-3.5" />
                                <span>{new Date(workflow.updatedAt).toLocaleDateString()}</span>
                            </div>
                        )}
                    </div>
                </div>

                {/* Actions */}
                <div className={cn(
                    "flex items-center gap-1 transition-opacity focus-within:opacity-100",
                    isCopying ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                )}>
                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            onEdit()
                        }}
                        className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                        title={t('settings.skills.editTarget')}
                    >
                        <ExternalLink className="w-4 h-4" />
                    </button>
                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            onCopy()
                        }}
                        disabled={disableCopy}
                        className={cn(
                            "p-2 rounded-lg transition-colors",
                            disableCopy
                                ? "cursor-not-allowed opacity-50 text-muted-foreground"
                                : "hover:bg-muted text-muted-foreground hover:text-foreground"
                        )}
                        title={isCopying ? t('messages.loading') : t('settings.skills.copyAsDuplicate')}
                    >
                        {isCopying ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                            <Copy className="w-4 h-4" />
                        )}
                    </button>

                    <div className="w-px h-4 bg-border mx-1" />

                    <button
                        onClick={(e) => {
                            e.stopPropagation()
                            onDelete()
                        }}
                        disabled={disableDelete || isDeleting}
                        title={disableDelete ? t('settings.skills.targetInUse') : t('common.delete')}
                        className={cn(
                            "p-2 rounded-lg transition-colors",
                            disableDelete
                                ? "opacity-50 cursor-not-allowed text-muted-foreground"
                                : "text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        )}
                    >
                        <Trash2 className="w-4 h-4" />
                    </button>
                </div>

                <div className="pl-2 text-muted-foreground">
                    {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                </div>
            </div>

            {/* Expanded Content */}
            <div
                className={cn(
                    "grid transition-all duration-300 ease-in-out",
                    isExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                )}
            >
                <div className="overflow-hidden">
                    <div className="p-4 pt-0 border-t border-border/50">
                        <div className="pt-4 space-y-4">
                            {isWorkflow ? (
                                workflow ? (
                                    <div className="space-y-4">
                                        {workflow.description && (
                                            <div className="text-sm text-muted-foreground leading-relaxed">
                                                {workflow.description}
                                            </div>
                                        )}
                                        <div className="rounded-lg border bg-muted/30 overflow-hidden">
                                            <WorkflowReadonlyPreview
                                                workflow={workflow}
                                                onOpenEditor={onEdit}
                                            />
                                        </div>
                                    </div>
                                ) : (
                                    <p className="text-sm text-muted-foreground">{t('messages.noData')}</p>
                                )
                            ) : (
                                <div className="grid gap-6">
                                    {/* Agent Info Grid */}
                                    <div className="grid gap-4 md:grid-cols-2">
                                        <div className="space-y-1.5">
                                            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                                {t('settings.skills.description')}
                                            </label>
                                            <div className="text-sm p-3 rounded-lg bg-muted/40 border border-border/50">
                                                {agent?.description || '-'}
                                            </div>
                                        </div>

                                        <div className="space-y-1.5">
                                            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                                {t('settings.skills.targetRuntimeConfig')}
                                            </label>
                                            <div className="text-sm p-3 rounded-lg bg-muted/40 border border-border/50 flex items-center gap-2">
                                                <div className={cn(
                                                    "w-2 h-2 rounded-full",
                                                    agent?.kbConfig?.enabled ? "bg-green-500" : "bg-gray-300"
                                                )} />
                                                <span>
                                                    {t('settings.skills.agentKbEnabled')}:{' '}
                                                    <span className="font-medium">
                                                        {agent?.kbConfig?.enabled
                                                            ? t('settings.skills.enabledStateOn')
                                                            : t('settings.skills.enabledStateOff')}
                                                    </span>
                                                </span>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Tools */}
                                    <div className="space-y-2">
                                        <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            {t('settings.skills.agentTools')}
                                        </label>
                                        {agentTools.length === 0 ? (
                                            <p className="text-sm text-muted-foreground italic">{t('settings.skills.noToolsSelected')}</p>
                                        ) : (
                                            <div className="flex flex-wrap gap-2">
                                                {agentTools.map((tool: string) => (
                                                    <span
                                                        key={tool}
                                                        className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-primary/10 text-primary border border-primary/20"
                                                    >
                                                        {tool}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                    </div>

                                    {/* System Prompt */}
                                    <div className="space-y-2">
                                        <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                                            {t('settings.skills.systemPrompt')}
                                        </label>
                                        <div className="rounded-lg border bg-muted/30 p-4 text-sm font-mono text-muted-foreground whitespace-pre-wrap max-h-64 overflow-y-auto">
                                            {agent?.systemPrompt || '-'}
                                        </div>
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
