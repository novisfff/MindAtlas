import { useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, CheckCircle, XCircle, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { ToolCall } from '../types'

interface ToolCallDisplayProps {
  toolCalls: ToolCall[]
  variant?: 'default' | 'compact'
}

const TOOL_KEYS: Record<string, string> = {
  search_entries: 'searchEntries',
  get_entry_detail: 'getEntryDetail',
  create_entry: 'createEntry',
  get_statistics: 'getStatistics',
  get_entries_by_time_range: 'getEntriesByTimeRange',
  analyze_activity: 'analyzeActivity',
  list_entry_types: 'listEntryTypes',
  list_tags: 'listTags',
}

export function ToolCallDisplay({ toolCalls, variant = 'default' }: ToolCallDisplayProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const { t } = useTranslation()

  if (!toolCalls || toolCalls.length === 0) return null
  const visibleToolCalls = toolCalls.filter((tc) => !tc.hidden)
  if (visibleToolCalls.length === 0) return null

  const toggleExpand = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const getToolLabel = (name: string) => {
    const key = TOOL_KEYS[name]
    return key ? t(`pages.assistant.tools.${key}`) : name
  }

  return (
    <div className={cn("flex flex-col w-full max-w-full", variant === 'compact' ? "my-1.5 gap-1.5" : "my-3 gap-2")}>
      {visibleToolCalls.map((tc) => (
        <ToolCallItem
          key={tc.id}
          toolCall={tc}
          label={getToolLabel(tc.name)}
          isExpanded={expanded[tc.id] || false}
          onToggle={() => toggleExpand(tc.id)}
          variant={variant}
        />
      ))}
    </div>
  )
}

interface ToolCallItemProps {
  toolCall: ToolCall
  label: string
  isExpanded: boolean
  onToggle: () => void
  variant: 'default' | 'compact'
}

function ToolCallItem({ toolCall, label, isExpanded, onToggle, variant }: ToolCallItemProps) {
  const { t } = useTranslation()
  const isCompact = variant === 'compact'
  const isError = toolCall.status === 'error'
  const isRunning = toolCall.status === 'running'

  // Dynamic colors based on status (Green/Blue vs Red)
  const themeColor = isError ? "red" : "blue"

  // Construct class names dynamically to avoid massive duplication
  const containerClasses = cn(
    "group relative flex flex-col w-full transition-all duration-300 overflow-hidden",
    isError
      ? "bg-gradient-to-r from-red-500/5 via-red-500/5 to-transparent dark:from-red-500/10 dark:via-red-500/5 border-red-500/70"
      : "bg-gradient-to-r from-blue-500/5 via-blue-500/5 to-transparent dark:from-blue-500/10 dark:via-blue-500/5 border-blue-500/70",
    isError
      ? "hover:from-red-500/10 dark:hover:from-red-500/15"
      : "hover:from-blue-500/10 dark:hover:from-blue-500/15",
    isCompact
      ? "rounded-sm border-l-2"
      : "rounded-r-md border-l-4"
  )

  const iconBoxClasses = cn(
    "relative flex items-center justify-center rounded-md shrink-0",
    isError ? "bg-red-500/10 text-red-600 dark:text-red-400" : "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    isCompact ? "p-1" : "p-1.5",
    isRunning && "animate-pulse"
  )

  const titleClasses = cn(
    "font-semibold truncate",
    isError ? "text-red-700 dark:text-red-300" : "text-blue-700 dark:text-blue-300",
    isCompact ? "text-xs" : "text-sm"
  )

  return (
    <div className={containerClasses}>
      <button
        onClick={onToggle}
        className={cn(
          "flex w-full items-center text-left relative z-10",
          isCompact ? "gap-2 px-2 py-1.5" : "gap-3 px-3 py-2" // Reduced vertical padding
        )}
      >
        {/* Left Icon Box */}
        <div className={iconBoxClasses}>
          <Wrench className={cn(isCompact ? "h-3.5 w-3.5" : "h-4 w-4")} aria-hidden="true" />
          {isRunning && (
            <span className={cn("absolute flex h-2 w-2", isCompact ? "-top-0.5 -right-0.5" : "-top-0.5 -right-0.5")}>
              <span className={cn("animate-ping absolute inline-flex h-full w-full rounded-full opacity-75", isError ? "bg-red-400" : "bg-blue-400")}></span>
              <span className={cn("relative inline-flex rounded-full h-2 w-2", isError ? "bg-red-500" : "bg-blue-500")}></span>
            </span>
          )}
        </div>

        {/* Content Info - Single Line */}
        <div className="flex flex-1 min-w-0 items-center">
          <span className={cn(
            "truncate font-medium",
            isError ? "text-red-900/90 dark:text-red-200/90" : "text-blue-900/90 dark:text-blue-200/90",
            isCompact ? "text-xs" : "text-sm"
          )}>
            {label}
          </span>
        </div>

        {/* Right Status & Expand */}
        <div className="flex items-center gap-1.5 ml-2 shrink-0">
          <StatusIcon status={toolCall.status} variant={variant} isError={isError} />
          <div className={cn("transition-transform duration-200", isExpanded && "rotate-180", isError ? "text-red-500/70" : "text-blue-500/70")}>
            <ChevronDown className={cn(isCompact ? "h-3.5 w-3.5" : "h-4 w-4")} />
          </div>
        </div>
      </button>

      {/* Expanded Details */}
      {isExpanded && (
        <div className={cn(
          "relative z-10 animate-in slide-in-from-top-1 fade-in duration-200 border-t",
          isError ? "border-red-500/10" : "border-blue-500/10",
          isCompact ? "px-2 py-1.5 mx-2 mb-1.5 mt-0" : "px-3 py-2 mx-3 mb-2 mt-0"
        )}>
          <div className="w-full min-w-0 grid grid-cols-1 gap-2">
            <div className="grid grid-cols-1">
              <span className={cn("font-medium mb-1", isError ? "text-red-700/70 dark:text-red-300/70" : "text-blue-700/70 dark:text-blue-300/70", isCompact ? "text-[10px]" : "text-xs")}>
                {t('pages.assistant.params')}:
              </span>
              <div className="w-full overflow-hidden">
                <pre
                  className={cn(
                    "overflow-x-auto rounded-md bg-black/5 dark:bg-black/20 p-2 text-foreground/90 font-mono custom-scrollbar border",
                    isError ? "border-red-500/10" : "border-blue-500/10",
                    isCompact ? "text-[10px]" : "text-xs"
                  )}
                  style={{ maxWidth: 'calc(100vw - 120px)' }}
                >
                  {JSON.stringify(toolCall.args, null, 2)}
                </pre>
              </div>
            </div>
            {toolCall.result && (
              <div className="grid grid-cols-1">
                <span className={cn("font-medium mb-1", isError ? "text-red-700/70 dark:text-red-300/70" : "text-blue-700/70 dark:text-blue-300/70", isCompact ? "text-[10px]" : "text-xs")}>
                  {t('pages.assistant.result')}:
                </span>
                <div className="w-full overflow-hidden">
                  <pre
                    className={cn(
                      "max-h-60 overflow-x-auto rounded-md bg-black/5 dark:bg-black/20 p-2 text-foreground/90 font-mono custom-scrollbar border",
                      isError ? "border-red-500/10" : "border-blue-500/10",
                      isCompact ? "text-[10px]" : "text-xs"
                    )}
                    style={{ maxWidth: 'calc(100vw - 120px)' }}
                  >
                    {toolCall.result}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Decorative background glow */}
      <div className={cn(
        "absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none",
        isError ? "bg-red-500/5" : "bg-blue-500/5"
      )} />
    </div>
  )
}

function StatusIcon({ status, variant, isError }: { status: ToolCall['status'], variant: 'default' | 'compact', isError: boolean }) {
  const isCompact = variant === 'compact'
  const iconClass = isCompact ? "h-3 w-3" : "h-3.5 w-3.5"

  switch (status) {
    case 'pending':
      return <span className={cn("rounded-full", isError ? "bg-red-500/30" : "bg-blue-500/30", isCompact ? "h-1.5 w-1.5" : "h-2 w-2")} />
    case 'running':
      return (
        <div className={cn("flex items-center animate-pulse", isError ? "text-red-600" : "text-blue-600")}>
          <Loader2 className={cn("animate-spin", iconClass)} />
        </div>
      )
    case 'completed':
      return <CheckCircle className={cn(isError ? "text-red-600/70" : "text-blue-600/70", iconClass)} />
    case 'error':
      return <XCircle className={cn("text-destructive", iconClass)} />
  }
}
