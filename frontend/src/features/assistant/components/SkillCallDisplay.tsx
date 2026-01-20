import { Loader2, CheckCircle, XCircle, BrainCircuit, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { SkillCall } from '../types'

interface SkillCallDisplayProps {
  skillCalls: SkillCall[]
  variant?: 'default' | 'compact'
}

const SKILL_KEYS: Record<string, string> = {
  search_entries: 'searchEntries',
  get_entry_detail: 'getEntryDetail',
  quick_stats: 'quickStats',
  list_types: 'listTypes',
  list_tags: 'listTags',
  smart_capture: 'smartCapture',
  periodic_review: 'periodicReview',
  knowledge_synthesis: 'knowledgeSynthesis',
  general_chat: 'generalChat',
}

export function SkillCallDisplay({ skillCalls, variant = 'default' }: SkillCallDisplayProps) {
  const { t } = useTranslation()

  // 过滤掉 hidden 的 skillCalls（默认 skill 不显示）
  const visibleSkillCalls = (skillCalls || []).filter((sc) => !sc.hidden && sc.name !== 'general_chat')
  if (visibleSkillCalls.length === 0) return null

  const getSkillLabel = (name: string) => {
    const key = SKILL_KEYS[name]
    return key ? t(`pages.assistant.skills.${key}`) : name
  }

  return (
    <div className={cn("flex flex-col w-full max-w-full", variant === 'compact' ? "my-1.5 gap-1.5" : "my-3 gap-2")}>
      {visibleSkillCalls.map((sc) => (
        <SkillCallItem
          key={sc.id}
          skillCall={sc}
          label={getSkillLabel(sc.name)}
          variant={variant}
        />
      ))}
    </div>
  )
}

interface SkillCallItemProps {
  skillCall: SkillCall
  label: string
  variant: 'default' | 'compact'
}

function SkillCallItem({ skillCall, label, variant }: SkillCallItemProps) {
  const { t } = useTranslation()
  const isRunning = skillCall.status === 'running'
  const isCompact = variant === 'compact'

  return (
    <div
      className={cn(
        "group relative flex items-center w-full transition-all duration-300 overflow-hidden",
        "bg-gradient-to-r from-purple-500/5 via-purple-500/5 to-transparent dark:from-purple-500/10 dark:via-purple-500/5",
        "hover:from-purple-500/10 dark:hover:from-purple-500/15",
        "border-purple-500/70",
        isCompact
          ? "gap-2 px-2 py-1.5 rounded-sm border-l-2"
          : "gap-3 px-3 py-2.5 rounded-r-md border-l-4"
      )}
    >
      <div className={cn(
        "relative flex items-center justify-center rounded-md bg-purple-500/10 text-purple-600 dark:text-purple-400 shrink-0",
        isCompact ? "p-1" : "p-1.5",
        isRunning && "animate-pulse"
      )}>
        <BrainCircuit className={cn(isCompact ? "h-3 w-3" : "h-4 w-4")} aria-hidden="true" />
        {isRunning && (
          <span className={cn("absolute flex h-2 w-2", isCompact ? "-top-0.5 -right-0.5" : "-top-0.5 -right-0.5")}>
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-purple-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-purple-500"></span>
          </span>
        )}
      </div>

      <div className="flex flex-col flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={cn("font-semibold text-purple-700 dark:text-purple-300 truncate", isCompact ? "text-xs" : "text-sm")}>
            {t('pages.assistant.skillDerived', 'Thinking Process')}
          </span>
          <div className="h-px bg-purple-200 dark:bg-purple-800 flex-1 opacity-50" />
        </div>
        <span className={cn("text-muted-foreground truncate font-medium mt-0.5", isCompact ? "text-[10px]" : "text-xs")}>
          {label}
        </span>
      </div>

      <StatusIcon status={skillCall.status} t={t} variant={variant} />

      {/* Decorative background glow */}
      <div className="absolute inset-0 bg-purple-500/5 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
    </div>
  )
}

function StatusIcon({ status, t, variant }: { status: SkillCall['status']; t: (key: string) => string, variant: 'default' | 'compact' }) {
  const isCompact = variant === 'compact'
  const iconClass = isCompact ? "h-3 w-3" : "h-3.5 w-3.5" // Matching the check/x circle size slightly smaller or same

  switch (status) {
    case 'running':
      return (
        <div className={cn("flex items-center gap-1.5 font-medium text-purple-600 dark:text-purple-400 animate-pulse", isCompact ? "text-[10px]" : "text-xs")}>
          <Loader2 className={cn("animate-spin", iconClass)} />
        </div>
      )
    case 'completed':
      return <CheckCircle className={cn("text-purple-600/70 dark:text-purple-400/70", iconClass)} aria-label={t('pages.assistant.toolStatus.completed')} />
    case 'error':
      return <XCircle className={cn("text-destructive", iconClass)} aria-label={t('pages.assistant.toolStatus.error')} />
    default:
      return null
  }
}
