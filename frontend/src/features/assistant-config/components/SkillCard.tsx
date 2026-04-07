import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import {
    Bot,
    ChevronDown,
    ChevronRight,
    Pencil,
    Power,
    Trash2,
    Workflow,
    Zap
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { uiChrome } from '@/components/ui/styles'
import { SettingsBadge, SettingsInset } from '@/features/settings/components/SettingsShell'
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
                uiChrome.card,
                "group overflow-hidden p-4 transition-all duration-200 ease-in-out",
                skill.enabled
                    ? "border-primary/20 bg-primary/[0.03]"
                    : "hover:border-primary/20",
                isExpanded && "border-primary/25 ring-1 ring-primary/10"
            )}
        >
            <div className="flex items-start gap-4">
                <button
                    type="button"
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
                        uiChrome.control,
                        "flex h-11 w-11 shrink-0 items-center justify-center border transition-colors shadow-none",
                        skill.enabled
                            ? "border-primary/15 bg-primary/10 text-primary"
                            : "text-muted-foreground hover:bg-muted/60",
                        isGeneralChat && "opacity-50 cursor-not-allowed",
                        isToggling && "animate-pulse"
                    )}
                >
                    <Power className="w-5 h-5" />
                </button>

                <div
                    role="button"
                    tabIndex={0}
                    className="min-w-0 flex-1 cursor-pointer space-y-3"
                    onClick={onToggleExpand}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            onToggleExpand()
                        }
                    }}
                >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                                <h3 className="truncate text-base font-semibold text-foreground">
                                    {skill.name}
                                </h3>
                                <SettingsBadge className="capitalize">
                                    {skill.isSystem ? t('settings.skills.system') : t('settings.skills.custom')}
                                </SettingsBadge>
                                <SettingsBadge className="gap-1">
                                    {skill.targetType === 'workflow' ? (
                                        <Workflow className="h-3.5 w-3.5" />
                                    ) : (
                                        <Bot className="h-3.5 w-3.5" />
                                    )}
                                    {targetLabel}
                                </SettingsBadge>
                            </div>

                            <p className="line-clamp-2 text-sm leading-6 text-muted-foreground">
                                {skill.description}
                            </p>

                            {!isExpanded ? (
                                <p className="text-xs text-muted-foreground">
                                    {t('settings.skills.boundTarget')}: {targetName}
                                </p>
                            ) : null}
                        </div>
                        <div className="flex items-center gap-2 self-start">
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onEdit()
                                }}
                                title={t('common.edit')}
                            >
                                <Pencil className="h-4 w-4" />
                            </Button>

                            {!skill.isSystem ? (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    onClick={(e) => {
                                        e.stopPropagation()
                                        onDelete()
                                    }}
                                    title={t('common.delete')}
                                    className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            ) : null}

                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                onClick={(e) => {
                                    e.stopPropagation()
                                    onToggleExpand()
                                }}
                                title={t(isExpanded ? 'actions.collapse' : 'actions.expand')}
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
                        <div className="grid gap-4 md:grid-cols-2">
                            <SettingsInset className="space-y-3">
                                <label className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                                    <Zap className="w-3 h-3" />
                                    {t('settings.skills.boundTarget')}
                                </label>
                                <div className={cn(uiChrome.control, "flex min-h-12 items-center justify-between gap-3 px-3 py-3 shadow-none")}>
                                    <div className="min-w-0 flex items-center gap-2">
                                        {skill.targetType === 'workflow' ? (
                                            <div className="rounded-full bg-primary/10 p-1.5 text-primary">
                                                <Workflow className="w-3.5 h-3.5" />
                                            </div>
                                        ) : (
                                            <div className="rounded-full bg-primary/10 p-1.5 text-primary">
                                                <Bot className="w-3.5 h-3.5" />
                                            </div>
                                        )}
                                        <span className="truncate font-medium">{targetName}</span>
                                    </div>
                                    <span className="shrink-0 text-xs text-muted-foreground">({targetLabel})</span>
                                </div>
                            </SettingsInset>

                            {skill.intentExamples && skill.intentExamples.length > 0 ? (
                                <SettingsInset className="space-y-3">
                                    <label className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                                        {t('settings.skills.intentExamples')}
                                    </label>
                                    <div className="flex min-h-[52px] flex-wrap gap-2">
                                        {skill.intentExamples.map((ex, i) => (
                                            <span
                                                key={i}
                                                className="inline-flex items-center rounded-full border border-border/70 bg-background/92 px-3 py-1.5 text-xs text-foreground"
                                            >
                                                {ex}
                                            </span>
                                        ))}
                                    </div>
                                </SettingsInset>
                            ) : null}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
})
