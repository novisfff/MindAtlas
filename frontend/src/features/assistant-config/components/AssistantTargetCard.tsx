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
import { Button } from '@/components/ui/button'
import { uiChrome } from '@/components/ui/styles'
import { SettingsBadge, SettingsInset } from '@/features/settings/components/SettingsShell'
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

interface ReferenceBadgeProps {
    count: number
    label: string
}

function ReferenceBadge({ count, label }: ReferenceBadgeProps) {
    if (count <= 0) return null
    return (
        <div className="flex items-center gap-1">
            <Hash className="h-3.5 w-3.5" />
            <span>{label}</span>
        </div>
    )
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
                uiChrome.card,
                "group overflow-hidden p-4 transition-all duration-200 ease-in-out",
                isExpanded
                    ? "border-primary/25 ring-1 ring-primary/10"
                    : "hover:border-primary/20"
            )}
        >
            <div
                role="button"
                tabIndex={0}
                onClick={onToggleExpand}
                className="flex cursor-pointer items-start gap-4"
            >
                <div className={cn(
                    uiChrome.control,
                    "flex h-11 w-11 shrink-0 items-center justify-center transition-colors shadow-none",
                    isWorkflow
                        ? "border-primary/15 bg-primary/10 text-primary"
                        : "border-primary/15 bg-primary/10 text-primary"
                )}>
                    {isWorkflow ? <Workflow className="h-6 w-6" /> : <Bot className="h-6 w-6" />}
                </div>

                <div className="min-w-0 flex-1 space-y-3">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="truncate text-base font-semibold text-foreground">
                            {target.name}
                        </h3>
                                {target.isSystemDefault && (
                                    <SettingsBadge className="border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
                                    {t('settings.skills.systemDefaultTarget')}
                                    </SettingsBadge>
                                )}
                                {target.isSystem && (
                                    <SettingsBadge>
                                    {t('settings.skills.system')}
                                    </SettingsBadge>
                                )}
                            </div>

                            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                                <ReferenceBadge
                                    count={target.referenceCount}
                                    label={t('settings.skills.referenceCount', { count: target.referenceCount })}
                                />
                                <ReferenceBadge
                                    count={target.systemBehaviorReferenceCount ?? 0}
                                    label={t('settings.systemBehaviors.referenceCount', {
                                        count: target.systemBehaviorReferenceCount ?? 0,
                                    })}
                                />
                                <ReferenceBadge
                                    count={target.openclawReferenceCount ?? 0}
                                    label={t('openclawIntegration.referenceCount', {
                                        count: target.openclawReferenceCount ?? 0,
                                    })}
                                />
                                {workflow?.updatedAt && (
                                    <div className="flex items-center gap-1">
                                        <Clock className="h-3.5 w-3.5" />
                                        <span>{new Date(workflow.updatedAt).toLocaleDateString()}</span>
                                    </div>
                                )}
                            </div>
                        </div>

                        <div className={cn(
                            "flex items-center gap-1 transition-opacity focus-within:opacity-100",
                            isCopying ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                        )}>
                            <Button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onEdit()
                                }}
                                variant="ghost"
                                size="icon"
                                title={t('settings.skills.editTarget')}
                            >
                                <ExternalLink className="h-4 w-4" />
                            </Button>
                            <Button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onCopy()
                                }}
                                disabled={disableCopy}
                                variant="ghost"
                                size="icon"
                                className={disableCopy ? 'cursor-not-allowed opacity-50 text-muted-foreground' : ''}
                                title={isCopying ? t('messages.loading') : t('settings.skills.copyAsDuplicate')}
                            >
                                {isCopying ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <Copy className="h-4 w-4" />
                                )}
                            </Button>

                            <Button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onDelete()
                                }}
                                disabled={disableDelete || isDeleting}
                                title={disableDelete ? t('settings.skills.targetInUse') : t('common.delete')}
                                variant="ghost"
                                size="icon"
                                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                            >
                                <Trash2 className="h-4 w-4" />
                            </Button>

                            <Button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onToggleExpand()
                                }}
                                variant="ghost"
                                size="icon"
                            >
                                {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </Button>
                        </div>
                    </div>
                </div>
            </div>

            <div
                className={cn(
                    "grid transition-all duration-300 ease-in-out",
                    isExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                )}
            >
                <div className="overflow-hidden">
                    <div className="mt-4 border-t border-border/70 pt-4">
                        <div className="space-y-4">
                            {isWorkflow ? (
                                workflow ? (
                                    <div className="space-y-4">
                                        {workflow.description && (
                                            <SettingsInset className="text-sm leading-6 text-muted-foreground">
                                                {workflow.description}
                                            </SettingsInset>
                                        )}
                                        <div className="overflow-hidden rounded-[20px] border border-border/70 bg-muted/20">
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
                                    <div className="grid gap-4 md:grid-cols-2">
                                        <SettingsInset className="space-y-1.5">
                                            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                                                {t('settings.skills.description')}
                                            </label>
                                            <div className={cn(uiChrome.control, 'px-3 py-3 text-sm shadow-none')}>
                                                {agent?.description || '-'}
                                            </div>
                                        </SettingsInset>

                                        <SettingsInset className="space-y-1.5">
                                            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                                                {t('settings.skills.targetRuntimeConfig')}
                                            </label>
                                            <div className={cn(uiChrome.control, 'flex items-center gap-2 px-3 py-3 text-sm shadow-none')}>
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
                                        </SettingsInset>
                                    </div>

                                    <SettingsInset className="space-y-2">
                                        <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                                            {t('settings.skills.agentTools')}
                                        </label>
                                        {agentTools.length === 0 ? (
                                            <p className="text-sm text-muted-foreground italic">{t('settings.skills.noToolsSelected')}</p>
                                        ) : (
                                            <div className="flex flex-wrap gap-2">
                                                {agentTools.map((tool: string) => (
                                                    <SettingsBadge
                                                        key={tool}
                                                        className="border-primary/15 bg-primary/10 text-primary"
                                                    >
                                                        {tool}
                                                    </SettingsBadge>
                                                ))}
                                            </div>
                                        )}
                                    </SettingsInset>

                                    <SettingsInset className="space-y-2">
                                        <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                                            {t('settings.skills.systemPrompt')}
                                        </label>
                                        <div className="max-h-64 overflow-y-auto rounded-[12px] border border-border/70 bg-background/92 p-4 font-mono text-sm text-muted-foreground whitespace-pre-wrap">
                                            {agent?.systemPrompt || '-'}
                                        </div>
                                    </SettingsInset>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
})
