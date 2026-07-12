import { memo, useEffect, useState, type CSSProperties } from 'react'
import { useDraggable } from '@dnd-kit/core'
import { useTranslation } from 'react-i18next'
import {
    Bot,
    ChevronDown,
    ChevronRight,
    Copy,
    ExternalLink,
    GripVertical,
    Loader2,
    MoveRight,
    Trash2,
    Workflow,
    Clock,
    Hash
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { uiChrome, uiField } from '@/components/ui/styles'
import { SettingsBadge, SettingsInset } from '@/features/settings/components/SettingsShell'
import { WorkflowReadonlyPreview } from './workflow/WorkflowReadonlyPreview'
import type { AssistantExecutableTarget } from './skillTargetOptions'
import type { FolderMoveOption } from './AssistantFolderCard'
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
    onMove: (folderId: string | null) => void
    isCopying: boolean
    disableCopy: boolean
    isDeleting: boolean
    disableDelete: boolean
    disableMove?: boolean
    isDetailLoading?: boolean
    pathLabel?: string
    moveOptions?: FolderMoveOption[]
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
    onMove,
    isCopying,
    disableCopy,
    isDeleting,
    disableDelete,
    disableMove = false,
    isDetailLoading = false,
    pathLabel,
    moveOptions = [],
}: AssistantTargetCardProps) {
    const { t } = useTranslation()
    const [moveTarget, setMoveTarget] = useState<string | null>(target.folderId ?? null)
    useEffect(() => {
        setMoveTarget(target.folderId ?? null)
    }, [target.folderId])
    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        isDragging,
    } = useDraggable({
        id: `${target.type}:${target.id}`,
        data: { kind: 'target', targetType: target.type, targetId: target.id },
    })
    const isWorkflow = target.type === 'workflow'
    const agentTools = Array.isArray(agent?.tools)
        ? agent.tools.map((item: string) => String(item)).filter(Boolean)
        : []
    const style: CSSProperties = {
        transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined,
        opacity: isDragging ? 0.62 : undefined,
    }

    return (
        <div
            ref={setNodeRef}
            style={style}
            className={cn(
                uiChrome.card,
                "group overflow-hidden p-4 transition-all duration-200 ease-in-out",
                isExpanded
                    ? "border-primary/25 ring-1 ring-primary/10"
                    : "hover:border-primary/20",
                isDragging && "z-20 shadow-xl"
            )}
        >
            <div
                role="button"
                tabIndex={0}
                onClick={onToggleExpand}
                className="flex cursor-pointer items-start gap-4"
            >
                <button
                    type="button"
                    {...listeners}
                    {...attributes}
                    className="mt-2 inline-flex h-8 w-5 cursor-grab items-center justify-center rounded-full text-muted-foreground/55 transition hover:bg-muted hover:text-foreground active:cursor-grabbing"
                    aria-label={t('settings.skills.folderDragHandle')}
                    onClick={(e) => e.stopPropagation()}
                >
                    <GripVertical className="h-4 w-4" />
                </button>
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
                                {!workflow?.updatedAt && agent?.updatedAt && (
                                    <div className="flex items-center gap-1">
                                        <Clock className="h-3.5 w-3.5" />
                                        <span>{new Date(agent.updatedAt).toLocaleDateString()}</span>
                                    </div>
                                )}
                                {pathLabel ? (
                                    <span className="truncate">{pathLabel}</span>
                                ) : null}
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

                            <Popover>
                                <PopoverTrigger asChild>
                                    <Button
                                        type="button"
                                        onClick={(e) => e.stopPropagation()}
                                        disabled={disableMove}
                                        variant="ghost"
                                        size="icon"
                                        title={t('settings.skills.moveToFolder')}
                                    >
                                        <MoveRight className="h-4 w-4" />
                                    </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-80 space-y-3" onClick={(e) => e.stopPropagation()}>
                                    <div className="space-y-1">
                                        <p className="text-sm font-medium">{t('settings.skills.moveToFolder')}</p>
                                        <p className="text-xs text-muted-foreground">{t('settings.skills.moveTargetDescription')}</p>
                                    </div>
                                    <select
                                        className={uiField.select}
                                        value={moveTarget ?? '__root__'}
                                        onChange={(event) => setMoveTarget(event.target.value === '__root__' ? null : event.target.value)}
                                    >
                                        {moveOptions.map((option) => (
                                            <option key={option.id ?? '__root__'} value={option.id ?? '__root__'} disabled={option.disabled}>
                                                {option.label}
                                            </option>
                                        ))}
                                    </select>
                                    <Button type="button" className="w-full" onClick={() => onMove(moveTarget)}>
                                        {t('settings.skills.move')}
                                    </Button>
                                </PopoverContent>
                            </Popover>

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
                    {isExpanded ? (
                        <div className="mt-4 border-t border-border/70 pt-4">
                            <div className="space-y-4">
                                {isWorkflow ? (
                                    isDetailLoading ? (
                                        <div className="flex items-center justify-center py-10 text-muted-foreground">
                                            <Loader2 className="h-5 w-5 animate-spin" />
                                        </div>
                                    ) : workflow ? (
                                        <div className="space-y-4">
                                            {workflow.description && (
                                                <SettingsInset className="text-sm leading-6 text-muted-foreground">
                                                    {workflow.description}
                                                </SettingsInset>
                                            )}
                                            <div className="overflow-hidden rounded-[20px] border border-border/70 bg-muted/20">
                                                <WorkflowReadonlyPreview
                                                    key={`workflow-preview:${workflow.id}:${workflow.updatedAt}:${workflow.workflowVersion}`}
                                                    workflow={workflow}
                                                    onOpenEditor={onEdit}
                                                />
                                            </div>
                                        </div>
                                    ) : (
                                        <p className="text-sm text-muted-foreground">{t('messages.noData')}</p>
                                    )
                                ) : (
                                    isDetailLoading ? (
                                        <div className="flex items-center justify-center py-10 text-muted-foreground">
                                            <Loader2 className="h-5 w-5 animate-spin" />
                                        </div>
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
                                    )
                                )}
                            </div>
                        </div>
                    ) : null}
                </div>
            </div>
        </div>
    )
})
