import { useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, CheckCircle, XCircle, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { ToolCall } from '../types'

interface ToolCallDisplayProps {
  toolCalls: ToolCall[]
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

export function ToolCallDisplay({ toolCalls }: ToolCallDisplayProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const { t } = useTranslation()

  if (!toolCalls || toolCalls.length === 0) return null

  const toggleExpand = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const getToolLabel = (name: string) => {
    const key = TOOL_KEYS[name]
    return key ? t(`pages.assistant.tools.${key}`) : name
  }

  return (
    <div className="my-2 space-y-2">
      {toolCalls.map((tc) => (
        <ToolCallItem
          key={tc.id}
          toolCall={tc}
          label={getToolLabel(tc.name)}
          isExpanded={expanded[tc.id] || false}
          onToggle={() => toggleExpand(tc.id)}
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
}

function ToolCallItem({ toolCall, label, isExpanded, onToggle }: ToolCallItemProps) {
  const { t } = useTranslation()

  return (
    <div className="rounded-md border bg-muted/30 text-sm overflow-hidden w-full max-w-full">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2 hover:bg-muted/50"
      >
        {isExpanded ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        <Wrench className="h-4 w-4 text-muted-foreground" />
        <span className="flex-1 text-left font-medium truncate">{label}</span>
        <StatusIcon status={toolCall.status} />
      </button>

      {isExpanded && (
        <div className="border-t px-3 py-2 space-y-2 animate-in slide-in-from-top-1 fade-in duration-200">
          <div className="w-full min-w-0 grid grid-cols-1">
            <span className="text-xs text-muted-foreground font-medium mb-1">{t('pages.assistant.params')}:</span>
            <div className="w-full overflow-hidden">
              <pre
                className="overflow-x-auto rounded-md bg-zinc-950/5 dark:bg-white/10 p-2 text-xs text-foreground/90 font-mono custom-scrollbar border border-border/50"
                style={{ maxWidth: 'calc(100vw - 120px)' }} // Fallback for floating widget
              >
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          </div>
          {toolCall.result && (
            <div className="w-full min-w-0 grid grid-cols-1">
              <span className="text-xs text-muted-foreground font-medium mb-1">{t('pages.assistant.result')}:</span>
              <div className="w-full overflow-hidden">
                <pre
                  className="max-h-60 overflow-x-auto rounded-md bg-zinc-950/5 dark:bg-white/10 p-2 text-xs text-foreground/90 font-mono custom-scrollbar border border-border/50"
                  style={{ maxWidth: 'calc(100vw - 120px)' }}
                >
                  {toolCall.result}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StatusIcon({ status }: { status: ToolCall['status'] }) {
  switch (status) {
    case 'pending':
      return <span className="h-2 w-2 rounded-full bg-muted-foreground" />
    case 'running':
      return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
    case 'completed':
      return <CheckCircle className="h-4 w-4 text-green-500" />
    case 'error':
      return <XCircle className="h-4 w-4 text-destructive" />
  }
}
