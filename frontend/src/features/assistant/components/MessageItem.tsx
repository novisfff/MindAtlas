import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { remarkCitation } from './remark-citation'
import { Bot, User, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { ToolCall, SkillCall, Analysis, HumanApproval } from '../types'
import { listPendingApprovals, submitApprovalDecision } from '../api'
import { assistantKeys } from '../queries'
import { useChatStore } from '../stores/chat-store'
import { ToolCallDisplay } from './ToolCallDisplay'
import { SkillCallDisplay } from './SkillCallDisplay'
import { AnalysisDisplay } from './AnalysisDisplay'
import { CitationProvider, CitationMarker, ReferenceList } from './citation'
import { HumanApprovalCard } from '@/features/shared/hitl'

interface MessageItemProps {
  message: {
    id: string
    role: 'user' | 'assistant'
    content: string
    toolCalls?: ToolCall[]
    skillCalls?: SkillCall[]
    analysisSteps?: Analysis[]
    humanApprovals?: HumanApproval[]
    createdAt: number
  }
  variant?: 'default' | 'compact'
  isStreaming?: boolean
}

const truncateHintText = (value: string, maxChars: number) => {
  const text = String(value || '').trim()
  if (!text) return ''
  if (text.length <= maxChars) return text
  return `${text.slice(0, Math.max(1, maxChars - 1)).trimEnd()}…`
}

export function MessageItem({ message, variant = 'default', isStreaming }: MessageItemProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { currentConversationId, upsertHumanApproval, setConversationPendingApprovals, activeWorkflowSteps } = useChatStore((state) => ({
    currentConversationId: state.currentConversationId,
    upsertHumanApproval: state.upsertHumanApproval,
    setConversationPendingApprovals: state.setConversationPendingApprovals,
    activeWorkflowSteps: state.activeWorkflowSteps,
  }))
  const [submittingApprovalId, setSubmittingApprovalId] = useState<string | null>(null)
  const isUser = message.role === 'user'
  const isCompact = variant === 'compact'

  // 检测正在运行的隐藏工具（KB 搜索）
  const activeHiddenTools = message.toolCalls?.filter(
    tc => tc.hidden && tc.status === 'running'
  )

  // 显示加载状态的条件：
  // 1. 有正在运行的隐藏工具（KB 搜索中）
  // 2. 流式输出中 + 内容为空（等待 AI 生成）
  const showKbSearching = activeHiddenTools && activeHiddenTools.length > 0
  const showGenerating = isStreaming && !message.content && !showKbSearching
  const runningStepLabels = activeWorkflowSteps
    .map((step) => (step.nodeLabel || step.nodeId || step.nodeType || '').trim())
    .filter(Boolean)
  const rawGeneratingHint = runningStepLabels.length === 0
    ? t('pages.assistant.generatingResponse', 'Generating response...')
    : runningStepLabels.length === 1
      ? t('pages.assistant.workflowRunning', { step: runningStepLabels[0] })
      : t('pages.assistant.workflowRunningParallel', { count: runningStepLabels.length })
  const maxHintChars = isCompact ? 24 : 38
  const generatingHint = truncateHintText(rawGeneratingHint, maxHintChars)

  const handleSubmitApproval = async (
    approvalId: string,
    payload: { decision: 'approved' | 'rejected'; values: Record<string, unknown>; comment?: string },
  ) => {
    if (!currentConversationId) {
      toast.error(t('settings.skills.humanApproval.noConversation'))
      return
    }
    setSubmittingApprovalId(approvalId)
    try {
      const approval = await submitApprovalDecision(currentConversationId, approvalId, payload)
      upsertHumanApproval(approval)
      try {
        const pending = await listPendingApprovals(currentConversationId)
        setConversationPendingApprovals(pending)
      } catch {
        setConversationPendingApprovals([])
      }
      queryClient.invalidateQueries({ queryKey: [...assistantKeys.conversations(), currentConversationId] })
      queryClient.invalidateQueries({ queryKey: assistantKeys.conversations() })
      toast.success(t('settings.skills.humanApproval.submitted'))
    } catch (error) {
      const message = error instanceof Error ? error.message : t('settings.skills.humanApproval.submitFailed')
      toast.error(message)
    } finally {
      setSubmittingApprovalId(null)
    }
  }

  return (
    <div className={cn(
      'flex w-full',
      isCompact ? 'gap-2 px-3 py-3' : 'gap-4 px-4 py-6',
      isUser ? 'flex-row-reverse' : 'flex-row'
    )}>
      <div className={cn(
        'flex shrink-0 items-center justify-center rounded-full border shadow-sm',
        isCompact ? 'h-7 w-7' : 'h-10 w-10',
        isUser ? 'bg-background' : 'bg-primary/10 border-primary/20 text-primary'
      )}>
        {isUser ? <User className={cn(isCompact ? "h-4 w-4" : "h-6 w-6")} /> : <Bot className={cn(isCompact ? "h-4 w-4" : "h-6 w-6")} />}
      </div>

      <div className={cn(
        "flex flex-col max-w-[85%]",
        isUser ? "items-end" : "items-start"
      )}>
        <div className="flex items-center gap-2 mb-1.5 px-1">
          <span className="text-xs font-medium text-muted-foreground group-hover:text-foreground transition-colors">
            {isUser ? t('pages.assistant.you') : t('pages.assistant.assistant')}
          </span>
          <span className="text-[10px] text-muted-foreground/60">
            {new Date(message.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>

        <div className={cn(
          "prose prose-sm dark:prose-invert max-w-none break-words shadow-sm transition-all",
          isCompact ? "rounded-xl px-3 py-2 text-sm" : "rounded-2xl px-5 py-3.5",
          isUser
            ? "bg-primary text-primary-foreground rounded-tr-sm"
            : "bg-muted/50 border border-border/50 rounded-tl-sm hover:bg-muted/80 hover:shadow-md"
        )}>
          {message.skillCalls && message.skillCalls.length > 0 && (
            <SkillCallDisplay skillCalls={message.skillCalls} variant={variant} />
          )}
          {message.toolCalls && message.toolCalls.length > 0 && (
            <ToolCallDisplay toolCalls={message.toolCalls!} variant={variant} />
          )}
          {message.analysisSteps && message.analysisSteps.length > 0 && (
            <AnalysisDisplay steps={message.analysisSteps} />
          )}
          {message.humanApprovals && message.humanApprovals.length > 0 && (
            <div className="my-2 space-y-2">
              {message.humanApprovals.map((approval) => (
                <HumanApprovalCard
                  key={approval.id}
                  approval={approval}
                  submitting={submittingApprovalId === approval.id}
                  onSubmit={(payload) => handleSubmitApproval(approval.id, payload)}
                />
              ))}
            </div>
          )}
          {/* KB 搜索状态提示 */}
          {showKbSearching && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground my-2 animate-pulse px-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>{t('pages.assistant.searchingKnowledgeBase', 'Searching knowledge base...')}</span>
            </div>
          )}
          {/* 等待 AI 生成回复状态 */}
          {showGenerating && (
            <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground my-2 animate-pulse px-1">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span className="min-w-0 flex-1 truncate">{generatingHint}</span>
            </div>
          )}
          <CitationProvider content={message.content || ''} toolCalls={message.toolCalls}>
            {message.content && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkCitation]}
                components={{
                  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
                  code: ({ node, inline, className, children, ...props }: any) => {
                    return inline ? (
                      <code className="bg-black/10 dark:bg-white/10 px-1 py-0.5 rounded font-mono text-sm" {...props}>{children}</code>
                    ) : (
                      <code className="block bg-black/10 dark:bg-white/10 p-3 rounded-lg font-mono text-sm overflow-x-auto my-2" {...props}>{children}</code>
                    )
                  },
                  // Handle our custom citation-marker node
                  // @ts-ignore - Custom element type from remark-citation
                  'citation-marker': ({ identifier }: { identifier: string }) => {
                    return <CitationMarker identifier={identifier} label={identifier} />
                  },
                  // Fallback for regular superscripts or if plugin fails
                  sup: ({ children, ...props }) => {
                    return <sup {...props}>{children}</sup>
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            )}
            <ReferenceList content={message.content} isStreaming={isStreaming} />
          </CitationProvider>
        </div>
      </div>
    </div>
  )
}
